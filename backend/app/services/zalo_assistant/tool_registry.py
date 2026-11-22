"""
Schema-Validated Read-Only SRE Tool Registry & AI Investigation Runner
Executes RBAC-checked read-only diagnostic tools, scrubs secrets/tokens, guards against
prompt injection in untrusted logs/repo metadata, enforces timeouts/tool budgets, and
records immutable AgentToolAuditRecord entries.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import select

from app.db.models import AgentToolAuditRecord
from app.db.session import DatabaseManager
from app.services.zalo_assistant.identity_rbac import ZaloPrincipal
from app.services.zalo_assistant.incident_engine import UnifiedIncidentEngine
from app.services.zalo_assistant.repo_alert_monitor import _fetch_agent_requests_sync
from app.services.zalo_assistant.report_generator import ReportGenerator
from app.services.zalo_assistant.telemetry_collector import UnifiedTelemetryCollector

logger = logging.getLogger("labops.sre.tools")

_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
    re.compile(r"(?i)(token|secret|password|api[_-]?key|authorization|bearer)\s*[:=]\s*['\"]?([A-Za-z0-9_\-.:/+=]{8,})['\"]?"),
    re.compile(r"\b(sk_live_|pk_live_|sk_test_|ghp_|gho_|glpat-)[A-Za-z0-9_\-]{8,}\b"),
]


def scrub_sensitive_text(text: str) -> str:
    cleaned = str(text or "")
    for pat in _SECRET_PATTERNS:
        cleaned = pat.sub("[REDACTED_SECRET]", cleaned)
    return cleaned


def _scrub_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return scrub_sensitive_text(obj)
    if isinstance(obj, dict):
        return {
            k: ("[REDACTED]" if str(k).lower() in {"masked_value", "secret", "token", "bot_token", "password"} else _scrub_obj(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_scrub_obj(v) for v in obj]
    return obj


class SREToolRegistry:
    MAX_TOOL_CALLS_PER_TURN = 4
    TOOL_TIMEOUT_SECONDS = 5.0

    TOOL_SPECS: Dict[str, Dict[str, Any]] = {
        "get_system_health": {
            "description": "Tổng quan sức khỏe toàn cụm máy chủ (Workstation, Rack, freshness, critical incidents).",
            "admin_only": False,
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        "get_server_metrics": {
            "description": "Chỉ số chi tiết CPU, RAM, Disk, Heartbeat và Freshness của từng máy chủ.",
            "admin_only": False,
            "parameters": {
                "type": "object",
                "properties": {"host_id": {"type": "string"}},
                "additionalProperties": False,
            },
        },
        "get_gpu_metrics": {
            "description": "Thông số GPU (host_id + uuid, utilization, VRAM, nhiệt độ, cấp phát).",
            "admin_only": False,
            "parameters": {
                "type": "object",
                "properties": {"host_id": {"type": "string"}},
                "additionalProperties": False,
            },
        },
        "get_active_jobs": {
            "description": "Danh sách tác vụ tính toán đang chạy (tự lọc theo quyền chủ sở hữu).",
            "admin_only": False,
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        "get_job_history": {
            "description": "Lịch sử các tác vụ tính toán (RUNNING/COMPLETED/FAILED).",
            "admin_only": False,
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
                "additionalProperties": False,
            },
        },
        "get_workspace_usage": {
            "description": "Trạng thái Docker workspace, tài nguyên, OOMKilled và dung lượng.",
            "admin_only": False,
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        "get_incidents": {
            "description": "Danh sách sự cố hạ tầng & bảo mật (lọc theo quyền RBAC).",
            "admin_only": False,
            "parameters": {
                "type": "object",
                "properties": {"status": {"type": "string"}, "severity": {"type": "string"}},
                "additionalProperties": False,
            },
        },
        "get_security_events": {
            "description": "Sự kiện an ninh SSH/Audit/Policy (chỉ Admin).",
            "admin_only": True,
            "parameters": {
                "type": "object",
                "properties": {"limit": {"type": "integer"}},
                "additionalProperties": False,
            },
        },
        "get_repository_scan_result": {
            "description": "Kết quả quét bảo mật Trivy sau clone cho các kho mã.",
            "admin_only": False,
            "parameters": {
                "type": "object",
                "properties": {"repo_filter": {"type": "string"}},
                "additionalProperties": False,
            },
        },
        "get_service_health": {
            "description": "Trạng thái Docker, Trivy, WireGuard, PostgreSQL, Beszel/Arcane.",
            "admin_only": False,
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        "get_metrics_history": {
            "description": "KPI 24 giờ từ dữ liệu lịch sử (CPU avg/peak, RAM peak, availability).",
            "admin_only": False,
            "parameters": {
                "type": "object",
                "properties": {"host_id": {"type": "string"}, "window_hours": {"type": "integer"}},
                "additionalProperties": False,
            },
        },
        "get_report_summary": {
            "description": "Tóm tắt báo cáo điều hành ngày (08:00) hoặc báo cáo tuần gần nhất.",
            "admin_only": False,
            "parameters": {
                "type": "object",
                "properties": {"report_type": {"type": "string"}},
                "additionalProperties": False,
            },
        },
    }

    def __init__(
        self,
        data_dir: str,
        *,
        db_manager: Optional[DatabaseManager] = None,
        telemetry_collector: Optional[UnifiedTelemetryCollector] = None,
        incident_engine: Optional[UnifiedIncidentEngine] = None,
        report_generator: Optional[ReportGenerator] = None,
        repo_fetcher: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        custom_handlers: Optional[Dict[str, Callable[..., Dict[str, Any]]]] = None,
        tool_timeout_seconds: float = TOOL_TIMEOUT_SECONDS,
    ):
        self.data_dir = os.path.abspath(data_dir)
        self.db = db_manager or DatabaseManager(self.data_dir)
        self.telemetry = telemetry_collector or UnifiedTelemetryCollector(self.data_dir, db_manager=self.db)
        self.incidents = incident_engine or UnifiedIncidentEngine(self.data_dir, db_manager=self.db)
        self.reports = report_generator or ReportGenerator(self.data_dir, db_manager=self.db)
        self.repo_fetcher = repo_fetcher or _fetch_agent_requests_sync
        self.custom_handlers = dict(custom_handlers or {})
        self.tool_timeout_seconds = float(tool_timeout_seconds)

    def openai_tool_schemas(self, principal: ZaloPrincipal) -> List[Dict[str, Any]]:
        schemas: List[Dict[str, Any]] = []
        for name, spec in self.TOOL_SPECS.items():
            if spec["admin_only"] and not principal.is_admin:
                continue
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": spec["description"],
                        "parameters": spec["parameters"],
                    },
                }
            )
        return schemas

    def _audit_tool(
        self,
        *,
        correlation_id: str,
        principal: ZaloPrincipal,
        source: str,
        question: str,
        tool_name: str,
        args: Dict[str, Any],
        rbac_allowed: bool,
        status: str,
        duration_ms: int,
        summary: str,
    ) -> None:
        try:
            with self.db.session_scope() as session:
                session.add(
                    AgentToolAuditRecord(
                        id=f"taud-{uuid.uuid4().hex[:12]}",
                        correlation_id=correlation_id,
                        actor_username=principal.username or principal.zalo_user_id or "anonymous",
                        actor_role=principal.role,
                        zalo_user_id=principal.zalo_user_id or None,
                        source=source,
                        question_snippet=scrub_sensitive_text(question)[:250],
                        tool_name=tool_name,
                        tool_args_json=_scrub_obj(args),
                        rbac_allowed=rbac_allowed,
                        status=status,
                        duration_ms=duration_ms,
                        result_summary=scrub_sensitive_text(summary)[:1000],
                        created_at=datetime.now(timezone.utc),
                    )
                )
        except Exception as exc:
            logger.debug("Cannot write agent tool audit: %s", exc)

    def execute_tool(
        self,
        tool_name: str,
        args: Optional[Dict[str, Any]],
        principal: ZaloPrincipal,
        *,
        correlation_id: str = "",
        source: str = "zalo",
        question: str = "",
    ) -> Dict[str, Any]:
        started = time.monotonic()
        corr = correlation_id or f"corr-{uuid.uuid4().hex[:8]}"
        clean_args = dict(args or {})

        if tool_name not in self.TOOL_SPECS:
            dur = int((time.monotonic() - started) * 1000)
            self._audit_tool(
                correlation_id=corr,
                principal=principal,
                source=source,
                question=question,
                tool_name=tool_name,
                args=clean_args,
                rbac_allowed=False,
                status="UNKNOWN_TOOL",
                duration_ms=dur,
                summary="Unregistered tool rejected",
            )
            return {"error": "UNKNOWN_TOOL", "tool_name": tool_name}

        if not principal.is_authorized:
            dur = int((time.monotonic() - started) * 1000)
            self._audit_tool(
                correlation_id=corr,
                principal=principal,
                source=source,
                question=question,
                tool_name=tool_name,
                args=clean_args,
                rbac_allowed=False,
                status="UNAUTHORIZED",
                duration_ms=dur,
                summary="Unauthorized caller",
            )
            return {"error": "UNAUTHORIZED", "detail": "Caller is not authorized."}

        spec = self.TOOL_SPECS[tool_name]
        allowed_keys = set((spec["parameters"].get("properties") or {}).keys())
        clean_args = {k: v for k, v in clean_args.items() if k in allowed_keys}

        if spec["admin_only"] and not principal.is_admin:
            dur = int((time.monotonic() - started) * 1000)
            self._audit_tool(
                correlation_id=corr,
                principal=principal,
                source=source,
                question=question,
                tool_name=tool_name,
                args=clean_args,
                rbac_allowed=False,
                status="PERMISSION_DENIED",
                duration_ms=dur,
                summary="Admin-only tool denied for non-admin",
            )
            return {"error": "PERMISSION_DENIED", "detail": f"Tool '{tool_name}' requires ADMIN role."}

        handler = self.custom_handlers.get(tool_name) or getattr(self, f"_tool_{tool_name}")
        pool = ThreadPoolExecutor(max_workers=1)
        try:
            future = pool.submit(handler, clean_args, principal)
            raw_res = future.result(timeout=self.tool_timeout_seconds)
            result = _scrub_obj(raw_res)
            status = "SUCCESS"
        except FuturesTimeoutError:
            result = {
                "error": "TOOL_TIMEOUT",
                "tool_name": tool_name,
                "timeout_seconds": self.tool_timeout_seconds,
            }
            status = "TIMEOUT"
        except Exception as exc:
            result = {"error": "TOOL_EXECUTION_ERROR", "detail": str(exc)[:250]}
            status = "FAILED"
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        dur = int((time.monotonic() - started) * 1000)
        self._audit_tool(
            correlation_id=corr,
            principal=principal,
            source=source,
            question=question,
            tool_name=tool_name,
            args=clean_args,
            rbac_allowed=True,
            status=status,
            duration_ms=dur,
            summary=json.dumps(result, ensure_ascii=False, default=str)[:400],
        )
        return result

    # ---------------- Individual RBAC-Enforced Tool Handlers ----------------

    def _tool_get_system_health(self, args: Dict[str, Any], principal: ZaloPrincipal) -> Dict[str, Any]:
        snap = self.telemetry.collect_snapshot()
        open_inc = self.incidents.list_incidents(limit=100)
        if not principal.is_admin:
            open_inc = [i for i in open_inc if i.get("owner_id") == principal.username]
        open_active = [i for i in open_inc if i.get("status") != "RESOLVED"]
        return {
            "collected_at": snap["collected_at"],
            "overall_health": snap["overall_health"],
            "hosts": [
                {
                    "host_id": h["host_id"],
                    "hostname": h["hostname"],
                    "status": h["status"],
                    "freshness_status": h["freshness_status"],
                    "last_heartbeat_at": h["last_heartbeat_at"],
                    "collected_at": h["collected_at"],
                }
                for h in snap["hosts"]
            ],
            "open_incidents_count": len(open_active),
            "p0_incidents_count": sum(1 for i in open_active if i.get("severity") == "P0"),
        }

    def _tool_get_server_metrics(self, args: Dict[str, Any], principal: ZaloPrincipal) -> Dict[str, Any]:
        snap = self.telemetry.collect_snapshot()
        hid = str(args.get("host_id") or "").strip().lower()
        hosts = snap["hosts"]
        if hid:
            hosts = [h for h in hosts if str(h["host_id"]).lower() == hid or str(h["hostname"]).lower() == hid]
        return {"collected_at": snap["collected_at"], "hosts": hosts}

    def _tool_get_gpu_metrics(self, args: Dict[str, Any], principal: ZaloPrincipal) -> Dict[str, Any]:
        snap = self.telemetry.collect_snapshot()
        hid = str(args.get("host_id") or "").strip().lower()
        gpus = snap["gpus"]
        if hid:
            gpus = [g for g in gpus if str(g["host_id"]).lower() == hid]
        if not principal.is_admin:
            filtered = []
            for g in gpus:
                g_copy = dict(g)
                g_copy["allocations"] = [a for a in (g.get("allocations") or []) if a.get("username") == principal.username]
                filtered.append(g_copy)
            gpus = filtered
        return {"collected_at": snap["collected_at"], "gpus": gpus}

    def _tool_get_active_jobs(self, args: Dict[str, Any], principal: ZaloPrincipal) -> Dict[str, Any]:
        snap = self.telemetry.collect_snapshot()
        jobs = [j for j in snap["jobs"] if j.get("status") == "RUNNING"]
        if not principal.is_admin:
            jobs = [j for j in jobs if j.get("owner") == principal.username]
        return {"collected_at": snap["collected_at"], "running_count": len(jobs), "jobs": jobs}

    def _tool_get_job_history(self, args: Dict[str, Any], principal: ZaloPrincipal) -> Dict[str, Any]:
        snap = self.telemetry.collect_snapshot()
        limit = max(1, min(int(args.get("limit") or 20), 100))
        jobs = snap["jobs"]
        if not principal.is_admin:
            jobs = [j for j in jobs if j.get("owner") == principal.username]
        return {"collected_at": snap["collected_at"], "jobs": jobs[:limit]}

    def _tool_get_workspace_usage(self, args: Dict[str, Any], principal: ZaloPrincipal) -> Dict[str, Any]:
        snap = self.telemetry.collect_snapshot()
        ws = snap["workspaces"]
        if not principal.is_admin:
            ws = [w for w in ws if w.get("owner") == principal.username]
        return {"collected_at": snap["collected_at"], "workspaces": ws}

    def _tool_get_incidents(self, args: Dict[str, Any], principal: ZaloPrincipal) -> Dict[str, Any]:
        owner_filter = None if principal.is_admin else principal.username
        items = self.incidents.list_incidents(
            status=args.get("status"),
            severity=args.get("severity"),
            owner_id=owner_filter,
            limit=30,
        )
        return {"collected_at": datetime.now(timezone.utc).isoformat(), "incidents": items}

    def _tool_get_security_events(self, args: Dict[str, Any], principal: ZaloPrincipal) -> Dict[str, Any]:
        if not principal.is_admin:
            return {"error": "PERMISSION_DENIED"}
        limit = max(1, min(int(args.get("limit") or 15), 50))
        logs: List[Dict[str, Any]] = []
        try:
            from app.services.labops_service import service as core_svc

            logs = list(getattr(core_svc, "audit_logs", []) or [])[:limit]
        except Exception:
            pass
        sec_inc = self.incidents.list_incidents(source="repository_guard", limit=15)
        return {
            "collected_at": datetime.now(timezone.utc).isoformat(),
            "audit_events": logs,
            "security_incidents": sec_inc,
        }

    def _tool_get_repository_scan_result(self, args: Dict[str, Any], principal: ZaloPrincipal) -> Dict[str, Any]:
        try:
            reqs = self.repo_fetcher() or []
        except Exception as exc:
            return {"collected_at": datetime.now(timezone.utc).isoformat(), "error": str(exc)[:150], "repositories": []}

        if not principal.is_admin:
            reqs = [r for r in reqs if str(r.get("submitted_by") or "") == principal.username]

        filt = str(args.get("repo_filter") or "").strip().lower()
        if filt:
            reqs = [
                r
                for r in reqs
                if filt in str(r.get("repo_url") or "").lower()
                or filt in str(r.get("repo_name") or "").lower()
            ]
        return {"collected_at": datetime.now(timezone.utc).isoformat(), "repositories": reqs[:20]}

    def _tool_get_service_health(self, args: Dict[str, Any], principal: ZaloPrincipal) -> Dict[str, Any]:
        snap = self.telemetry.collect_snapshot()
        return {"collected_at": snap["collected_at"], "services": snap["services"]}

    def _tool_get_metrics_history(self, args: Dict[str, Any], principal: ZaloPrincipal) -> Dict[str, Any]:
        hid = args.get("host_id")
        wh = int(args.get("window_hours") or 24)
        return self.telemetry.get_history_kpis(host_id=hid, window_hours=wh)

    def _tool_get_report_summary(self, args: Dict[str, Any], principal: ZaloPrincipal) -> Dict[str, Any]:
        rtype = str(args.get("report_type") or "daily").lower()
        if rtype == "daily":
            period_key, _, _ = self.reports.compute_daily_window()
            rep = self.reports.get_daily_report_by_period(period_key)
            if rep is not None:
                return {"report_type": "DAILY_EXECUTIVE", "report": rep}
        reps = self.reports.list_reports()
        return {"report_type": "WEEKLY_OPERATIONAL", "report": reps[0] if reps else None}

    def list_recent_tool_audit(self, limit: int = 30) -> List[Dict[str, Any]]:
        with self.db.session_scope() as session:
            rows = session.execute(
                select(AgentToolAuditRecord).order_by(AgentToolAuditRecord.created_at.desc()).limit(limit)
            ).scalars().all()
            return [
                {
                    "id": r.id,
                    "correlation_id": r.correlation_id,
                    "actor_username": r.actor_username,
                    "actor_role": r.actor_role,
                    "tool_name": r.tool_name,
                    "rbac_allowed": bool(r.rbac_allowed),
                    "status": r.status,
                    "duration_ms": r.duration_ms,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }
                for r in rows
            ]


class SREAgentRunner:
    def __init__(self, registry: SREToolRegistry):
        self.registry = registry

    def _select_deterministic_tools(self, question: str, principal: ZaloPrincipal) -> List[tuple[str, Dict[str, Any]]]:
        q = str(question or "").lower()
        selected: List[tuple[str, Dict[str, Any]]] = []

        if any(k in q for k in ("chậm", "slow", "lag", "cpu", "ram", "disk", "ổ", "workstation", "rack", "máy chủ", "tải")):
            selected.extend([
                ("get_server_metrics", {}),
                ("get_metrics_history", {"window_hours": 24}),
                ("get_workspace_usage", {}),
                ("get_incidents", {}),
            ])
        elif any(k in q for k in ("gpu", "vram", "nhiệt", "cuda", "a30")):
            selected.extend([
                ("get_gpu_metrics", {}),
                ("get_active_jobs", {}),
                ("get_incidents", {}),
            ])
        elif any(k in q for k in ("repo", "clone", "scan", "trivy", "mã nguồn", "github")):
            selected.extend([
                ("get_repository_scan_result", {}),
                ("get_incidents", {}),
            ])
        elif any(k in q for k in ("bảo mật", "security", "ssh", "audit", "tấn công", "xâm nhập")):
            selected.append(("get_security_events", {}) if principal.is_admin else ("get_incidents", {}))
            selected.append(("get_incidents", {}))
        elif any(k in q for k in ("job", "train", "tác vụ", "oom", "workspace", "docker")):
            selected.extend([
                ("get_active_jobs", {}),
                ("get_workspace_usage", {}),
                ("get_incidents", {}),
            ])
        elif any(k in q for k in ("báo cáo", "report", "kpi")):
            selected.extend([
                ("get_report_summary", {"report_type": "daily"}),
                ("get_system_health", {}),
            ])
        else:
            selected.extend([
                ("get_system_health", {}),
                ("get_gpu_metrics", {}),
                ("get_active_jobs", {}),
                ("get_incidents", {}),
            ])
        # Deduplicate while preserving order and enforcing max calls per turn
        seen = set()
        out: List[tuple[str, Dict[str, Any]]] = []
        for name, args in selected:
            if name not in seen and len(out) < self.registry.MAX_TOOL_CALLS_PER_TURN:
                seen.add(name)
                out.append((name, args))
        return out

    def run_investigation(
        self,
        question: str,
        principal: ZaloPrincipal,
        *,
        ai_client: Optional[Any] = None,
        source: str = "zalo",
    ) -> Dict[str, Any]:
        corr_id = f"inv-{uuid.uuid4().hex[:10]}"
        traces: List[Dict[str, Any]] = []
        evidence_bundles: List[Dict[str, Any]] = []
        timestamps: List[str] = []

        # Wrap user question safely (never treat logs/user text as system instructions)
        sys_msg = (
            "Bạn là LabOps AI SRE Agent v2. Trả lời bằng tiếng Việt ngắn gọn, CHỈ dựa vào dữ liệu JSON từ công cụ (tool evidence). "
            "Không tự suy diễn số liệu bị thiếu (nếu null/None ghi rõ N/A). Luôn cấu trúc: Kết luận, Bằng chứng + Timestamp, Độ chắc chắn, Hành động đề xuất."
        )

        # Try native tool calling if ai_client supports generate_with_tools
        used_native_tools = False
        if ai_client is not None and hasattr(ai_client, "generate_with_tools"):
            schemas = self.registry.openai_tool_schemas(principal)
            messages: List[Dict[str, Any]] = [
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": scrub_sensitive_text(question)},
            ]
            first = ai_client.generate_with_tools(messages, tools=schemas, temperature=0.1, max_tokens=600)
            tcalls = first.get("tool_calls") or []
            if isinstance(tcalls, list) and len(tcalls) > 0:
                used_native_tools = True
                for tc in tcalls[: self.registry.MAX_TOOL_CALLS_PER_TURN]:
                    fn = tc.get("function") or {}
                    tname = str(fn.get("name") or "")
                    try:
                        raw_args = fn.get("arguments")
                        parsed_args = json.loads(raw_args) if isinstance(raw_args, str) and raw_args.strip() else (raw_args if isinstance(raw_args, dict) else {})
                    except Exception:
                        parsed_args = {}
                    res = self.registry.execute_tool(
                        tname,
                        parsed_args,
                        principal,
                        correlation_id=corr_id,
                        source=source,
                        question=question,
                    )
                    traces.append({"tool": tname, "args": parsed_args, "status": "DENIED" if res.get("error") == "PERMISSION_DENIED" else ("TIMEOUT" if res.get("error") == "TOOL_TIMEOUT" else "OK")})
                    evidence_bundles.append({"tool": tname, "result": res})
                    if res.get("collected_at"):
                        timestamps.append(str(res["collected_at"]))

        if not used_native_tools:
            plan = self._select_deterministic_tools(question, principal)
            for tname, args in plan:
                res = self.registry.execute_tool(
                    tname,
                    args,
                    principal,
                    correlation_id=corr_id,
                    source=source,
                    question=question,
                )
                traces.append({"tool": tname, "args": args, "status": "DENIED" if res.get("error") == "PERMISSION_DENIED" else ("TIMEOUT" if res.get("error") == "TOOL_TIMEOUT" else "OK")})
                evidence_bundles.append({"tool": tname, "result": res})
                if res.get("collected_at"):
                    timestamps.append(str(res["collected_at"]))

        evidence_json = json.dumps(evidence_bundles, ensure_ascii=False, default=str)[:4500]
        ai_used = False
        reply_text = ""

        if ai_client is not None:
            try:
                synthesis_prompt = (
                    f"Câu hỏi của {principal.role} (@{principal.username}): {scrub_sensitive_text(question)}\n\n"
                    f"BẰNG CHỨNG CÔNG CỤ ĐÃ THỰC THI (JSON):\n{evidence_json}\n\n"
                    "Hãy phân tích và trả lời theo 4 mục:\n"
                    "1) Kết luận\n2) Bằng chứng & Thời gian\n3) Mức độ chắc chắn\n4) Hành động đề xuất"
                )
                raw = ai_client.generate_chat_response(
                    [{"role": "user", "content": synthesis_prompt}],
                    system_prompt=sys_msg,
                    temperature=0.1,
                    max_tokens=650,
                )
                if isinstance(raw, str) and raw.strip() and not raw.startswith(("Không thể kết nối", "Lỗi cổng", "Trợ lý không nhận được")):
                    reply_text = scrub_sensitive_text(raw.strip())
                    ai_used = True
            except Exception:
                pass

        if not reply_text:
            reply_text = self._deterministic_fallback_answer(question, principal, evidence_bundles, timestamps)

        return {
            "reply": reply_text,
            "tool_traces": traces,
            "evidence_timestamps": list(dict.fromkeys(timestamps)),
            "context_scope": f"{principal.role}:{principal.username}",
            "ai_used": ai_used,
            "correlation_id": corr_id,
        }

    @staticmethod
    def _deterministic_fallback_answer(
        question: str,
        principal: ZaloPrincipal,
        evidence_bundles: List[Dict[str, Any]],
        timestamps: List[str],
    ) -> str:
        lines = [
            "🔍 PHÂN TÍCH HỆ THỐNG (Deterministic SRE Fallback):",
            f"• Phạm vi quyền: {principal.role} (@{principal.username})",
        ]
        for b in evidence_bundles:
            tname = b["tool"]
            res = b["result"]
            if res.get("error"):
                lines.append(f"• {tname}: Lỗi {res.get('error')} ({res.get('detail', '')})")
                continue
            if tname in {"get_system_health", "get_server_metrics"}:
                for h in res.get("hosts") or []:
                    cpu = f"{h.get('cpu_usage_percent')}%" if h.get("cpu_usage_percent") is not None else "N/A"
                    ram = f"{h.get('ram_used_gb')}/{h.get('ram_total_gb')} GiB" if h.get("ram_total_gb") else "N/A"
                    lines.append(f"• Host `{h.get('hostname')}`: {h.get('status')} (freshness={h.get('freshness_status')}) | CPU {cpu} | RAM {ram}")
            elif tname == "get_gpu_metrics":
                for g in res.get("gpus") or []:
                    util = f"{g.get('utilization_gpu')}%" if g.get("utilization_gpu") is not None else "N/A"
                    temp = f"{g.get('temp_celsius')}°C" if g.get("temp_celsius") is not None else "N/A"
                    lines.append(f"• GPU `{g.get('gpu_key')}`: Util {util}, Temp {temp}")
            elif tname == "get_incidents":
                incs = res.get("incidents") or []
                lines.append(f"• Sự cố ghi nhận: {len(incs)} sự cố" + (f" (gần nhất: {incs[0].get('incident_id')} [{incs[0].get('severity')}] {incs[0].get('title')})" if incs else ""))
            elif tname == "get_active_jobs":
                lines.append(f"• Tác vụ đang chạy: {res.get('running_count', 0)}")
            elif tname == "get_metrics_history":
                lines.append(f"• KPI 24h: Avail={res.get('availability_percent') or 'N/A'}% | CPU Peak={res.get('cpu_peak_percent') or 'N/A'}% (samples={res.get('sample_count', 0)})")
        if timestamps:
            lines.append(f"• Timestamp bằng chứng: {timestamps[0]}")
        lines.append("• Độ chắc chắn: Xác định trực tiếp từ telemetry/DB (AI Gateway hiện ngoại tuyến).")
        return "\n".join(lines)
