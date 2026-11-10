"""
LabOps GPU Scheduler V1 — FULL_GPU_EXCLUSIVE only.

Nguyên tắc:
- GPU được định danh bằng UUID (không dùng label hiển thị).
- Chống cấp phát trùng bằng khóa file (fcntl) + kiểm tra trạng thái reservation
  trong cùng một critical section, kèm idempotency key.
- Trước khi giữ GPU, đối chiếu NVML (nvidia-smi) để phát hiện process ngoài;
  nếu có process ngoài hoặc không đọc được NVML -> từ chối (fail-closed).
- Scheduler KHÔNG giả lập việc chạy job. Reservation được tạo ở trạng thái
  RESERVED; job chỉ chuyển RUNNING khi Node Agent/Workspace Controller báo cáo
  và được xác minh. Không bao giờ sinh PID giả.
"""
from __future__ import annotations

import fcntl
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

ACTIVE_STATES = {"RESERVED", "STARTING", "RUNNING", "STOP_REQUESTED"}
ALLOWED_MODES = {"FULL_GPU_EXCLUSIVE"}


class SchedulerError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class GpuScheduler:
    def __init__(self, state_path: str, telemetry_fn: Callable[[], List[Dict[str, Any]]]):
        self.state_path = state_path
        self.lock_path = state_path + ".lock"
        self.telemetry_fn = telemetry_fn
        os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)

    # ---------- persistence ----------
    @contextmanager
    def _locked(self):
        with open(self.lock_path, "a+") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                state = self._load()
                yield state
                self._save(state)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def _load(self) -> Dict[str, Any]:
        if not os.path.exists(self.state_path):
            return {"reservations": [], "idempotency": {}}
        with open(self.state_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save(self, state: Dict[str, Any]) -> None:
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.state_path)

    # ---------- queries ----------
    def list_reservations(self, owner: Optional[str] = None) -> List[Dict[str, Any]]:
        res = self._load()["reservations"]
        return [r for r in res if owner is None or r["owner"] == owner]

    # ---------- commands ----------
    def reserve(self, *, gpu_uuid: str, owner: str, actor: str, job_name: str,
                mode: str = "FULL_GPU_EXCLUSIVE", idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        if mode not in ALLOWED_MODES:
            raise SchedulerError("MODE_NOT_SUPPORTED",
                                 "V1 chỉ hỗ trợ chế độ FULL_GPU_EXCLUSIVE (độc quyền toàn bộ GPU).", 400)
        with self._locked() as state:
            if idempotency_key and idempotency_key in state["idempotency"]:
                rid = state["idempotency"][idempotency_key]
                return next(r for r in state["reservations"] if r["id"] == rid)

            try:
                telemetry = self.telemetry_fn()
            except Exception as exc:  # pragma: no cover
                raise SchedulerError("NVML_UNAVAILABLE", f"Không đọc được NVML: {exc}", 503)
            gpu = next((g for g in telemetry if g.get("uuid") == gpu_uuid), None)
            if gpu is None:
                raise SchedulerError("GPU_NOT_OBSERVED",
                                     "Không quan sát được GPU này qua NVML; từ chối cấp phát.", 503)
            if gpu.get("active_processes"):
                raise SchedulerError("GPU_BUSY_EXTERNAL",
                                     "GPU đang có process ngoài hệ thống lịch; không thể giữ độc quyền.")
            if any(r["gpu_uuid"] == gpu_uuid and r["state"] in ACTIVE_STATES for r in state["reservations"]):
                raise SchedulerError("GPU_ALREADY_RESERVED", "GPU đã được giữ bởi một reservation khác.")

            r = {
                "id": f"rsv-{uuid.uuid4().hex[:10]}",
                "gpu_uuid": gpu_uuid,
                "gpu_id": gpu.get("id"),
                "host_id": gpu.get("server_id"),
                "owner": owner,
                "requested_by": actor,
                "job_name": job_name,
                "mode": mode,
                "state": "RESERVED",
                "runtime_verified": False,
                "pid": None,
                "reserved_at": _now(),
                "started_at": None,
                "ended_at": None,
                "idempotency_key": idempotency_key,
            }
            state["reservations"].append(r)
            if idempotency_key:
                state["idempotency"][idempotency_key] = r["id"]
            return r

    def request_stop(self, reservation_id: str, actor: str, is_admin: bool) -> Dict[str, Any]:
        with self._locked() as state:
            r = next((x for x in state["reservations"] if x["id"] == reservation_id), None)
            if r is None:
                raise SchedulerError("NOT_FOUND", "Không tìm thấy reservation/tác vụ.", 404)
            if not is_admin and r["owner"] != actor:
                raise SchedulerError("FORBIDDEN", "Bạn không sở hữu tác vụ này.", 403)
            if r["state"] == "RESERVED":
                # Chưa có runtime -> giải phóng ngay là trung thực.
                r["state"] = "RELEASED"
                r["ended_at"] = _now()
            elif r["state"] in ACTIVE_STATES:
                # Có runtime: chỉ ghi nhận yêu cầu, chờ Node Agent xác minh process đã dừng.
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

