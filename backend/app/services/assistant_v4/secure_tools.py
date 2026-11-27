"""
LabOps Personal AI Agent V4 — Secure Schema-Validated Read-Only & Proposal Tool Executor.
- Validates every tool input with strict Pydantic models (`extra='forbid'`).
- Always reloads/verifies principal server-side (ignores username/role claims inside tool args).
- Never uses local Docker CLI, arbitrary shell, SSH, sudo, raw SQL, or prefix container matching.
- Non-admin Docker/workspace access checks exact DB/controller ownership; omitted container returns own only.
- Wraps every result in typed ToolEvidenceResult(status, observed_at, freshness, source_id, data, error_code, evidence_ref).
- Audits every allowed, denied, invalid-arg, and timed-out invocation in AgentToolAuditRecord.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from app.db.models import (
    AgentToolAuditRecord,
    AuditEventRecord,
    MemberProfileRecord,
    SupervisorAssignmentRecord,
    V4KnowledgeDocumentRecord,
)
from app.db.session import DatabaseManager
from app.services.assistant_v4.event_store import redact_payload, redact_text
from app.services.zalo_assistant.identity_rbac import ZaloPrincipal
from app.services.zalo_assistant.incident_engine import UnifiedIncidentEngine
from app.services.zalo_assistant.telemetry_collector import UnifiedTelemetryCollector


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------- Strict Pydantic Input Models ----------------


class _EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _HostArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host_id: Optional[str] = Field(default=None, max_length=64)


class _HistoryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host_id: Optional[str] = Field(default=None, max_length=64)
    window_hours: int = Field(default=24, ge=1, le=720)


class _CompareArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: Literal["gpu", "cpu", "ram", "disk"] = "gpu"
    host_id: Optional[str] = Field(default=None, max_length=64)
    window_hours: int = Field(default=24, ge=1, le=168)


class _IncidentListArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Optional[str] = Field(default=None, max_length=32)
    severity: Optional[str] = Field(default=None, max_length=16)
    limit: int = Field(default=20, ge=1, le=100)


class _IdArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: str = Field(min_length=1, max_length=128)


class _ContainerStatsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    container_id: Optional[str] = Field(default=None, max_length=128)


class _MemberArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: Optional[str] = Field(default=None, max_length=64)


class _LimitArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=20, ge=1, le=50)
    actor: Optional[str] = Field(default=None, max_length=64)


class _LogSearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["backend", "worker", "proxy"] = "backend"
    keyword: str = Field(min_length=1, max_length=100)
    lines: int = Field(default=30, ge=1, le=50)


class _QueryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=120)


class _RepoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_filter: Optional[str] = Field(default=None, max_length=120)


class _WatchProposalArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: str = Field(min_length=1, max_length=128)
    metric: Literal["gpu_temperature_c", "gpu_util_percent", "storage_percent", "cpu_percent", "ram_percent", "node_status", "job_status"]
    comparator: Literal["lt", "lte", "gt", "gte", "eq"]
    threshold: float = Field(allow_inf_nan=False)
    sustained_seconds: int = Field(default=300, ge=0, le=86400)
    once: bool = True


class _ReminderProposalArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    in_minutes: int = Field(default=60, ge=1, le=43200)


class _ActionReasonArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(default="", max_length=300)


class ToolSpecV4:
    def __init__(
        self,
        *,
        name: str,
        description: str,
        admin_only: bool,
        input_model: type[BaseModel],
        operation: str,
        handler: Callable[[BaseModel, ZaloPrincipal], Dict[str, Any]],
        timeout_s: float = 5.0,
    ):
        self.name = name
        self.description = description
        self.admin_only = admin_only
        self.input_model = input_model
        self.operation = operation
        self.handler = handler
        self.timeout_s = timeout_s


class SecureToolExecutor:
    """Typed schema-validated, RBAC-reverified read-only & proposal tool registry for V4."""

    FORBIDDEN_CAPABILITIES = {
        "execute_arbitrary_shell",
        "sudo_system_commands",
        "remote_ssh_execution",
        "restart_server_or_daemon",
        "delete_user_workspace",
        "docker_privileged_operations",
        "network_routing_modification",
        "raw_sql_execution",
    }

    def __init__(
        self,
        data_dir: str,
        *,
        db_manager: DatabaseManager,
        telemetry_collector: Optional[UnifiedTelemetryCollector] = None,
        incident_engine: Optional[UnifiedIncidentEngine] = None,
        identity_reloader: Optional[Callable[[ZaloPrincipal], ZaloPrincipal]] = None,
        task_service: Optional[Any] = None,
        proposal_service: Optional[Any] = None,
        log_paths: Optional[Dict[str, str]] = None,
    ):
        self.data_dir = os.path.abspath(data_dir)
        self.db = db_manager
        self.telemetry = telemetry_collector or UnifiedTelemetryCollector(self.data_dir, db_manager=db_manager)
        self.incidents = incident_engine or UnifiedIncidentEngine(self.data_dir, db_manager=db_manager)
        self.identity_reloader = identity_reloader
        self.task_service = task_service
        self.proposal_service = proposal_service
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))
        self.log_paths = dict(
            log_paths
            or {
                "backend": os.path.join(self.data_dir, "labops.log"),
                "worker": os.path.join(self.data_dir, "zalo_worker.log"),
                "proxy": os.path.join(repo_root, ".runtime", "proxy.log"),
            }
        )
        self._specs: Dict[str, ToolSpecV4] = {}
        self._register_builtin_tools()

    def _register(self, spec: ToolSpecV4) -> None:
        if spec.name in self.FORBIDDEN_CAPABILITIES:
            raise ValueError(f"Forbidden tool name: {spec.name}")
        self._specs[spec.name] = spec

    def _register_builtin_tools(self) -> None:
        self._register(ToolSpecV4(name="get_node_status", description="Trạng thái realtime các máy chủ (Workstation, Rack, freshness).", admin_only=False, input_model=_HostArgs, operation="telemetry.read", handler=self._t_get_node_status))
        self._register(ToolSpecV4(name="get_gpu_metrics", description="Thông số GPU (utilization, VRAM, nhiệt độ, host online).", admin_only=False, input_model=_HostArgs, operation="gpu.read", handler=self._t_get_gpu_metrics))
        self._register(ToolSpecV4(name="get_service_health", description="Trạng thái dịch vụ hệ thống (Docker, PostgreSQL, WireGuard, gateway).", admin_only=False, input_model=_EmptyArgs, operation="service.read", handler=self._t_get_service_health))
        self._register(ToolSpecV4(name="get_storage_usage", description="Dung lượng ổ đĩa hiện tại và phân bổ storage pool.", admin_only=False, input_model=_HostArgs, operation="storage.read", handler=self._t_get_storage_usage))
        self._register(ToolSpecV4(name="get_metric_history", description="Lịch sử metric theo cửa sổ giờ (CPU, RAM, GPU, disk growth).", admin_only=False, input_model=_HistoryArgs, operation="history.read", handler=self._t_get_metric_history))
        self._register(ToolSpecV4(name="compare_resource_metrics", description="So sánh tải hiện tại với lịch sử trước đó nếu có đủ mẫu.", admin_only=False, input_model=_CompareArgs, operation="history.compare", handler=self._t_compare_metrics))
        self._register(ToolSpecV4(name="list_incident_history", description="Danh sách sự cố hạ tầng và bảo mật theo quyền.", admin_only=False, input_model=_IncidentListArgs, operation="incident.read", handler=self._t_list_incidents))
        self._register(ToolSpecV4(name="list_my_jobs", description="Danh sách tác vụ tính toán (lọc theo quyền sở hữu).", admin_only=False, input_model=_EmptyArgs, operation="job.read", handler=self._t_list_jobs))
        self._register(ToolSpecV4(name="get_job", description="Chi tiết một job theo ID (chủ sở hữu hoặc Admin).", admin_only=False, input_model=_IdArgs, operation="job.read", handler=self._t_get_job))
        self._register(ToolSpecV4(name="get_workspace", description="Thông tin Docker workspace thuộc quyền sở hữu.", admin_only=False, input_model=_ContainerStatsArgs, operation="workspace.read", handler=self._t_get_container_stats))
        self._register(ToolSpecV4(name="get_container_stats", description="Thống kê CPU/RAM/OOM/storage của container theo exact ownership (không match prefix).", admin_only=False, input_model=_ContainerStatsArgs, operation="container.read", handler=self._t_get_container_stats))
        self._register(ToolSpecV4(name="get_gpu_allocations", description="Thống kê phân bổ GPU/VRAM theo thiết bị và tài khoản.", admin_only=False, input_model=_EmptyArgs, operation="gpu.alloc.read", handler=self._t_get_gpu_allocations))
        self._register(ToolSpecV4(name="get_member_info", description="Thông tin hồ sơ thành viên, port SSH và quota (Admin xem tất cả, Researcher chỉ xem của mình).", admin_only=False, input_model=_MemberArgs, operation="member.read", handler=self._t_get_member_info))
        self._register(ToolSpecV4(name="list_pending_requests", description="Danh sách đơn xin cấp SSH đang chờ Admin duyệt.", admin_only=True, input_model=_EmptyArgs, operation="mram.read", handler=self._t_list_pending_requests))
        self._register(ToolSpecV4(name="get_request", description="Chi tiết một đơn SSH theo ID (chủ đơn hoặc Admin).", admin_only=False, input_model=_IdArgs, operation="mram.read", handler=self._t_get_request))
        self._register(ToolSpecV4(name="get_group_usage", description="Thống kê thành viên/sử dụng trong nhóm được phân công hướng dẫn.", admin_only=False, input_model=_EmptyArgs, operation="group.read", handler=self._t_get_group_usage))
        self._register(ToolSpecV4(name="get_audit_trail", description="Nhật ký kiểm toán bảo mật hệ thống (Chỉ Admin).", admin_only=True, input_model=_LimitArgs, operation="audit.read", handler=self._t_get_audit_trail))
        self._register(ToolSpecV4(name="get_vpn_status", description="Trạng thái WireGuard VPN và danh sách thiết bị (Chỉ Admin).", admin_only=True, input_model=_EmptyArgs, operation="vpn.read", handler=self._t_get_vpn_status))
        self._register(ToolSpecV4(name="search_redacted_logs", description="Tìm tối đa 50 dòng trong log được phép (Chỉ Admin, đã lọc secret).", admin_only=True, input_model=_LogSearchArgs, operation="logs.read", handler=self._t_search_redacted_logs))
        self._register(ToolSpecV4(name="get_repository_scan", description="Kết quả quét bảo mật Trivy sau clone (lọc theo quyền).", admin_only=False, input_model=_RepoArgs, operation="repo.read", handler=self._t_get_repository_scan))
        self._register(ToolSpecV4(name="search_runbooks", description="Tìm runbook khắc phục sự cố đã duyệt.", admin_only=False, input_model=_QueryArgs, operation="kb.read", handler=self._t_search_kb))
        self._register(ToolSpecV4(name="get_runbook_version", description="Đọc phiên bản cụ thể của runbook hoặc tài liệu kỹ thuật.", admin_only=False, input_model=_IdArgs, operation="kb.read", handler=self._t_get_kb_doc))
        self._register(ToolSpecV4(name="search_faq", description="Tra cứu hướng dẫn SSH, Docker, Conda và FAQ nội bộ.", admin_only=False, input_model=_QueryArgs, operation="kb.read", handler=self._t_search_kb))
        self._register(ToolSpecV4(name="list_my_tasks", description="Danh sách watch rule và tác vụ nhắc việc của bạn.", admin_only=False, input_model=_EmptyArgs, operation="task.read", handler=self._t_list_my_tasks))
        # Proposal-only tools (model proposes; NEVER executes core writes directly)
        self._register(ToolSpecV4(name="propose_watch_rule", description="Đề xuất tạo watch theo dõi ngưỡng phần cứng (cần xác nhận).", admin_only=False, input_model=_WatchProposalArgs, operation="task.propose", handler=self._t_propose_watch))
        self._register(ToolSpecV4(name="propose_reminder", description="Đề xuất đặt lịch nhắc việc một lần (cần xác nhận).", admin_only=False, input_model=_ReminderProposalArgs, operation="task.propose", handler=self._t_propose_reminder))
        self._register(ToolSpecV4(name="propose_cancel_task", description="Đề xuất hủy một task/watch hiện có.", admin_only=False, input_model=_ActionReasonArgs, operation="task.propose", handler=self._t_propose_cancel))
        self._register(ToolSpecV4(name="propose_ack", description="Đề xuất xác nhận (ack) một sự cố với mã bảo vệ.", admin_only=False, input_model=_ActionReasonArgs, operation="action.propose", handler=lambda a, p: self._t_propose_r2("ack_incident", "INCIDENT", a, p)))
        self._register(ToolSpecV4(name="propose_resolve", description="Đề xuất đóng sự cố (cần Admin xác nhận bằng mã bảo vệ & bằng chứng).", admin_only=True, input_model=_ActionReasonArgs, operation="action.propose", handler=lambda a, p: self._t_propose_r2("resolve_incident", "INCIDENT", a, p)))
        self._register(ToolSpecV4(name="propose_reject", description="Đề xuất từ chối đơn SSH (cần Admin xác nhận bằng mã bảo vệ).", admin_only=True, input_model=_ActionReasonArgs, operation="action.propose", handler=lambda a, p: self._t_propose_r2("reject_request", "SSH_REQUEST", a, p)))
        self._register(ToolSpecV4(name="propose_approval", description="Đề xuất phê duyệt cấp phát SSH/GPU (luôn điều hướng Dashboard step-up auth).", admin_only=True, input_model=_ActionReasonArgs, operation="action.propose", handler=lambda a, p: self._t_propose_r3("approve_request", "SSH_REQUEST", a, p)))

    def list_allowed_tool_schemas(self, principal: ZaloPrincipal, *, allowed_names: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        allowed_set = set(allowed_names) if allowed_names is not None else None
        out: List[Dict[str, Any]] = []
        for name, spec in self._specs.items():
            if allowed_set is not None and name not in allowed_set:
                continue
            if spec.admin_only and not principal.is_admin:
                continue
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": spec.description,
                        "parameters": spec.input_model.model_json_schema(),
                    },
                }
            )
        return out

    def _wrap_evidence(
        self,
        *,
        status: str,
        source_id: str,
        freshness: str = "FRESH",
        observed_at: Optional[str] = None,
        data: Any = None,
        error_code: Optional[str] = None,
        evidence_ref: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "status": status,
            "observed_at": observed_at or _utcnow_iso(),
            "freshness": freshness,
            "source_id": source_id,
            "data": redact_payload(data) if data is not None else {},
            "error_code": error_code,
            "evidence_ref": evidence_ref or f"evd-{uuid.uuid4().hex[:8]}",
        }

    def _write_audit(self, *, corr_id: str, principal: ZaloPrincipal, source: str, question: str, tool_name: str, args: Any, allowed: bool, status: str, dur_ms: int, summary: str) -> None:
        try:
            with self.db.session_scope() as session:
                session.add(
                    AgentToolAuditRecord(
                        id=f"taud4-{uuid.uuid4().hex[:11]}",
                        correlation_id=corr_id,
                        actor_username=principal.username or principal.zalo_user_id or "anonymous",
                        actor_role=principal.role,
                        zalo_user_id=principal.zalo_user_id or None,
                        source=source,
                        question_snippet=redact_text(question)[:250],
                        tool_name=tool_name,
                        tool_args_json=redact_payload(args) if isinstance(args, dict) else {},
                        rbac_allowed=allowed,
                        status=status,
                        duration_ms=dur_ms,
                        result_summary=redact_text(summary)[:800],
                        created_at=datetime.now(timezone.utc),
                    )
                )
        except Exception:
            pass

    def execute_tool(
        self,
        tool_name: str,
        raw_args: Optional[Dict[str, Any]],
        principal: ZaloPrincipal,
        *,
        allowed_tool_names: Optional[List[str]] = None,
        correlation_id: str = "",
        source: str = "zalo",
        question: str = "",
    ) -> Dict[str, Any]:
        t0 = time.monotonic()
        corr = correlation_id or f"corr-{uuid.uuid4().hex[:8]}"

        # Reload verified principal identity (never trust model-supplied identity)
        eff_principal = self.identity_reloader(principal) if self.identity_reloader else principal
        if not eff_principal.is_authorized or eff_principal.role == "ANONYMOUS":
            res = self._wrap_evidence(status="DENIED", source_id="auth", error_code="UNAUTHORIZED")
            self._write_audit(corr_id=corr, principal=eff_principal, source=source, question=question, tool_name=tool_name, args=raw_args, allowed=False, status="UNAUTHORIZED", dur_ms=0, summary="Unauthorized caller")
            return res

        if tool_name in self.FORBIDDEN_CAPABILITIES or tool_name not in self._specs:
            res = self._wrap_evidence(status="DENIED", source_id="tool_registry", error_code="TOOL_NOT_ALLOWED")
            self._write_audit(corr_id=corr, principal=eff_principal, source=source, question=question, tool_name=tool_name, args=raw_args, allowed=False, status="TOOL_NOT_ALLOWED", dur_ms=0, summary="Blocked unregistered/forbidden tool")
            return res

        if allowed_tool_names is not None and tool_name not in set(allowed_tool_names):
            res = self._wrap_evidence(status="DENIED", source_id="skill_policy", error_code="SKILL_TOOL_DENIED")
            self._write_audit(corr_id=corr, principal=eff_principal, source=source, question=question, tool_name=tool_name, args=raw_args, allowed=False, status="PERMISSION_DENIED", dur_ms=0, summary="Tool excluded from active skill")
            return res

        spec = self._specs[tool_name]
        if spec.admin_only and not eff_principal.is_admin:
            res = self._wrap_evidence(status="DENIED", source_id="rbac", error_code="PERMISSION_DENIED")
            self._write_audit(corr_id=corr, principal=eff_principal, source=source, question=question, tool_name=tool_name, args=raw_args, allowed=False, status="PERMISSION_DENIED", dur_ms=0, summary="Admin role required")
            return res

        try:
            validated_args = spec.input_model.model_validate(raw_args or {})
        except ValidationError as exc:
            res = self._wrap_evidence(status="ERROR", source_id="validator", error_code="INVALID_ARGUMENTS", data={"detail": str(exc)[:250]})
            self._write_audit(corr_id=corr, principal=eff_principal, source=source, question=question, tool_name=tool_name, args=raw_args, allowed=True, status="INVALID_ARGS", dur_ms=0, summary="Schema validation failed")
            return res

        pool = ThreadPoolExecutor(max_workers=1)
        try:
            fut = pool.submit(spec.handler, validated_args, eff_principal)
            res = fut.result(timeout=spec.timeout_s)
            status = str(res.get("status") or "OK")
        except PermissionError as exc:
            res = self._wrap_evidence(status="DENIED", source_id=tool_name, error_code="PERMISSION_DENIED", data={"detail": str(exc)[:200]})
            status = "PERMISSION_DENIED"
        except FuturesTimeoutError:
            res = self._wrap_evidence(status="ERROR", source_id=tool_name, error_code="TOOL_TIMEOUT")
            status = "TIMEOUT"
        except Exception as exc:
            res = self._wrap_evidence(status="ERROR", source_id=tool_name, error_code="TOOL_EXECUTION_ERROR", data={"detail": str(exc)[:200]})
            status = "FAILED"
        finally:
            pool.shutdown(wait=False, cancel_futures=True)

        dur_ms = int((time.monotonic() - t0) * 1000)
        self._write_audit(
            corr_id=corr,
            principal=eff_principal,
            source=source,
            question=question,
            tool_name=tool_name,
            args=validated_args.model_dump(),
            allowed=status != "PERMISSION_DENIED",
            status=status,
            dur_ms=dur_ms,
            summary=json.dumps(res, ensure_ascii=False, default=str)[:400],
        )
        return res

    # ---------------- Individual Handlers ----------------

    def _t_get_node_status(self, args: _HostArgs, principal: ZaloPrincipal) -> Dict[str, Any]:
        snap = self.telemetry.collect_snapshot()
        hosts = snap.get("hosts") or []
        if args.host_id:
            hid = args.host_id.strip().lower()
            hosts = [h for h in hosts if str(h.get("host_id") or "").lower() == hid or str(h.get("hostname") or "").lower() == hid]
        fresh = hosts[0].get("freshness_status", "UNKNOWN") if hosts else "UNKNOWN"
        return self._wrap_evidence(status="OK", source_id="telemetry_collector", observed_at=snap.get("collected_at"), freshness=str(fresh), data={"overall_health": snap.get("overall_health"), "hosts": hosts})

    def _t_get_gpu_metrics(self, args: _HostArgs, principal: ZaloPrincipal) -> Dict[str, Any]:
        snap = self.telemetry.collect_snapshot()
        gpus = snap.get("gpus") or []
        if args.host_id:
            hid = args.host_id.strip().lower()
            gpus = [g for g in gpus if str(g.get("host_id") or "").lower() == hid]
        if not principal.is_admin:
            gpus = [{**g, "allocations": [a for a in (g.get("allocations") or []) if a.get("username") == principal.username]} for g in gpus]
        return self._wrap_evidence(status="OK", source_id="telemetry_collector:gpu", observed_at=snap.get("collected_at"), data={"gpus": gpus})

    def _t_get_service_health(self, args: _EmptyArgs, principal: ZaloPrincipal) -> Dict[str, Any]:
        snap = self.telemetry.collect_snapshot()
        return self._wrap_evidence(status="OK", source_id="telemetry_collector:services", observed_at=snap.get("collected_at"), data={"services": snap.get("services") or {}})

    def _t_get_storage_usage(self, args: _HostArgs, principal: ZaloPrincipal) -> Dict[str, Any]:
        snap = self.telemetry.collect_snapshot()
        hosts = snap.get("hosts") or []
        if args.host_id:
            hid = args.host_id.strip().lower()
            hosts = [h for h in hosts if str(h.get("host_id") or "").lower() == hid]
        storage = [{"host_id": h.get("host_id"), "hostname": h.get("hostname"), "status": h.get("status"), "disk_used_tb": h.get("disk_used_tb"), "disk_total_tb": h.get("disk_total_tb"), "disk_usage_percent": h.get("disk_usage_percent")} for h in hosts]
        return self._wrap_evidence(status="OK", source_id="telemetry_collector:storage", observed_at=snap.get("collected_at"), data={"storage": storage})

    def _t_get_metric_history(self, args: _HistoryArgs, principal: ZaloPrincipal) -> Dict[str, Any]:
        hist = self.telemetry.get_history_kpis(host_id=args.host_id, window_hours=args.window_hours)
        if int(hist.get("sample_count") or 0) == 0:
            return self._wrap_evidence(status="INSUFFICIENT_HISTORY", source_id="telemetry_history", freshness="MISSING", data=hist, error_code="NO_HISTORICAL_SAMPLES")
        return self._wrap_evidence(status="OK", source_id="telemetry_history", data=hist)

    def _t_compare_metrics(self, args: _CompareArgs, principal: ZaloPrincipal) -> Dict[str, Any]:
        snap = self.telemetry.collect_snapshot()
        hist = self.telemetry.get_history_kpis(host_id=args.host_id, window_hours=args.window_hours)
        if int(hist.get("sample_count") or 0) < 2:
            return self._wrap_evidence(
                status="INSUFFICIENT_HISTORY",
                source_id="telemetry_history",
                freshness="MISSING",
                observed_at=snap.get("collected_at"),
                error_code="INSUFFICIENT_HISTORY",
                data={"metric": args.metric, "current_snapshot": {"hosts": snap.get("hosts"), "gpus": snap.get("gpus")}, "message": "Chưa đủ mẫu lịch sử (sample_count < 2) để so sánh trung thực với kỳ trước."},
            )
        return self._wrap_evidence(
            status="OK",
            source_id="telemetry_history",
            observed_at=snap.get("collected_at"),
            data={"metric": args.metric, "history_kpis": hist, "current_hosts": snap.get("hosts"), "current_gpus": snap.get("gpus")},
        )

    def _t_list_incidents(self, args: _IncidentListArgs, principal: ZaloPrincipal) -> Dict[str, Any]:
        owner_f = None if principal.is_admin else principal.username
        items = self.incidents.list_incidents(status=args.status, severity=args.severity, owner_id=owner_f, limit=args.limit)
        return self._wrap_evidence(status="OK", source_id="incident_engine", data={"incidents": items})

    def _t_list_jobs(self, args: _EmptyArgs, principal: ZaloPrincipal) -> Dict[str, Any]:
        snap = self.telemetry.collect_snapshot()
        jobs = snap.get("jobs") or []
        if not principal.is_admin:
            jobs = [j for j in jobs if j.get("owner") == principal.username]
        return self._wrap_evidence(status="OK", source_id="telemetry_collector:jobs", observed_at=snap.get("collected_at"), data={"jobs": jobs[:50]})

    def _t_get_job(self, args: _IdArgs, principal: ZaloPrincipal) -> Dict[str, Any]:
        snap = self.telemetry.collect_snapshot()
        job = next((j for j in (snap.get("jobs") or []) if str(j.get("id") or "") == args.target_id), None)
        if job is None:
            return self._wrap_evidence(status="ERROR", source_id="jobs", error_code="JOB_NOT_FOUND")
        if not principal.is_admin and job.get("owner") != principal.username:
            raise PermissionError("Bạn không có quyền xem job của thành viên khác.")
        return self._wrap_evidence(status="OK", source_id="jobs", observed_at=snap.get("collected_at"), data={"job": job})

    def _t_get_container_stats(self, args: _ContainerStatsArgs, principal: ZaloPrincipal) -> Dict[str, Any]:
        snap = self.telemetry.collect_snapshot()
        workspaces = list(snap.get("workspaces") or [])
        if not principal.is_admin:
            authorized = [w for w in workspaces if str(w.get("owner") or "") == principal.username]
        else:
            authorized = workspaces

        if args.container_id is None:
            return self._wrap_evidence(status="OK", source_id="workspace_controller", observed_at=snap.get("collected_at"), data={"containers": authorized})

        target = args.container_id.strip()
        # Exact DB/controller ID or exact workspace name match ONLY — NEVER prefix match!
        matched = [
            w
            for w in workspaces
            if str(w.get("id") or "") == target or str(w.get("name") or "") == target or str(w.get("container_id") or "") == target
        ]
        if not matched:
            return self._wrap_evidence(status="ERROR", source_id="workspace_controller", error_code="CONTAINER_NOT_FOUND")
        if not principal.is_admin and str(matched[0].get("owner") or "") != principal.username:
            raise PermissionError("Không được phép xem thống kê container thuộc tài khoản khác.")
        return self._wrap_evidence(status="OK", source_id="workspace_controller", observed_at=snap.get("collected_at"), data={"container": matched[0]})

    def _t_get_gpu_allocations(self, args: _EmptyArgs, principal: ZaloPrincipal) -> Dict[str, Any]:
        try:
            from app.services.labops_service import service as core_svc

            groups = core_svc.get_gpu_allocations_by_device()
        except Exception:
            groups = []
        if not principal.is_admin:
            groups = [{**g, "accounts": [a for a in (g.get("accounts") or []) if a.get("username") == principal.username]} for g in groups]
        return self._wrap_evidence(status="OK", source_id="gpu_scheduler", data={"groups": groups})

    def _t_get_member_info(self, args: _MemberArgs, principal: ZaloPrincipal) -> Dict[str, Any]:
        target_user = (args.username or principal.username).strip()
        target_lower = target_user.lower()
        if not principal.is_admin and target_lower != principal.username.lower():
            raise PermissionError("Chỉ Admin được phép tra cứu hồ sơ thành viên khác.")
        m: Optional[Dict[str, Any]] = None
        source_id = "labops_service:members"
        try:
            from app.services.labops_service import service as core_svc

            m = next(
                (x for x in getattr(core_svc, "members", []) or [] if str(x.get("username") or "").strip().lower() == target_lower),
                None,
            )
        except Exception:
            m = None

        profile_meta: Dict[str, Any] = {}
        if hasattr(self, "db") and self.db is not None:
            try:
                with self.db.session_scope() as session:
                    rec = session.execute(
                        select(MemberProfileRecord).where(MemberProfileRecord.username == target_lower)
                    ).scalars().first()
                    if rec is not None:
                        sup_rec = (
                            session.get(MemberProfileRecord, rec.primary_supervisor_id)
                            if rec.primary_supervisor_id
                            else None
                        )
                        profile_meta = {
                            "username": rec.username,
                            "full_name": rec.full_name,
                            "email": rec.email,
                            "member_code": rec.member_code,
                            "member_category": rec.member_category,
                            "department_class": rec.department_class,
                            "supervisor_name": sup_rec.full_name if sup_rec else None,
                            "web_account_status": rec.web_account_status,
                            "ssh_access_state": rec.ssh_access_state,
                        }
                        if m is None:
                            source_id = "db:member_profiles"
                            m = {
                                "username": rec.username,
                                "full_name": rec.full_name,
                                "email": rec.email,
                                "role": rec.member_category or "RESEARCHER",
                                "status": rec.ssh_access_state or rec.web_account_status,
                            }
            except Exception:
                pass

        if m is None and (target_lower == "admin" or (target_lower == principal.username.lower() and principal.is_admin)):
            source_id = "identity_rbac:admin"
            m = {
                "username": target_user,
                "full_name": principal.display_name or "LabOps Administrator",
                "role": "ADMIN",
                "status": "ACTIVE",
                "assigned_server": "workstation",
                "workspace_name": "admin-control-plane",
                "ssh_port": None,
                "storage_quota_gb": None,
                "cpu_limit_cores": None,
                "ram_limit_gb": None,
                "gpu_permissions": ["FULL_ACCESS"],
            }

        recent_reqs: List[Dict[str, Any]] = []
        try:
            from app.services.mram_service import mram_service

            reqs = mram_service.list_requests_for_member(target_lower)
            recent_reqs = reqs[:5]
            if m is None and recent_reqs:
                source_id = "mram_service:requests"
                first_r = recent_reqs[0]
                m = {
                    "username": first_r.get("username") or target_lower,
                    "full_name": first_r.get("full_name") or target_user,
                    "role": first_r.get("member_category") or "STUDENT",
                    "status": first_r.get("status") or "PENDING_APPROVAL",
                }
        except Exception:
            recent_reqs = []

        if m is None:
            return self._wrap_evidence(status="ERROR", source_id="members", error_code="MEMBER_NOT_FOUND")
        safe = {
            "username": m.get("username") or profile_meta.get("username"),
            "full_name": m.get("full_name") or m.get("fullName") or profile_meta.get("full_name"),
            "email": m.get("email") or profile_meta.get("email"),
            "member_code": profile_meta.get("member_code"),
            "member_category": profile_meta.get("member_category"),
            "department_class": profile_meta.get("department_class"),
            "supervisor_name": profile_meta.get("supervisor_name"),
            "web_account_status": profile_meta.get("web_account_status"),
            "ssh_access_state": profile_meta.get("ssh_access_state"),
            "role": m.get("role"),
            "status": m.get("status"),
            "assigned_server": m.get("assigned_server"),
            "workspace_name": m.get("workspace_name"),
            "ssh_port": m.get("ssh_port"),
            "storage_quota_gb": m.get("storage_quota_gb"),
            "cpu_limit_cores": m.get("cpu_limit_cores"),
            "ram_limit_gb": m.get("ram_limit_gb"),
            "gpu_permissions": m.get("gpu_permissions"),
            "recent_ssh_requests": recent_reqs,
        }
        return self._wrap_evidence(status="OK", source_id=source_id, data={"member": safe})

    def _t_list_pending_requests(self, args: _EmptyArgs, principal: ZaloPrincipal) -> Dict[str, Any]:
        from app.services.mram_service import mram_service

        pending_reqs = mram_service.list_requests_for_admin(status_filter="PENDING_APPROVAL")
        need_info_reqs = mram_service.list_requests_for_admin(status_filter="NEED_INFO")
        all_pending = list(pending_reqs) + list(need_info_reqs)
        enriched: List[Dict[str, Any]] = []
        for r in all_pending[:15]:
            item = dict(r)
            rec = item.get("recommendation") or {}
            if not rec.get("recommended_pool_id"):
                try:
                    rec = mram_service.compute_resource_recommendation(
                        member_category=str(item.get("member_category") or "STUDENT"),
                        requested_duration_days=item.get("requested_duration_days"),
                        gpu_requested=bool(item.get("gpu_requested")),
                    )
                    item["recommendation"] = rec
                except Exception:
                    pass
            rat = rec.get("rationale") or {}
            item["supervisor_name"] = item.get("supervisor_name") or "Chưa khai báo"
            item["registration_reason"] = str(item.get("registration_reason") or "").strip()
            item["ssh_purpose"] = str(item.get("ssh_purpose") or "").strip()
            item["expected_tasks"] = str(item.get("expected_tasks") or "").strip()
            item["gpu_requested"] = bool(item.get("gpu_requested"))
            item["gpu_justification"] = str(item.get("gpu_justification") or "").strip()
            item["requested_duration_days"] = item.get("requested_duration_days")
            item["remaining_sla_hours"] = round(int(item.get("remaining_review_seconds") or 0) / 3600.0, 1)
            item["recommended_host"] = rec.get("recommended_host_id") or "workstation"
            item["recommended_pool_id"] = rec.get("recommended_pool_id")
            item["recommended_mount_point"] = rec.get("recommended_mount_point")
            item["recommended_free_gib"] = rat.get("chosen_pool_net_allocatable_gib")
            item["recommended_member_count"] = rat.get("chosen_pool_assigned_members")
            item["recommended_spec"] = (
                f"{rec.get('cpu_cores', 4)}C/{rec.get('ram_gb', 16)}G/"
                f"{rec.get('storage_quota_gb', 50)}GiB, GPU={rec.get('gpu_mode', 'NONE')}"
            )
            enriched.append(item)
        return self._wrap_evidence(
            status="OK",
            source_id="mram_service",
            data={"pending_count": len(all_pending), "requests": enriched},
        )

    def _t_get_request(self, args: _IdArgs, principal: ZaloPrincipal) -> Dict[str, Any]:
        from app.services.mram_service import mram_service

        detail = mram_service.get_request_detail(args.target_id, viewer_username=principal.username, is_admin=principal.is_admin)
        return self._wrap_evidence(status="OK", source_id="mram_service", data={"request": detail})

    def _t_get_group_usage(self, args: _EmptyArgs, principal: ZaloPrincipal) -> Dict[str, Any]:
        with self.db.session_scope() as session:
            if principal.is_admin:
                assigns = session.execute(select(SupervisorAssignmentRecord).where(SupervisorAssignmentRecord.is_active.is_(True))).scalars().all()
            else:
                prof = session.execute(select(MemberProfileRecord).where(MemberProfileRecord.username == principal.username)).scalars().first()
                if prof is None:
                    raise PermissionError("Bạn không có quyền xem dữ liệu nhóm hướng dẫn.")
                assigns = session.execute(
                    select(SupervisorAssignmentRecord).where(
                        SupervisorAssignmentRecord.supervisor_member_id == prof.member_id,
                        SupervisorAssignmentRecord.is_active.is_(True),
                    )
                ).scalars().all()
                if not assigns:
                    raise PermissionError("Tài khoản chưa được phân công nhóm sinh viên hướng dẫn hợp lệ.")
            student_ids = [a.student_member_id for a in assigns]
            students = (
                session.execute(select(MemberProfileRecord).where(MemberProfileRecord.member_id.in_(student_ids))).scalars().all()
                if student_ids
                else []
            )
            return self._wrap_evidence(
                status="OK",
                source_id="supervisor_assignments",
                data={"students": [{"username": s.username, "full_name": s.full_name, "ssh_access_state": s.ssh_access_state} for s in students]},
            )

    def _t_get_audit_trail(self, args: _LimitArgs, principal: ZaloPrincipal) -> Dict[str, Any]:
        with self.db.session_scope() as session:
            stmt = select(AuditEventRecord)
            if args.actor:
                stmt = stmt.where(AuditEventRecord.actor == args.actor)
            rows = session.execute(stmt.order_by(AuditEventRecord.timestamp.desc()).limit(args.limit)).scalars().all()
            return self._wrap_evidence(
                status="OK",
                source_id="audit_events",
                data={"events": [{"id": r.id, "timestamp": r.timestamp, "actor": r.actor, "action": r.action, "target_type": r.target_type, "target_id": r.target_id, "result": r.result, "details": redact_text(r.details)} for r in rows]},
            )

    def _t_get_vpn_status(self, args: _EmptyArgs, principal: ZaloPrincipal) -> Dict[str, Any]:
        try:
            from app.services.vpn_manager import vpn_manager

            overview = vpn_manager.get_overview() if hasattr(vpn_manager, "get_overview") else {"status": "AVAILABLE"}
        except Exception as exc:
            overview = {"status": "UNAVAILABLE", "error": str(exc)[:150]}
        return self._wrap_evidence(status="OK", source_id="vpn_manager", data=overview)

    def _t_search_redacted_logs(self, args: _LogSearchArgs, principal: ZaloPrincipal) -> Dict[str, Any]:
        path = self.log_paths.get(args.source)
        if not path or not os.path.isfile(path):
            return self._wrap_evidence(status="ERROR", source_id=f"logs:{args.source}", error_code="LOG_SOURCE_UNAVAILABLE")
        kw = args.keyword.lower()
        matches: List[str] = []
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                lines_tail = fh.readlines()[-2000:]
            for line in lines_tail:
                if kw in line.lower():
                    matches.append(redact_text(line.rstrip("\n"))[:300])
            matches = matches[-args.lines :]
        except Exception as exc:
            return self._wrap_evidence(status="ERROR", source_id=f"logs:{args.source}", error_code="LOG_READ_ERROR", data={"detail": str(exc)[:120]})
        return self._wrap_evidence(status="OK", source_id=f"logs:{args.source}", data={"matches": matches, "count": len(matches)})

    def _t_get_repository_scan(self, args: _RepoArgs, principal: ZaloPrincipal) -> Dict[str, Any]:
        try:
            from app.services.zalo_assistant.repo_alert_monitor import _fetch_agent_requests_sync

            reqs = _fetch_agent_requests_sync() or []
        except Exception as exc:
            return self._wrap_evidence(status="ERROR", source_id="repository_guard", error_code="AGENT_UNREACHABLE", data={"detail": str(exc)[:150]})
        if not principal.is_admin:
            reqs = [r for r in reqs if str(r.get("submitted_by") or "") == principal.username]
        if args.repo_filter:
            f = args.repo_filter.lower()
            reqs = [r for r in reqs if f in str(r.get("repo_url") or "").lower() or f in str(r.get("repo_name") or "").lower()]
        return self._wrap_evidence(status="OK", source_id="repository_guard", data={"repositories": reqs[:20]})

    def _t_search_kb(self, args: _QueryArgs, principal: ZaloPrincipal) -> Dict[str, Any]:
        q_terms = [w for w in args.query.lower().split() if w]
        with self.db.session_scope() as session:
            docs = session.execute(select(V4KnowledgeDocumentRecord).where(V4KnowledgeDocumentRecord.status == "ACTIVE")).scalars().all()
            scored = []
            for d in docs:
                if d.scope == "admin" and not principal.is_admin:
                    continue
                if d.scope not in {"shared", "admin"} and d.owner_id != principal.username and not principal.is_admin:
                    continue
                hay = f"{d.title}\n{d.content_markdown}\n{' '.join(d.tags_json or [])}".lower()
                score = sum(1 for t in q_terms if t in hay)
                if score > 0:
                    scored.append((score, d))
            scored.sort(key=lambda x: x[0], reverse=True)
            return self._wrap_evidence(
                status="OK",
                source_id="knowledge_base",
                data={"documents": [{"doc_id": d.doc_id, "title": d.title, "version": d.version, "snippet": d.content_markdown[:500]} for _, d in scored[:5]]},
            )

    def _t_get_kb_doc(self, args: _IdArgs, principal: ZaloPrincipal) -> Dict[str, Any]:
        with self.db.session_scope() as session:
            d = session.get(V4KnowledgeDocumentRecord, args.target_id)
            if d is None or d.status != "ACTIVE":
                return self._wrap_evidence(status="ERROR", source_id="knowledge_base", error_code="DOC_NOT_FOUND")
            if d.scope == "admin" and not principal.is_admin:
                raise PermissionError("Tài liệu nội bộ chỉ dành cho Admin.")
            if d.scope not in {"shared", "admin"} and d.owner_id != principal.username and not principal.is_admin:
                raise PermissionError("Bạn không có quyền truy cập tài liệu cá nhân của người khác.")
            return self._wrap_evidence(status="OK", source_id="knowledge_base", data={"doc_id": d.doc_id, "title": d.title, "version": d.version, "content": d.content_markdown})

    def _t_list_my_tasks(self, args: _EmptyArgs, principal: ZaloPrincipal) -> Dict[str, Any]:
        if self.task_service is None:
            return self._wrap_evidence(status="ERROR", source_id="task_service", error_code="TASK_SERVICE_UNAVAILABLE")
        items = self.task_service.list_tasks(owner_id=principal.username, is_admin=principal.is_admin)
        return self._wrap_evidence(status="OK", source_id="task_service", data={"tasks": items})

    def _t_propose_watch(self, args: _WatchProposalArgs, principal: ZaloPrincipal) -> Dict[str, Any]:
        return self._wrap_evidence(
            status="PROPOSAL_CREATED",
            source_id="task_service",
            data={"proposal_type": "watch_rule", "spec": args.model_dump(), "requires_confirmation": True, "confirm_hint": f"Dùng `/watch {args.target_id} {args.metric} {args.comparator} {args.threshold} {args.sustained_seconds}` để xác nhận tạo watch."},
        )

    def _t_propose_reminder(self, args: _ReminderProposalArgs, principal: ZaloPrincipal) -> Dict[str, Any]:
        return self._wrap_evidence(
            status="PROPOSAL_CREATED",
            source_id="task_service",
            data={"proposal_type": "one_time_reminder", "title": args.title, "in_minutes": args.in_minutes, "requires_confirmation": True},
        )

    def _t_propose_cancel(self, args: _ActionReasonArgs, principal: ZaloPrincipal) -> Dict[str, Any]:
        return self._wrap_evidence(
            status="PROPOSAL_CREATED",
            source_id="task_service",
            data={"proposal_type": "cancel_task", "task_id": args.target_id, "requires_confirmation": True, "confirm_hint": f"Dùng `/cancel {args.target_id}` để hủy task."},
        )

    def _t_propose_r2(self, action_type: str, target_type: str, args: _ActionReasonArgs, principal: ZaloPrincipal) -> Dict[str, Any]:
        if self.proposal_service is None:
            return self._wrap_evidence(status="ERROR", source_id="approval_service", error_code="APPROVAL_SERVICE_UNAVAILABLE")
        prop = self.proposal_service.propose_action(
            actor_id=principal.username,
            actor_role=principal.role,
            chat_id=principal.zalo_user_id or "dashboard",
            session_id="tool-proposal",
            action_type=action_type,
            target_type=target_type,
            target_id=args.target_id,
            canonical_args={"target_id": args.target_id, "reason": args.reason},
            reason=args.reason,
        )
        return self._wrap_evidence(status="PROPOSAL_CREATED", source_id="approval_service", data=prop)

    def _t_propose_r3(self, action_type: str, target_type: str, args: _ActionReasonArgs, principal: ZaloPrincipal) -> Dict[str, Any]:
        if self.proposal_service is None:
            return self._wrap_evidence(status="ERROR", source_id="approval_service", error_code="APPROVAL_SERVICE_UNAVAILABLE")
        prop = self.proposal_service.propose_action(
            actor_id=principal.username,
            actor_role=principal.role,
            chat_id=principal.zalo_user_id or "dashboard",
            session_id="tool-proposal",
            action_type=action_type,
            target_type=target_type,
            target_id=args.target_id,
            canonical_args={"target_id": args.target_id, "reason": args.reason},
            reason=args.reason,
        )
        return self._wrap_evidence(status="DASHBOARD_STEP_UP_REQUIRED", source_id="approval_service", data=prop)
