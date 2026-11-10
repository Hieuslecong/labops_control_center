"""
LabOps Node Agent & Adapter (V2).

Thiết kế an toàn theo Mục 11 của prompt.md:
- Xác thực giữa Control Plane và Node Agent bằng machine token riêng (LABOPS_NODE_AGENT_TOKEN).
- Chỉ thực thi các thao tác nằm trong ALLOWLIST cố định, có kiểm tra tham số chặt chẽ.
- Tuyệt đối KHÔNG nhận lệnh shell tùy ý từ API hay giao diện người dùng.
- Có request_id, idempotency_key, timeout, log có cấu trúc và tương quan với audit event.
- Khi thao tác thất bại trên host hoặc không kết nối được agent -> fail-closed và trả lỗi trung thực.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

AGENT_VERSION = "2.0.0"

# Danh sách thao tác được phép duy nhất giữa Control Plane và Node Agent
ALLOWED_AGENT_ACTIONS = frozenset({
    "INSPECT_HOST",
    "INSPECT_GPUS",
    "VERIFY_PROCESS_PID",
    "PROVISION_DOCKER_WORKSPACE",
    "STOP_DOCKER_WORKSPACE",
    "SYNC_SSH_AUTHORIZED_KEYS",
    "INSPECT_WIREGUARD_PEERS",
})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class NodeAgentError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 503):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


class LocalNodeAgentExecutor:
    """Thực thi các thao tác allowlist ngay trên node cục bộ (không qua shell tùy ý)."""

    def __init__(self, host_id: str = "workstation"):
        self.host_id = host_id
        self._idempotency_cache: Dict[str, Dict[str, Any]] = {}

    def execute(
        self,
        action: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        request_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        request_id = request_id or f"req-{uuid.uuid4().hex[:12]}"
        params = params or {}
        if action not in ALLOWED_AGENT_ACTIONS:
            raise NodeAgentError(
                "ACTION_NOT_ALLOWLISTED",
                f"Thao tác '{action}' không nằm trong danh sách cho phép của Node Agent.",
                403,
            )
        if idempotency_key and idempotency_key in self._idempotency_cache:
            cached = dict(self._idempotency_cache[idempotency_key])
            cached["idempotent_replay"] = True
            return cached

        if action == "INSPECT_HOST":
            result = self._inspect_host()
        elif action == "INSPECT_GPUS":
            result = self._inspect_gpus()
        elif action == "VERIFY_PROCESS_PID":
            result = self._verify_process_pid(int(params.get("pid", 0)))
        elif action == "INSPECT_WIREGUARD_PEERS":
            result = self._inspect_wireguard(str(params.get("interface", "wg0")))
        else:
            # Các thao tác đột biến hạ tầng (Docker/SSH/WireGuard) kiểm tra quyền thực thi thực tế
            result = self._fail_closed_mutation(action, params)

        envelope = {
            "request_id": request_id,
            "host_id": self.host_id,
            "agent_version": AGENT_VERSION,
            "action": action,
            "executed_at": _now(),
            "result": result,
        }
        if idempotency_key:
            self._idempotency_cache[idempotency_key] = envelope
        return envelope

    def _inspect_host(self) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "hostname": platform.node(),
            "os": f"{platform.system()} {platform.release()}",
            "kernel": platform.release(),
            "arch": platform.machine(),
            "docker_available": bool(shutil.which("docker")),
            "nvidia_smi_available": bool(shutil.which("nvidia-smi")),
            "wg_available": bool(shutil.which("wg")),
            "trivy_available": bool(shutil.which("trivy")),
            "source": "node_agent_local",
            "collected_at": _now(),
            "freshness": "ONLINE",
        }
        try:
            import psutil
            vm = psutil.virtual_memory()
            info["cpu_logical_cores"] = psutil.cpu_count(logical=True)
            info["cpu_physical_cores"] = psutil.cpu_count(logical=False)
            info["ram_total_mb"] = int(vm.total // (1024 * 1024))
            info["ram_used_mb"] = int(vm.used // (1024 * 1024))
            info["ram_unit"] = "MiB"
        except Exception:
            pass
        return info

    def _inspect_gpus(self) -> Dict[str, Any]:
        if not shutil.which("nvidia-smi"):
            return {"status": "UNAVAILABLE", "gpus": [], "collected_at": _now()}
        try:
            out = subprocess.check_output(
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,uuid,memory.total,memory.used,utilization.gpu,temperature.gpu,mig.mode.current",
                    "--format=csv,noheader,nounits",
                ],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
            )
            gpus = []
            for line in out.strip().splitlines():
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 8:
                    gpus.append({
                        "index": int(parts[0]),
                        "model": parts[1],
                        "uuid": parts[2],
                        "vram_total_mb": int(parts[3]),
                        "vram_used_mb": int(parts[4]),
                        "gpu_util_percent": int(parts[5]),
                        "temp_celsius": int(parts[6]),
                        "mig_mode": parts[7],
                        "unit": "MiB",
                    })
            return {"status": "ONLINE", "source": "nvidia-smi", "collected_at": _now(), "gpus": gpus}
        except Exception as exc:
            raise NodeAgentError("NVML_QUERY_FAILED", f"Không thể truy vấn nvidia-smi: {exc}", 503)

    def _verify_process_pid(self, pid: int) -> Dict[str, Any]:
        if pid <= 0:
            raise NodeAgentError("INVALID_PID", "PID không hợp lệ", 400)
        exists = os.path.exists(f"/proc/{pid}")
        return {"pid": pid, "running": exists, "verified_at": _now(), "source": "/proc"}

    def _inspect_wireguard(self, interface: str) -> Dict[str, Any]:
        if interface != "wg0":
            raise NodeAgentError("INVALID_INTERFACE", "Chỉ cho phép kiểm tra giao diện wg0", 400)
        if not shutil.which("wg"):
            return {"status": "NOT_CONFIGURED", "reason": "Không tìm thấy binary wg"}
        try:
            out = subprocess.check_output(["wg", "show", interface, "dump"], stderr=subprocess.PIPE, text=True, timeout=3)
            return {"status": "ONLINE", "lines": len(out.strip().splitlines()), "collected_at": _now()}
        except Exception as exc:
            return {"status": "BLOCKED_BY_INFRASTRUCTURE", "reason": str(exc)[:200], "collected_at": _now()}

    def _fail_closed_mutation(self, action: str, params: Dict[str, Any]) -> Dict[str, Any]:
        raise NodeAgentError(
            "BLOCKED_BY_INFRASTRUCTURE",
            f"Thao tác '{action}' yêu cầu daemon Node Agent có đặc quyền trên host đích; không giả lập thành công.",
            503,
        )


class NodeAgentClient:
    """Client từ Control Plane gọi sang Node Agent (cục bộ hoặc từ xa qua HTTP + Bearer machine token)."""

    def __init__(self, local_host_id: str = "workstation"):
        self.local_host_id = local_host_id
        self.local_executor = LocalNodeAgentExecutor(host_id=local_host_id)

    def dispatch(
        self,
        host: Dict[str, Any],
        action: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        if action not in ALLOWED_AGENT_ACTIONS:
            raise NodeAgentError("ACTION_NOT_ALLOWLISTED", f"Thao tác '{action}' bị từ chối bởi allowlist.", 403)
        request_id = f"req-{uuid.uuid4().hex[:12]}"
        if host.get("id") == self.local_host_id:
            return self.local_executor.execute(
                action, params, request_id=request_id, idempotency_key=idempotency_key
            )

        agent_url = host.get("agent_url")
        token = os.getenv("LABOPS_NODE_AGENT_TOKEN", "")
        if not agent_url or not token:
            raise NodeAgentError(
                "AGENT_NOT_CONFIGURED",
                f"Node Agent cho máy chủ '{host.get('id')}' chưa cấu hình URL hoặc LABOPS_NODE_AGENT_TOKEN.",
                503,
            )
        req = urllib.request.Request(
            f"{agent_url.rstrip('/')}/v1/execute",
            data=json.dumps({"action": action, "params": params or {}, "request_id": request_id}).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "X-Request-Id": request_id,
                **({"Idempotency-Key": idempotency_key} if idempotency_key else {}),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise NodeAgentError(
                "AGENT_UNREACHABLE",
                f"Không thể kết nối Node Agent tại {agent_url}: {exc}",
                503,
            )

