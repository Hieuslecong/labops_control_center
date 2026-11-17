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
import hashlib
import json
import os
import stat
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

ACTIVE_STATES = {"RESERVED", "STARTING", "RUNNING", "STOP_REQUESTED"}
ALLOWED_MODES = {"FULL_GPU_EXCLUSIVE"}
TERMINAL_STATES = {"RELEASED", "FAILED"}


class SchedulerError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _telemetry_is_fresh(gpu: Dict[str, Any], max_age_seconds: int = 90) -> bool:
    if gpu.get("live") is not True or gpu.get("freshness") != "ONLINE":
        return False
    collected_at = gpu.get("collected_at")
    if not isinstance(collected_at, str) or not collected_at:
        return False
    try:
        observed = datetime.fromisoformat(collected_at.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
        return 0 <= age <= max_age_seconds
    except (TypeError, ValueError, OverflowError):
        return False


def observed_scheduler_state(
    gpu: Dict[str, Any], reservations: List[Dict[str, Any]], *, reservation_state_available: bool
) -> str:
    """Return FREE only when both current GPU telemetry and reservation state are trusted."""
    if not reservation_state_available or not _telemetry_is_fresh(gpu):
        return "UNKNOWN"
    active = next((
        reservation for reservation in reservations
        if reservation.get("gpu_uuid") == gpu.get("uuid")
        and reservation.get("state") in ACTIVE_STATES
    ), None)
    if active:
        return active["state"]
    if gpu.get("active_processes"):
        return "BUSY_EXTERNAL"
    return "FREE"


class GpuScheduler:
    def __init__(self, state_path: str, telemetry_fn: Callable[[], List[Dict[str, Any]]]):
        self.state_path = state_path
        self.lock_path = state_path + ".lock"
        self.telemetry_fn = telemetry_fn
        self._persistence_failed: Optional[str] = None
        os.makedirs(os.path.dirname(state_path) or ".", exist_ok=True)

    # ---------- persistence ----------
    @contextmanager
    def _locked(self):
        fd = os.open(self.lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "r+") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                state = self._load()
                yield state
                try:
                    self._save(state)
                except OSError as exc:
                    self._persistence_failed = str(exc)
                    raise SchedulerError(
                        "STATE_PERSIST_FAILED",
                        "Không thể lưu trạng thái reservation bền vững; scheduler đã fail-closed.",
                        503,
                    ) from exc
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def _load(self) -> Dict[str, Any]:
        if self._persistence_failed:
            raise SchedulerError("STATE_PERSIST_FAILED", "Scheduler đang fail-closed sau lỗi lưu trạng thái.", 503)
        if not os.path.exists(self.state_path):
            return {"reservations": [], "idempotency": {}}
        try:
            info = os.lstat(self.state_path)
        except OSError as exc:
            raise SchedulerError("STATE_READ_FAILED", "Không thể kiểm tra tệp trạng thái GPU.", 503) from exc
        if not stat.S_ISREG(info.st_mode):
            raise SchedulerError("STATE_INVALID", "Tệp trạng thái GPU phải là regular file.", 503)
        with open(self.state_path, "r", encoding="utf-8") as f:
            state = json.load(f)
        if (
            not isinstance(state, dict)
            or not isinstance(state.get("reservations"), list)
            or not isinstance(state.get("idempotency"), dict)
        ):
            raise SchedulerError("STATE_INVALID", "Schema trạng thái GPU không hợp lệ; từ chối cấp phát.", 503)
        reservation_ids = set()
        active_gpus = set()
        for reservation in state["reservations"]:
            if not isinstance(reservation, dict):
                raise SchedulerError("STATE_INVALID", "Reservation GPU không hợp lệ.", 503)
            required_strings = ("id", "gpu_uuid", "host_id", "owner", "requested_by", "job_name", "mode", "state", "reserved_at")
            if any(not isinstance(reservation.get(key), str) or not reservation[key].strip() for key in required_strings):
                raise SchedulerError("STATE_INVALID", "Reservation GPU thiếu trường định danh bắt buộc.", 503)
            if reservation["id"] in reservation_ids or reservation["mode"] not in ALLOWED_MODES:
                raise SchedulerError("STATE_INVALID", "Reservation GPU trùng ID hoặc mode không hợp lệ.", 503)
            if reservation["state"] not in ACTIVE_STATES | TERMINAL_STATES:
                raise SchedulerError("STATE_INVALID", "Trạng thái reservation GPU không hợp lệ.", 503)
            reservation_ids.add(reservation["id"])
            if reservation["state"] in ACTIVE_STATES:
                if reservation["gpu_uuid"] in active_gpus:
                    raise SchedulerError("STATE_INVALID", "GPU có nhiều reservation đang hoạt động.", 503)
                active_gpus.add(reservation["gpu_uuid"])
        for key, reservation_id in state["idempotency"].items():
            if (
                not isinstance(key, str) or len(key) != 64
                or any(ch not in "0123456789abcdef" for ch in key)
                or not isinstance(reservation_id, str) or reservation_id not in reservation_ids
            ):
                raise SchedulerError("STATE_INVALID", "Idempotency index GPU không hợp lệ.", 503)
        return state

    def _save(self, state: Dict[str, Any]) -> None:
        parent = os.path.dirname(os.path.abspath(self.state_path))
        fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(self.state_path)}.", suffix=".tmp", dir=parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                fd = -1
                json.dump(state, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.state_path)
            dir_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass

    @staticmethod
    def _idempotency_index_key(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    # ---------- queries ----------
    def list_reservations(self, owner: Optional[str] = None) -> List[Dict[str, Any]]:
        res = self._load()["reservations"]
        return [r for r in res if owner is None or r["owner"] == owner]

    # ---------- commands ----------
    def reserve(self, *, gpu_uuid: str, owner: str, actor: str, job_name: str,
                mode: str = "FULL_GPU_EXCLUSIVE", idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        if not all(isinstance(value, str) and value.strip() and value == value.strip() for value in (gpu_uuid, owner, actor, job_name)):
            raise SchedulerError("INVALID_RESERVATION", "GPU, owner, actor và job name phải hợp lệ.", 400)
        if mode not in ALLOWED_MODES:
            raise SchedulerError("MODE_NOT_SUPPORTED",
                                 "V1 chỉ hỗ trợ chế độ FULL_GPU_EXCLUSIVE (độc quyền toàn bộ GPU).", 400)
        if idempotency_key is not None and (not idempotency_key.strip() or len(idempotency_key) > 200):
            raise SchedulerError("INVALID_IDEMPOTENCY_KEY", "Idempotency key không hợp lệ.", 400)
        with self._locked() as state:
            idem_index = self._idempotency_index_key(idempotency_key) if idempotency_key else None
            if idem_index and idem_index in state["idempotency"]:
                rid = state["idempotency"][idem_index]
                existing = next((r for r in state["reservations"] if r.get("id") == rid), None)
                if existing is None:
                    raise SchedulerError("STATE_INVALID", "Idempotency index tham chiếu reservation không tồn tại.", 503)
                same_request = (
                    existing.get("gpu_uuid") == gpu_uuid
                    and existing.get("owner") == owner
                    and existing.get("requested_by") == actor
                    and existing.get("job_name") == job_name
                    and existing.get("mode") == mode
                )
                if not same_request:
                    raise SchedulerError("IDEMPOTENCY_KEY_REUSED", "Idempotency key đã được dùng cho yêu cầu khác.", 409)
                return existing

            try:
                telemetry = self.telemetry_fn()
            except Exception as exc:  # pragma: no cover
                raise SchedulerError("NVML_UNAVAILABLE", f"Không đọc được NVML: {exc}", 503)
            matches = [g for g in telemetry if isinstance(g, dict) and g.get("uuid") == gpu_uuid]
            if len(matches) != 1:
                raise SchedulerError("GPU_NOT_OBSERVED",
                                     "GPU UUID không được quan sát duy nhất qua NVML; từ chối cấp phát.", 503)
            gpu = matches[0]
            if not _telemetry_is_fresh(gpu) or not isinstance(gpu.get("active_processes"), list):
                raise SchedulerError("GPU_TELEMETRY_STALE", "GPU telemetry chưa được xác minh hoặc đã cũ; từ chối cấp phát.", 503)
            if gpu["active_processes"]:
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
                "idempotency_key_hash": idem_index,
            }
            state["reservations"].append(r)
            if idem_index:
                state["idempotency"][idem_index] = r["id"]
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

    def confirm_from_agent(self, reservation_id: str, observed_state: str, pid: Optional[int],
                           node_id: Optional[str] = None) -> Dict[str, Any]:
        """Apply a node-bound runtime observation; machine credentials alone are insufficient."""
        with self._locked() as state:
            r = next((x for x in state["reservations"] if x["id"] == reservation_id), None)
            if r is None:
                raise SchedulerError("NOT_FOUND", "Không tìm thấy reservation.", 404)
            if not node_id or node_id != r.get("host_id"):
                raise SchedulerError("NODE_MISMATCH", "Node Agent không sở hữu reservation này.", 403)
            if observed_state not in {"RUNNING", "STOPPED", "EXITED", "FAILED"}:
                raise SchedulerError("INVALID_RUNTIME_STATE", "Trạng thái runtime không hợp lệ.", 400)
            if r.get("state") in {"RELEASED", "FAILED"}:
                raise SchedulerError("INVALID_STATE_TRANSITION", "Reservation đã kết thúc và không thể được cập nhật lại.", 409)
            if observed_state == "RUNNING":
                if type(pid) is not int or pid <= 0:
                    raise SchedulerError("INVALID_RUNTIME_PID", "PID runtime phải là số nguyên dương.", 400)
                # Do not erase an outstanding stop request when a stale/racing heartbeat
                # still observes the job as running.
                next_state = "STOP_REQUESTED" if r.get("state") == "STOP_REQUESTED" else "RUNNING"
                r.update(state=next_state, pid=pid, runtime_verified=True, started_at=r["started_at"] or _now())
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

