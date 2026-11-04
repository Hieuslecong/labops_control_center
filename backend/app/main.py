import os
import sys
import json
import logging
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
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS, PATCH")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")

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
        elif path in ["/api/v1/admin/members", "/api/v1/users"]:
            self._send_json(200, service.members)
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
        
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
        try:
            body = json.loads(raw_body)
        except Exception:
            body = {}

        if path == "/api/v1/admin/members":
            try:
                res = service.provision_member(body)
                self._send_json(201, res)
            except ValueError as e:
                self._send_json(400, {"detail": str(e)})
        elif path == "/api/v1/gpus/allocate":
            try:
                res = service.allocate_gpu(
                    gpu_id=body.get("gpu_id"),
                    username=body.get("username"),
                    mode=body.get("mode", "SHARED"),
                    requested_vram_gb=body.get("requested_vram_gb", 8),
                    job_name=body.get("job_name", "workload.py")
                )
                self._send_json(200, res)
            except ValueError as e:
                self._send_json(400, {"detail": str(e)})
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
