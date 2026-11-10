import os
import sys
import json
import logging
import secrets
from app.core.security import AuthError, principal_from_authorization, require_node_agent, require_role
from app.services.gpu_scheduler import GpuScheduler, SchedulerError
from app.services.workspace_controller import WorkspaceSecurityError
from typing import Dict, Any

# Ensure backend root is in sys.path
backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

from app.core.config import settings
from app.services.labops_service import service

logger = logging.getLogger(__name__)

# Attempt to load FastAPI if installed
has_fastapi = False
app = None

try:
    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from app.api.v1.api import api_router

    app = FastAPI(
        title=settings.PROJECT_NAME,
        version=settings.VERSION,
        description="LabOps Control Center — Production REST API for Multi-Server AI Research Infrastructure",
        openapi_url=f"{settings.API_V1_STR}/openapi.json",
        docs_url=f"{settings.API_V1_STR}/docs",
        redoc_url=f"{settings.API_V1_STR}/redoc"
    )

    if settings.BACKEND_CORS_ORIGINS:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[str(origin) for origin in settings.BACKEND_CORS_ORIGINS],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(api_router, prefix=settings.API_V1_STR)

    @app.get("/healthz", tags=["Health"])
    async def healthz():
        return {
            "status": "ok",
            "app": settings.PROJECT_NAME,
            "version": settings.VERSION,
            "mode": "DEMO" if settings.DEMO_MODE else "PRODUCTION",
            "engine": "FastAPI"
        }

    @app.get("/", tags=["Root"])
    async def root():
        return {
            "message": "Welcome to LabOps Control Center API",
            "docs": f"{settings.API_V1_STR}/docs",
            "cluster": {
                "track": "Server 01 (Hà Nội, 3x A30 24GB)",
                "workstation": "Server 02 (TP.HCM, 1x A30 24GB)"
            }
        }
    has_fastapi = True
except ImportError:
    has_fastapi = False


# Lightweight, Zero-Dependency Standard Library HTTP Server Fallback
# Ensures the backend can run in any environment even if FastAPI is not installed
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

def _format_server(s: Dict[str, Any]) -> Dict[str, Any]:
    res = dict(s)
    res["ipAddress"] = s.get("ip_address", s.get("ipAddress", ""))
    res["vpnIp"] = s.get("vpn_ip", s.get("vpnIp", ""))
    res["cpuModel"] = s.get("cpu_model", s.get("cpuModel", ""))
    res["cpuCores"] = s.get("cpu_cores", s.get("cpuCores", 0))
    res["cpuUsagePercent"] = s.get("cpu_usage_percent", s.get("cpuUsagePercent", 0.0))
    res["ramTotalGb"] = s.get("ram_total_gb", s.get("ramTotalGb", 0.0))
    res["ramUsedGb"] = s.get("ram_used_gb", s.get("ramUsedGb", 0.0))
    res["ramUsagePercent"] = s.get("ram_usage_percent", s.get("ramUsagePercent", 0.0))
    res["diskTotalTb"] = s.get("disk_total_tb", s.get("diskTotalTb", 0.0))
    res["diskUsedTb"] = s.get("disk_used_tb", s.get("diskUsedTb", 0.0))
    res["diskUsagePercent"] = s.get("disk_usage_percent", s.get("diskUsagePercent", 0.0))
    res["diskType"] = s.get("disk_type", s.get("diskType", ""))
    res["networkInMbps"] = s.get("network_in_mbps", s.get("networkInMbps", 0))
    res["networkOutMbps"] = s.get("network_out_mbps", s.get("networkOutMbps", 0))
    res["gpuCount"] = s.get("gpu_count", s.get("gpuCount", 0))
    res["activeMembers"] = s.get("active_members", s.get("activeMembers", 0))
    res["activeJobs"] = s.get("active_jobs", s.get("activeJobs", 0))
    res["incusVersion"] = s.get("incus_version", s.get("incusVersion", ""))
    res["driverVersion"] = s.get("driver_version", s.get("driverVersion", ""))
    res["cudaVersion"] = s.get("cuda_version", s.get("cudaVersion", ""))
    res["lastUpdated"] = s.get("last_updated", s.get("lastUpdated", ""))
    return res

def _format_gpu(g: Dict[str, Any]) -> Dict[str, Any]:
    res = dict(g)
    res["displayLabel"] = g.get("display_label", g.get("displayLabel", g.get("id")))
    res["serverId"] = g.get("server_id", g.get("serverId", ""))
    res["serverName"] = g.get("server_name", g.get("serverName", ""))
    res["pciBusId"] = g.get("pci_bus_id", g.get("pciBusId", ""))
    res["vramTotalMb"] = g.get("vram_total_mb", g.get("vramTotalMb", 0))
    res["vramUsedMb"] = g.get("vram_used_mb", g.get("vramUsedMb", 0))
    res["gpuUtilPercent"] = g.get("gpu_util_percent", g.get("gpuUtilPercent", 0))
    res["tempCelsius"] = g.get("temp_celsius", g.get("tempCelsius", 0))
    res["powerWatts"] = g.get("power_watts", g.get("powerWatts", 0))
    res["powerLimitWatts"] = g.get("power_limit_watts", g.get("powerLimitWatts", 0))
    res["allocationMode"] = g.get("allocation_mode", g.get("allocationMode", "SHARED"))
    res["enforcementState"] = g.get("enforcement_state", g.get("enforcementState", "MONITOR_ONLY"))
    res["activeProcesses"] = [
        {
            "pid": p.get("pid", 0),
            "name": p.get("name", ""),
            "username": p.get("username", ""),
            "workspace": p.get("workspace", ""),
            "vramMb": p.get("vram_mb", p.get("vramMb", 0)),
            "cpuPercent": p.get("cpu_percent", p.get("cpuPercent", 0.0))
        }
        for p in g.get("active_processes", g.get("activeProcesses", []))
    ]
    res["activeUsers"] = g.get("active_users", g.get("activeUsers", []))
    res["associatedJobs"] = g.get("associated_jobs", g.get("associatedJobs", []))
    return res

class LabOpsRequestHandler(BaseHTTPRequestHandler):
    def _send_cors_headers(self):
        origin = self.headers.get("Origin", "")
        if origin and origin in settings.BACKEND_CORS_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS, PATCH")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, Idempotency-Key")

    PUBLIC_PATHS = ("/api/v1/health", "/health", "/api/v1/monitoring/health")
    ADMIN_PREFIXES = ("/api/v1/users", "/api/v1/admin", "/api/v1/ssh", "/api/v1/access", "/api/v1/audit-logs",
                      "/api/v1/events", "/api/v1/security/reviews", "/api/v1/storage-pools", "/api/v1/workspaces")

    def _authenticate(self, path: str):
        """Trả về Principal hoặc None (đã gửi lỗi). Fail-closed."""
        if path in self.PUBLIC_PATHS or not path.startswith("/api/"):
            return "PUBLIC"
        try:
            principal = principal_from_authorization(self.headers.get("Authorization"))
            if path.startswith(self.ADMIN_PREFIXES):
                require_role(principal, "ADMIN")
            return principal
        except AuthError as e:
            self._send_json(e.status, {"detail": e.detail})
            return None

    def _require_admin_api(self) -> bool:
        configured = settings.LABOPS_ADMIN_API_TOKEN
        if not configured:
            self._send_json(503, {"detail": "Access management authentication is not configured"})
            return False
        authorization = self.headers.get("Authorization", "")
        token = authorization[7:] if authorization.startswith("Bearer ") else ""
        if not token or not secrets.compare_digest(token, configured):
            self._send_json(401, {"detail": "Unauthorized"})
            return False
        return True

    def _send_json(self, status_code: int, data: Any):
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._send_cors_headers()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        principal = self._authenticate(path)
        if principal is None:
            return
        if path == "/api/v1/security/scans":
            self._send_json(200, service.security_scanner.list_scans(None if principal.is_admin else principal.username))
            return
        if path == "/api/v1/gpus/reservations/me":
            owner = None if principal.is_admin else principal.username
            self._send_json(200, [{**r, **GpuScheduler.gpu_hours(r)} for r in service.gpu_scheduler.list_reservations(owner)])
            return
        if path == "/api/v1/v2/hosts":
            self._send_json(200, service.get_v2_hosts())
            return
        if path == "/api/v1/v2/containers/summary":
            if not principal.is_admin:
                self._send_json(403, {"detail": "Chỉ quản trị viên được phép truy cập Arcane Docker Management"})
                return
            self._send_json(200, service.arcane_adapter.get_integration_summary())
            return
        if path == "/api/v1/v2/monitoring/beszel":
            self._send_json(200, service.beszel_adapter.get_monitoring_overview(live_hosts=service.get_v2_hosts()))
            return
        if path == "/api/v1/v2/workspaces":
            owner = None if principal.is_admin else principal.username
            self._send_json(200, service.workspace_controller.list_workspaces(owner=owner, legacy_endpoints=service.ssh_endpoints))
            return
        if path == "/api/v1/v2/ssh-keys":
            owner = None if principal.is_admin else principal.username
            self._send_json(200, service.ssh_key_registry.list_keys(username=owner))
            return
        if path == "/api/v1/v2/researcher/me":
            self._send_json(200, service.get_researcher_portal_snapshot(principal.username))
            return
        if path == "/api/v1/v2/reconcile":
            if not principal.is_admin:
                self._send_json(403, {"detail": "Chỉ quản trị viên được phép"})
                return
            self._send_json(200, service.host_registry.reconcile_json_stores())
            return

        principal = self._authenticate(path)
        if principal is None:
            return
        if path.startswith(("/api/v1/users", "/api/v1/admin/members", "/api/v1/ssh/endpoints", "/api/v1/access")):
            self._send_json(501, {"detail": "Secure access routes require the FastAPI backend"})
            return

        if path == "" or path == "/":
            self._send_json(200, {
                "message": "Welcome to LabOps Control Center API",
                "version": settings.VERSION,
                "cluster": {
                    "track": "Server 01 (Hà Nội, 3x A30 24GB)",
                    "workstation": "Server 02 (TP.HCM, 1x A30 24GB)"
                }
            })
        elif path == "/healthz":
            self._send_json(200, {
                "status": "ok",
                "app": settings.PROJECT_NAME,
                "version": settings.VERSION,
                "mode": "DEMO" if settings.DEMO_MODE else "PRODUCTION",
                "engine": "Python Standard Library ThreadingHTTPServer"
            })
        elif path == "/api/v1/dashboard/summary":
            self._send_json(200, service.get_summary())
        elif path == "/api/v1/servers":
            self._send_json(200, [_format_server(s) for s in service.servers])
        elif path.startswith("/api/v1/servers/"):
            parts = path.split("/")
            server_id = parts[4]
            if len(parts) > 5:
                sub = parts[5]
                if sub == "processes":
                    query_params = parse_qs(parsed.query)
                    filter_type = query_params.get("filter", ["all"])[0]
                    self._send_json(200, service.get_server_processes(server_id, filter_type=filter_type))
                    return
                elif sub == "gpu-processes":
                    self._send_json(200, service.get_server_gpu_processes(server_id))
                    return
                elif sub == "ssh-sessions":
                    self._send_json(200, service.get_server_ssh_sessions(server_id))
                    return
            srv = next((s for s in service.servers if s["id"] == server_id), None)
            if srv:
                self._send_json(200, _format_server(srv))
            else:
                self._send_json(404, {"detail": "Server not found"})
        elif path == "/api/v1/gpus":
            self._send_json(200, [_format_gpu(g) for g in service.gpus])
        elif path.startswith("/api/v1/gpus/"):
            gpu_id = path.split("/")[4]
            gpu = next((g for g in service.gpus if g["id"] == gpu_id), None)
            if gpu:
                self._send_json(200, _format_gpu(gpu))
            else:
                self._send_json(404, {"detail": "GPU not found"})
        elif path == "/api/v1/storage-pools":
            self._send_json(200, service.storage_pools)
        elif path == "/api/v1/ssh/endpoints":
            self._send_json(200, service.ssh_endpoints)
        elif path in ["/api/v1/jobs", "/api/v1/jobs/active"]:
            query_params = parse_qs(parsed.query)
            resource = query_params.get("resource", [None])[0]
            uname = query_params.get("username", [None])[0] or query_params.get("user", [None])[0]
            srv_id = query_params.get("server_id", [None])[0] or query_params.get("server", [None])[0]
            search_q = query_params.get("search", [None])[0]

            filtered = service.jobs
            if srv_id and srv_id != "all":
                filtered = [j for j in filtered if j.get("serverId") == srv_id or j.get("server_id") == srv_id]
            if uname and uname != "all":
                filtered = [j for j in filtered if j.get("username") == uname]
            if resource == "gpu":
                filtered = [j for j in filtered if j.get("isGpu") or j.get("is_gpu") or (j.get("vramUsedMb") or 0) > 0]
            elif resource == "cpu":
                filtered = [j for j in filtered if not (j.get("isGpu") or j.get("is_gpu")) and (j.get("vramUsedMb") or 0) <= 0]
            if search_q:
                q = search_q.lower()
                filtered = [
                    j for j in filtered
                    if q in str(j.get("pid", "")).lower()
                    or q in str(j.get("script", "")).lower()
                    or q in str(j.get("cmdline", "")).lower()
                    or q in str(j.get("username", "")).lower()
                    or q in str(j.get("gpuId", "")).lower()
                ]
            self._send_json(200, filtered)
        elif path.startswith("/api/v1/workspaces/") and path.endswith("/processes"):
            ws_id = path.split("/")[4]
            self._send_json(200, service.get_workspace_processes(ws_id))
        elif path == "/api/v1/monitoring/health":
            self._send_json(200, service.get_monitoring_health())
        elif path == "/api/v1/security":
            self._send_json(200, service.security_reviews)
        elif path == "/api/v1/events":
            self._send_json(200, service.events)
        elif path == "/api/v1/audit-logs":
            self._send_json(200, service.audit_logs)
        else:
            self._send_json(404, {"detail": f"Path '{path}' not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        principal = self._authenticate(path)
        if principal is None:
            return
        if path.startswith(("/api/v1/users", "/api/v1/admin/members", "/api/v1/ssh/endpoints", "/api/v1/access")):
            self._send_json(501, {"detail": "Secure access routes require the FastAPI backend"})
            return
        
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
        try:
            body = json.loads(raw_body)
        except Exception:
            body = {}

        if path == "/api/v1/gpus/allocate":
            try:
                owner = (body.get("username") or principal.username) if principal.is_admin else principal.username
                res = service.allocate_gpu(
                    gpu_id=body.get("gpu_id"),
                    username=owner,
                    mode=body.get("mode", "FULL_GPU_EXCLUSIVE"),
                    job_name=str(body.get("job_name", ""))[:200],
                    actor=principal.username,
                    idempotency_key=self.headers.get("Idempotency-Key"),
                )
                self._send_json(200, res)
            except SchedulerError as e:
                self._send_json(e.http_status, {"detail": {"code": e.code, "message": e.message}})
            except ValueError as e:
                self._send_json(400, {"detail": str(e)})
        elif path == "/api/v1/security/scans":
            try:
                self._send_json(200, service.security_scanner.submit(
                    str(body.get("repo_url", "")), str(body.get("commit_sha", "")), principal.username))
            except ValueError as e:
                self._send_json(400, {"detail": str(e)})
        elif path.startswith("/api/v1/security/scans/") and path.endswith("/decision"):
            if not principal.is_admin:
                self._send_json(403, {"detail": "Chỉ quản trị viên được phép"})
                return
            try:
                self._send_json(200, service.security_scanner.decide(path.split("/")[5], str(body.get("decision", "")), principal.username))
            except LookupError as e:
                self._send_json(404, {"detail": str(e)})
            except ValueError as e:
                self._send_json(409, {"detail": str(e)})
        elif path.startswith("/api/v1/v2/hosts/") and path.endswith("/agent/heartbeat"):
            try:
                require_node_agent(principal)
                host_id = path.split("/")[5]
                self._send_json(200, service.host_registry.record_agent_heartbeat(host_id, body))
            except AuthError as e:
                self._send_json(e.status, {"detail": e.detail})
            except LookupError as e:
                self._send_json(404, {"detail": str(e)})
        elif path == "/api/v1/v2/hosts":
            if not principal.is_admin:
                self._send_json(403, {"detail": "Chỉ quản trị viên được phép đăng ký máy chủ"})
                return
            try:
                self._send_json(200, service.host_registry.register_host(
                    host_id=str(body.get("host_id", "")),
                    hostname=str(body.get("hostname", "")),
                    display_name=str(body.get("display_name", "")),
                    management_ip=str(body.get("management_ip", "")),
                    role=str(body.get("role", "COMPUTE_NODE")),
                    vpn_ip=body.get("vpn_ip"),
                    agent_url=body.get("agent_url"),
                ))
            except ValueError as e:
                self._send_json(400, {"detail": str(e)})
        elif path == "/api/v1/v2/workspaces":
            if not principal.is_admin:
                self._send_json(403, {"detail": "Chỉ quản trị viên được phép tạo workspace"})
                return
            try:
                self._send_json(200, service.workspace_controller.request_workspace(
                    owner=str(body.get("owner", "")),
                    host_id=str(body.get("host_id", "workstation")),
                    ssh_public_key=str(body.get("ssh_public_key", "")),
                    image_ref=str(body.get("image_ref", "nvcr.io/nvidia/pytorch:24.08-py3")),
                    cpu_cores=int(body.get("cpu_cores", 8)),
                    ram_mb=int(body.get("ram_mb", 32768)),
                    pids_limit=int(body.get("pids_limit", 4096)),
                    storage_quota_gb=int(body.get("storage_quota_gb", 200)),
                    gpu_uuid=body.get("gpu_uuid"),
                    privileged=bool(body.get("privileged", False)),
                    mount_docker_socket=bool(body.get("mount_docker_socket", False)),
                    extra_mounts=body.get("extra_mounts"),
                    idempotency_key=self.headers.get("Idempotency-Key"),
                ))
            except WorkspaceSecurityError as e:
                self._send_json(e.http_status, {"detail": {"code": e.code, "message": e.message}})
        elif path == "/api/v1/v2/ssh-keys":
            owner = str(body.get("username") or principal.username) if principal.is_admin else principal.username
            try:
                self._send_json(200, service.ssh_key_registry.register_key(
                    username=owner,
                    label=str(body.get("label", "Khóa cá nhân")),
                    public_key=str(body.get("public_key", "")),
                    actor=principal.username,
                ))
            except ValueError as e:
                self._send_json(400, {"detail": str(e)})
        elif path.startswith("/api/v1/v2/ssh-keys/") and path.endswith("/revoke"):
            key_id = path.split("/")[5]
            try:
                self._send_json(200, service.ssh_key_registry.revoke_key(
                    key_id, actor=principal.username, is_admin=principal.is_admin
                ))
            except LookupError as e:
                self._send_json(404, {"detail": str(e)})
            except PermissionError as e:
                self._send_json(403, {"detail": str(e)})
        elif path.startswith("/api/v1/jobs/") and path.endswith("/stop"):
            job_id = path.split("/")[4]
            try:
                self._send_json(200, service.stop_job(job_id, actor=principal.username, is_admin=principal.is_admin))
            except SchedulerError as e:
                self._send_json(e.http_status, {"detail": {"code": e.code, "message": e.message}})
        else:
            self._send_json(404, {"detail": f"POST path '{path}' not found"})

    def log_message(self, format, *args):
        # Clean logging
        sys.stderr.write(f"[{self.log_date_time_string()}] {self.address_string()} - {format%args}\n")


def run_server(host="0.0.0.0", port=8000):
    if has_fastapi:
        try:
            import uvicorn
            print(f"🚀 Khởi chạy LabOps Control Center API bằng FastAPI/Uvicorn trên http://{host}:{port}")
            uvicorn.run("app.main:app", host=host, port=port, reload=True)
            return
        except ImportError:
            pass

    print(f"🚀 Khởi chạy LabOps Control Center API (Built-in Threading HTTP Engine) trên http://{host}:{port}")
    server = ThreadingHTTPServer((host, port), LabOpsRequestHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Dừng dịch vụ LabOps Control Center API.")
        server.server_close()


if __name__ == "__main__":
    run_server(host="0.0.0.0", port=8000)
