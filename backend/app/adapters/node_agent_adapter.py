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
import sys
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
    "ARCHIVE_REMOVE_DOCKER_WORKSPACE",
    "SYNC_SSH_AUTHORIZED_KEYS",
    "VERIFY_DOCKER_WORKSPACE",
    "INSPECT_WIREGUARD_PEERS",
    "LIST_STORAGE_POOLS",
    "INSPECT_STORAGE_POOL",
    "VALIDATE_STORAGE_ALLOCATION",
    "PROVISION_MEMBER_STORAGE",
})

READ_ONLY_LOCAL_FALLBACK_ACTIONS = frozenset({
    "INSPECT_HOST",
    "INSPECT_GPUS",
    "VERIFY_PROCESS_PID",
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
        actor_id: str = "fastapi-controller",
        actor_role: str = "ADMIN",
    ) -> Dict[str, Any]:
        if action not in ALLOWED_AGENT_ACTIONS:
            raise NodeAgentError("ACTION_NOT_ALLOWLISTED", f"Thao tác '{action}' bị từ chối bởi allowlist.", 403)
        request_id = f"req-{uuid.uuid4().hex[:12]}"
        node_id = str(host.get("id") or "")
        is_local = node_id == self.local_host_id

        # Chuẩn hóa parameters thành string map cho chữ ký DelegationToken & Go Executor
        str_params: Dict[str, str] = {}
        for k, v in (params or {}).items():
            if v is None:
                continue
            str_params[k] = v if isinstance(v, str) else json.dumps(v)

        delegation_secret = os.getenv("LABOPS_DELEGATION_SECRET", "").strip()
        auth_ctx = ""
        if delegation_secret:
            from app.core.delegation import build_delegation_context
            auth_ctx = build_delegation_context(
                secret=delegation_secret,
                command_id=request_id,
                actor=actor_id,
                role=actor_role,
                node_id=node_id,
                action=action,
                workspace_id=str(str_params.get("workspace_id") or str_params.get("workspace") or ""),
                parameters=str_params,
            )

        # 1. Thử gửi lệnh qua LabOps Zero-Trust Gateway (mTLS gRPC tunnel) nếu được cấu hình
        gateway_http_url = os.getenv("LABOPS_GATEWAY_HTTP_URL", "").strip()
        control_secret = os.getenv("LABOPS_GATEWAY_CONTROL_SECRET", "").strip()
        if gateway_http_url and control_secret:
            gw_headers = {"Content-Type": "application/json", "Authorization": f"Bearer {control_secret}"}
            dispatch_body: Dict[str, Any] = {
                "node_id": node_id,
                "action": action,
                "parameters": str_params,
                "timeout_seconds": 15,
                "idempotency_key": idempotency_key or "",
                "actor_id": actor_id,
            }
            if auth_ctx:
                dispatch_body["authorization_context"] = auth_ctx
            try:
                gw_req = urllib.request.Request(
                    f"{gateway_http_url.rstrip('/')}/api/v1/gateway/dispatch",
                    data=json.dumps(dispatch_body).encode("utf-8"),
                    headers=gw_headers,
                    method="POST",
                )
                with urllib.request.urlopen(gw_req, timeout=15) as resp:
                    gw_data = json.loads(resp.read().decode("utf-8"))
                    if gw_data.get("success"):
                        return {
                            "request_id": gw_data.get("command_id", request_id),
                            "host_id": node_id,
                            "agent_version": AGENT_VERSION,
                            "action": action,
                            "executed_at": _now(),
                            "result": gw_data.get("result", {}),
                        }
                    if gw_data.get("error_code") == "ACTION_NOT_ALLOWED":
                        raise NodeAgentError("ACTION_NOT_ALLOWLISTED", gw_data.get("error_message", ""), 403)
                    if gw_data.get("error_code") != "NODE_OFFLINE":
                        raise NodeAgentError(
                            gw_data.get("error_code", "GATEWAY_ERROR"),
                            gw_data.get("error_message", "Lỗi điều khiển từ xa"),
                            500,
                        )
            except NodeAgentError:
                raise
            except Exception:
                pass

        # 2. Gửi thống nhất tới Go Node Agent HTTP endpoint có xác thực (cho cả local Workstation lẫn remote Rack)
        agent_url = str(host.get("agent_url") or "").strip()
        if not agent_url and is_local:
            agent_url = os.getenv("LABOPS_LOCAL_AGENT_URL", "").strip()
            if not agent_url and not ("PYTEST_CURRENT_TEST" in os.environ or "unittest" in sys.modules):
                from app.core.config import settings
                agent_url = getattr(settings, "LABOPS_LOCAL_AGENT_URL", "").strip()

        token = (
            os.getenv("LABOPS_NODE_AGENT_TOKEN", "").strip()
            or os.getenv("LABOPS_AGENT_SECRET", "").strip()
        )
        if not token and not ("PYTEST_CURRENT_TEST" in os.environ or "unittest" in sys.modules):
            from app.core.config import settings
            token = (
                getattr(settings, "LABOPS_NODE_AGENT_TOKEN", "").strip()
                or getattr(settings, "LABOPS_AGENT_SECRET", "").strip()
            )

        if agent_url and token:
            exec_body: Dict[str, Any] = {
                "request_id": request_id,
                "node_id": node_id,
                "actor_id": actor_id,
                "action": action,
                "parameters": str_params,
                "idempotency_key": idempotency_key or "",
            }
            if auth_ctx:
                exec_body["authorization_context"] = auth_ctx
            req = urllib.request.Request(
                f"{agent_url.rstrip('/')}/api/v1/agent/execute",
                data=json.dumps(exec_body).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "X-LabOps-Agent-Secret": token,
                    "Authorization": f"Bearer {token}",
                    "X-Request-Id": request_id,
                    **({"Idempotency-Key": idempotency_key} if idempotency_key else {}),
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    res_data = json.loads(resp.read().decode("utf-8"))
                    if not res_data.get("success", False):
                        raise NodeAgentError(
                            str(res_data.get("error_code") or "AGENT_EXECUTION_FAILED"),
                            str(res_data.get("error_message") or "Node Agent từ chối hoặc thực thi thất bại"),
                            409,
                        )
                    return res_data
            except urllib.error.HTTPError as http_err:
                raw_err = http_err.read().decode("utf-8", errors="replace") if hasattr(http_err, "read") else ""
                err_code = "AGENT_REJECTED"
                err_msg = f"Node Agent HTTP {http_err.code}"
                try:
                    parsed_err = json.loads(raw_err)
                    if isinstance(parsed_err, dict):
                        err_code = str(parsed_err.get("error_code") or err_code)
                        err_msg = str(parsed_err.get("error_message") or parsed_err.get("error") or err_msg)
                except Exception:
                    if raw_err:
                        err_msg = f"{err_msg}: {raw_err[:200]}"
                raise NodeAgentError(err_code, err_msg, http_err.code) from http_err
            except NodeAgentError:
                raise
            except Exception as exc:
                if is_local and action in READ_ONLY_LOCAL_FALLBACK_ACTIONS:
                    return self.local_executor.execute(
                        action, params, request_id=request_id, idempotency_key=idempotency_key
                    )
                raise NodeAgentError(
                    "AGENT_UNREACHABLE",
                    f"Không thể kết nối Node Agent tại {agent_url}: {exc}",
                    503,
                ) from exc

        # Chỉ cho phép fallback đọc telemetry cục bộ; mọi thao tác Docker/SSH/Storage PHẢI đi qua Go Node Agent
        if is_local and action in READ_ONLY_LOCAL_FALLBACK_ACTIONS:
            return self.local_executor.execute(
                action, params, request_id=request_id, idempotency_key=idempotency_key
            )
        raise NodeAgentError(
            "AGENT_NOT_CONFIGURED",
            f"Node Agent cho máy chủ '{node_id}' chưa cấu hình agent_url/LABOPS_NODE_AGENT_TOKEN cho thao tác '{action}'.",
            503,
        )

