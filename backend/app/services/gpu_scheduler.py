"""
LabOps GPU Scheduler V2 — Scoped Idempotency, TTL Expiry, NVML Verification & Reconciliation.

Nguyên tắc:
- GPU được định danh bằng UUID (không dùng label hiển thị).
- Chống cấp phát trùng bằng khóa file (fcntl) + scoped actor:idempotency_key + payload hash.
- Trước khi giữ GPU, đối chiếu NVML (nvidia-smi) để phát hiện process ngoài;
  nếu có process ngoài hoặc không đọc được NVML -> từ chối (fail-closed).
- Scheduler KHÔNG giả lập việc chạy job: reservation chỉ chuyển RUNNING khi runtime thực
  xác minh được PID qua NVML hoặc qua báo cáo Node Agent.
- Tự động reconcile hết hạn TTL (RESERVED -> EXPIRED).
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from app.db.atomic_json import atomic_write_json, safe_load_json

ACTIVE_STATES = {"RESERVED", "STARTING", "RUNNING", "STOP_REQUESTED"}
ALLOWED_MODES = {"FULL_GPU_EXCLUSIVE"}
DEFAULT_RESERVATION_TTL_MINUTES = 240
MAX_RESERVATION_TTL_MINUTES = 48 * 60


class SchedulerError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _hash_reserve_payload(
    *,
    gpu_uuid: str,
    owner: str,
    job_name: str,
    mode: str,
    requested_vram_gb: int,
    ttl_minutes: int,
) -> str:
    canonical = json.dumps(
        {
            "gpu_uuid": gpu_uuid,
            "owner": owner,
            "job_name": job_name,
            "mode": mode,
            "requested_vram_gb": int(requested_vram_gb),
            "ttl_minutes": int(ttl_minutes),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class GpuScheduler:
    def __init__(self, state_path: str, telemetry_fn: Callable[[], List[Dict[str, Any]]]):
        self.state_path = state_path
        self.lock_path = state_path + ".lock"
        self.telemetry_fn = telemetry_fn
        os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)

    @contextmanager
    def _locked(self):
        with open(self.lock_path, "a+", encoding="utf-8") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                state = self._load()
                yield state
                self._save(state)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def _load(self) -> Dict[str, Any]:
        return safe_load_json(self.state_path, lambda: {"reservations": [], "idempotency": {}})

    def _save(self, state: Dict[str, Any]) -> None:
        atomic_write_json(self.state_path, state, use_lock=False)

    def _reconcile_state_dict(self, state: Dict[str, Any], telemetry: Optional[List[Dict[str, Any]]] = None) -> bool:
        changed = False
        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()

        gpu_procs_by_uuid: Dict[str, List[Dict[str, Any]]] = {}
        if telemetry:
            for g in telemetry:
                uid = g.get("uuid")
                if uid:
                    gpu_procs_by_uuid[uid] = list(g.get("active_processes") or [])

        for r in state.get("reservations", []):
            st = r.get("state")
            if st not in ACTIVE_STATES:
                continue

            exp_dt = _parse_iso(r.get("expires_at"))
            if exp_dt is not None and now_dt > exp_dt and st == "RESERVED":
                r["state"] = "EXPIRED"
                r["ended_at"] = now_iso
                changed = True
                continue

            if telemetry is not None and r.get("gpu_uuid") in gpu_procs_by_uuid:
                procs = gpu_procs_by_uuid[r["gpu_uuid"]]
                owner_procs = [p for p in procs if p.get("username") == r.get("owner")]
                if st == "RESERVED" and owner_procs:
                    r["state"] = "RUNNING"
                    r["runtime_verified"] = True
                    r["pid"] = owner_procs[0].get("pid")
                    r["started_at"] = r.get("started_at") or now_iso
                    changed = True
                elif st == "STOP_REQUESTED" and not owner_procs:
                    r["state"] = "RELEASED"
                    r["runtime_verified"] = True
                    r["ended_at"] = now_iso
                    changed = True

        return changed

    def reconcile(self, telemetry: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        if telemetry is None:
            try:
                telemetry = self.telemetry_fn()
            except Exception:
                telemetry = None
        with self._locked() as state:
            self._reconcile_state_dict(state, telemetry)
            return list(state.get("reservations", []))

    def list_reservations(self, owner: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._locked() as state:
            self._reconcile_state_dict(state, telemetry=None)
            res = list(state.get("reservations", []))
        return [r for r in res if owner is None or r.get("owner") == owner]

    def reserve(
        self,
        *,
        gpu_uuid: str,
        owner: str,
        actor: str,
        job_name: str,
        mode: str = "FULL_GPU_EXCLUSIVE",
        requested_vram_gb: int = 0,
        ttl_minutes: int = DEFAULT_RESERVATION_TTL_MINUTES,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        if mode not in ALLOWED_MODES:
            raise SchedulerError(
                "MODE_NOT_SUPPORTED",
                "V1 chỉ hỗ trợ chế độ FULL_GPU_EXCLUSIVE (độc quyền toàn bộ GPU).",
                400,
            )

        clamped_ttl = max(1, min(int(ttl_minutes or DEFAULT_RESERVATION_TTL_MINUTES), MAX_RESERVATION_TTL_MINUTES))
        req_vram = max(0, int(requested_vram_gb or 0))

        try:
            telemetry = self.telemetry_fn()
        except Exception as exc:
            raise SchedulerError("NVML_UNAVAILABLE", f"Không đọc được NVML: {exc}", 503)

        gpu = next((g for g in telemetry if g.get("uuid") == gpu_uuid), None)
        if gpu is None:
            raise SchedulerError(
                "GPU_NOT_OBSERVED",
                "Không quan sát được GPU này qua NVML; từ chối cấp phát.",
                503,
            )

        gpu_vram_gb = (int(gpu.get("vram_total_mb") or 24576)) / 1024.0
        if req_vram > gpu_vram_gb + 0.5:
            raise SchedulerError(
                "VRAM_QUOTA_EXCEEDED",
                f"Yêu cầu {req_vram}GB VRAM vượt quá dung lượng vật lý ({gpu_vram_gb:.1f}GB) của GPU.",
                400,
            )

        with self._locked() as state:
            self._reconcile_state_dict(state, telemetry=telemetry)

            payload_hash = _hash_reserve_payload(
                gpu_uuid=gpu_uuid,
                owner=owner,
                job_name=job_name,
                mode=mode,
                requested_vram_gb=req_vram,
                ttl_minutes=clamped_ttl,
            )

            if idempotency_key:
                scoped_key = f"{actor}:{idempotency_key.strip()}"
                idem_map = state.setdefault("idempotency", {})
                if scoped_key in idem_map:
                    entry = idem_map[scoped_key]
                    if isinstance(entry, dict):
                        if entry.get("payload_hash") != payload_hash:
                            raise SchedulerError(
                                "IDEMPOTENCY_CONFLICT",
                                "Idempotency-Key đã được sử dụng cho yêu cầu giữ GPU khác.",
                                409,
                            )
                        rid = entry.get("reservation_id")
                    else:
                        rid = entry
                    existing = next((r for r in state["reservations"] if r["id"] == rid), None)
                    if existing is not None:
                        cp = dict(existing)
                        cp["idempotent_replay"] = True
                        return cp

            if gpu.get("active_processes"):
                raise SchedulerError(
                    "GPU_BUSY_EXTERNAL",
                    "GPU đang có process ngoài hệ thống lịch; không thể giữ độc quyền.",
                )
            if any(r["gpu_uuid"] == gpu_uuid and r["state"] in ACTIVE_STATES for r in state["reservations"]):
                raise SchedulerError("GPU_ALREADY_RESERVED", "GPU đã được giữ bởi một reservation khác.")

            now_dt = datetime.now(timezone.utc)
            exp_dt = now_dt + timedelta(minutes=clamped_ttl)

            r = {
                "id": f"rsv-{uuid.uuid4().hex[:10]}",
                "gpu_uuid": gpu_uuid,
                "gpu_id": gpu.get("id"),
                "host_id": gpu.get("server_id"),
                "owner": owner,
                "requested_by": actor,
                "job_name": job_name,
                "mode": mode,
                "requested_vram_gb": req_vram,
                "ttl_minutes": clamped_ttl,
                "state": "RESERVED",
                "runtime_verified": False,
                "pid": None,
                "reserved_at": now_dt.isoformat(),
                "expires_at": exp_dt.isoformat(),
                "started_at": None,
                "ended_at": None,
                "idempotency_key": idempotency_key,
            }
            state["reservations"].append(r)
            if idempotency_key:
                state.setdefault("idempotency", {})[f"{actor}:{idempotency_key.strip()}"] = {
                    "reservation_id": r["id"],
                    "payload_hash": payload_hash,
                }
            return r

    def request_stop(self, reservation_id: str, actor: str, is_admin: bool) -> Dict[str, Any]:
        with self._locked() as state:
            r = next((x for x in state["reservations"] if x["id"] == reservation_id), None)
            if r is None:
                raise SchedulerError("NOT_FOUND", "Không tìm thấy reservation/tác vụ.", 404)
            if not is_admin and r["owner"] != actor:
                raise SchedulerError("FORBIDDEN", "Bạn không sở hữu tác vụ này.", 403)
            if r["state"] == "RESERVED":
                r["state"] = "RELEASED"
                r["ended_at"] = _now()
            elif r["state"] in ACTIVE_STATES:
                r["state"] = "STOP_REQUESTED"
            return r

    def confirm_from_agent(self, reservation_id: str, observed_state: str, pid: Optional[int]) -> Dict[str, Any]:
        """Node Agent báo cáo trạng thái quan sát được (đã xác thực machine token)."""
        with self._locked() as state:
            r = next((x for x in state["reservations"] if x["id"] == reservation_id), None)
            if r is None:
                raise SchedulerError("NOT_FOUND", "Không tìm thấy reservation.", 404)
            if observed_state == "RUNNING":
                r.update(state="RUNNING", pid=pid, runtime_verified=True, started_at=r["started_at"] or _now())
            elif observed_state in {"STOPPED", "EXITED"}:
                r.update(state="RELEASED", runtime_verified=True, ended_at=_now())
            elif observed_state == "FAILED":
                r.update(state="FAILED", ended_at=_now())
            return r

    @staticmethod
    def gpu_hours(r: Dict[str, Any]) -> Dict[str, Optional[float]]:
        """Tách thời gian giữ GPU (allocated) và thời gian chạy thực (runtime)."""
        def _h(a, b):
            if not a:
                return None
            end = datetime.fromisoformat(b) if b else datetime.now(timezone.utc)
            return round((end - datetime.fromisoformat(a)).total_seconds() / 3600, 4)

        allocated = _h(r["reserved_at"], r["ended_at"])
        runtime = _h(r["started_at"], r["ended_at"]) if r.get("runtime_verified") else None
        idle = round(allocated - runtime, 4) if allocated is not None and runtime is not None else None
        return {"allocated_hours": allocated, "runtime_hours": runtime, "idle_hold_hours": idle}
