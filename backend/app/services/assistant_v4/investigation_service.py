"""
LabOps Personal AI Agent V4 — Evidence-Grounded Autonomous Investigation (RCA).
- Evaluates incident transitions using reviewed versioned runbooks + read-only evidence tools.
- Never fabricates causes (returns verified_root_cause=None when evidence is incomplete).
- Deduplicates per episode_key + cooldown; records human review feedback without self-editing rules.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app.db.models import IncidentRecord, V4InvestigationRecord
from app.db.session import DatabaseManager
from app.services.assistant_v4.config import InvestigationBudgetConfig
from app.services.assistant_v4.secure_tools import SecureToolExecutor
from app.services.zalo_assistant.identity_rbac import ZaloPrincipal


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return _utcnow()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


REVIEWED_RUNBOOKS: Dict[str, Dict[str, Any]] = {
    "rb-node-unreachable-v1": {
        "version": 1,
        "event_types": {"NODE_HEARTBEAT_LOST", "NODE_HEARTBEAT_STALE"},
        "title": "Điều tra mất kết nối / stale heartbeat nút máy chủ",
        "required_tools": ["get_node_status", "get_metric_history", "get_service_health"],
        "never_infer": ["Mất điện vật lý nếu không có bằng chứng nguồn điện"],
    },
    "rb-gpu-overheat-v1": {
        "version": 1,
        "event_types": {"GPU_THERMAL_HIGH", "GPU_HARDWARE_FAULT"},
        "title": "Điều tra quá nhiệt hoặc lỗi phần cứng GPU",
        "required_tools": ["get_gpu_metrics", "get_metric_history", "list_my_jobs"],
    },
    "rb-storage-pressure-v1": {
        "version": 1,
        "event_types": {"STORAGE_HIGH_USAGE", "STORAGE_CRITICAL", "FILESYSTEM_ERROR"},
        "title": "Điều tra áp lực dung lượng đĩa / lỗi filesystem",
        "required_tools": ["get_storage_usage", "get_metric_history", "get_container_stats"],
    },
    "rb-docker-oom-v1": {
        "version": 1,
        "event_types": {"DOCKER_OOM_KILLED", "DOCKER_RESTART_LOOP"},
        "title": "Điều tra container OOMKilled / khởi động lại liên tiếp",
        "required_tools": ["get_container_stats", "list_my_jobs"],
    },
    "rb-repo-scan-failed-v1": {
        "version": 1,
        "event_types": {"REPO_SCAN_FAILED", "REPO_SECURITY_FINDINGS"},
        "title": "Điều tra cảnh báo hoặc lỗi quét Repository Guard",
        "required_tools": ["get_repository_scan", "get_service_health"],
    },
}


class InvestigationService:
    def __init__(
        self,
        db_manager: DatabaseManager,
        *,
        tool_executor: SecureToolExecutor,
        cfg: Optional[InvestigationBudgetConfig] = None,
        ai_client: Optional[Any] = None,
    ):
        self.db = db_manager
        self.tools = tool_executor
        self.cfg = cfg or InvestigationBudgetConfig()
        self.ai_client = ai_client

    @staticmethod
    def select_runbook(event_type: str) -> str:
        et = str(event_type or "").upper()
        for rb_id, spec in REVIEWED_RUNBOOKS.items():
            if et in spec["event_types"]:
                return rb_id
        return "rb-node-unreachable-v1"

    def investigate_incident(
        self,
        incident_id: str,
        *,
        actor_principal: Optional[ZaloPrincipal] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        sys_admin = actor_principal or ZaloPrincipal("", "admin", "mem-admin", "ADMIN", "SRE Investigation", True, True)
        if not sys_admin.is_admin:
            raise PermissionError("Investigation pipeline requires ADMIN scope")

        with self.db.session_scope() as session:
            inc = session.get(IncidentRecord, incident_id)
            if inc is None:
                raise LookupError(f"Incident '{incident_id}' not found")
            ep = max(1, int(inc.reopen_count or 0) + 1)
            ep_key = f"{incident_id}:ep{ep}"

            existing = session.execute(
                select(V4InvestigationRecord).where(V4InvestigationRecord.episode_key == ep_key)
            ).scalar_one_or_none()
            if existing is not None and not force:
                cooldown_until = _as_utc(existing.completed_at) + timedelta(minutes=self.cfg.episode_cooldown_minutes)
                if _utcnow() < cooldown_until:
                    return {**self._serialize(existing), "deduplicated": True}

            inc_snapshot = {
                "incident_id": inc.incident_id,
                "event_type": inc.event_type,
                "severity": inc.severity,
                "status": inc.status,
                "source": inc.source,
                "resource_id": inc.resource_id,
                "host_id": inc.host_id,
                "owner_id": inc.owner_id,
                "title": inc.title,
                "evidence": dict(inc.evidence_json or {}),
                "recommended_action": inc.recommended_action,
            }

        rb_id = self.select_runbook(inc_snapshot["event_type"])
        rb = REVIEWED_RUNBOOKS.get(rb_id, {})
        tool_list = list(rb.get("required_tools") or ["get_node_status"])[: self.cfg.tool_limit]

        facts: List[Dict[str, Any]] = []
        evidence_refs: List[str] = []
        missing_info: List[str] = []
        tool_calls_used = 0
        collected_data: Dict[str, Any] = {}

        for tname in tool_list:
            if tool_calls_used >= self.cfg.tool_limit:
                break
            args: Dict[str, Any] = {}
            if tname in {"get_node_status", "get_gpu_metrics", "get_storage_usage", "get_metric_history"} and inc_snapshot.get("host_id"):
                args["host_id"] = inc_snapshot["host_id"]
            elif tname == "get_container_stats" and inc_snapshot.get("resource_id") and str(inc_snapshot["source"]) == "docker":
                args["container_id"] = inc_snapshot["resource_id"]

            out = self.tools.execute_tool(tname, args, sys_admin, correlation_id=f"inv-{incident_id}", source="investigation")
            tool_calls_used += 1
            ev_ref = str(out.get("evidence_ref") or f"{tname}:{tool_calls_used}")
            evidence_refs.append(ev_ref)
            collected_data[tname] = out
            facts.append(
                {
                    "tool": tname,
                    "status": out.get("status"),
                    "freshness": out.get("freshness"),
                    "observed_at": out.get("observed_at"),
                    "evidence_ref": ev_ref,
                }
            )
            if out.get("status") in {"INSUFFICIENT_HISTORY", "ERROR"} or out.get("freshness") in {"MISSING", "OFFLINE", "UNKNOWN", "STALE"}:
                missing_info.append(f"{tname}:{out.get('error_code') or out.get('freshness')}")

        # Deterministic hypothesis evaluation (never guesses verified cause without solid evidence)
        hypotheses, verified_root_cause, recs = self._evaluate_hypotheses(inc_snapshot, collected_data, evidence_refs, missing_info)

        summary = (
            f"[{inc_snapshot['severity']}] {inc_snapshot['title']} — "
            + (f"Nguyên nhân xác minh: {verified_root_cause}" if verified_root_cause else "Chưa đủ bằng chứng kết luận root cause; liệt kê giả thuyết cần kiểm tra.")
        )

        now = _utcnow()
        run_id = f"inv4-{uuid.uuid4().hex[:12]}"
        with self.db.session_scope() as session:
            existing = session.execute(
                select(V4InvestigationRecord).where(V4InvestigationRecord.episode_key == ep_key)
            ).scalar_one_or_none()
            if existing is None:
                rec = V4InvestigationRecord(
                    run_id=run_id,
                    incident_id=incident_id,
                    episode_key=ep_key,
                    runbook_id=rb_id,
                    state="COMPLETED",
                    summary=summary,
                    facts_json=facts,
                    evidence_refs_json=evidence_refs,
                    hypotheses_json=hypotheses,
                    verified_root_cause=verified_root_cause,
                    missing_information_json=missing_info,
                    recommendations_json=recs,
                    model_route="deterministic",
                    model_requests_used=0,
                    tool_calls_used=tool_calls_used,
                    started_at=now,
                    completed_at=now,
                )
                session.add(rec)
                session.flush()
                return self._serialize(rec)
            else:
                existing.summary = summary
                existing.facts_json = facts
                existing.evidence_refs_json = evidence_refs
                existing.hypotheses_json = hypotheses
                existing.verified_root_cause = verified_root_cause
                existing.missing_information_json = missing_info
                existing.recommendations_json = recs
                existing.tool_calls_used += tool_calls_used
                existing.completed_at = now
                session.flush()
                return self._serialize(existing)

    @staticmethod
    def _evaluate_hypotheses(
        inc: Dict[str, Any],
        collected: Dict[str, Any],
        ev_refs: List[str],
        missing: List[str],
    ) -> tuple[List[Dict[str, Any]], Optional[str], List[Dict[str, Any]]]:
        et = str(inc.get("event_type") or "").upper()
        hypotheses: List[Dict[str, Any]] = []
        verified: Optional[str] = None
        recs: List[Dict[str, Any]] = []

        if et in {"NODE_HEARTBEAT_LOST", "NODE_HEARTBEAT_STALE"}:
            node_out = (collected.get("get_node_status") or {}).get("data") or {}
            hosts = node_out.get("hosts") or []
            h_status = hosts[0].get("freshness_status") if hosts else "UNKNOWN"
            # Without physical IPMI/PDU proof, NEVER claim power loss — report UNKNOWN telemetry timeout!
            hypotheses.append(
                {
                    "claim": "Node agent mất kết nối gRPC/VPN hoặc máy chủ bị tắt/quá tải nặng.",
                    "evidence_for": [f"host status={h_status}", f"last_heartbeat={hosts[0].get('last_heartbeat_at') if hosts else 'N/A'}"],
                    "evidence_against": ["Không có cảm biến IPMI/PDU chứng minh mất nguồn vật lý"],
                    "status": "PLAUSIBLE_UNVERIFIED",
                }
            )
            verified = None
            missing.append("Không có telemetry IPMI/PDU hoặc traceroute từ bên ngoài để phân biệt lỗi mạng vs mất điện.")
            recs.append({"action": "Kiểm tra ping/WireGuard/systemd labops-agent trên node", "risk": "R0 (Read-only check)"})

        elif et == "GPU_THERMAL_HIGH":
            gpu_out = (collected.get("get_gpu_metrics") or {}).get("data") or {}
            gpus = gpu_out.get("gpus") or []
            jobs_out = ((collected.get("list_my_jobs") or {}).get("data") or {}).get("jobs") or []
            hot_gpus = [g for g in gpus if g.get("host_online") and g.get("temp_celsius") is not None and float(g["temp_celsius"]) >= 85.0]
            active_gpu_load = [g for g in hot_gpus if float(g.get("utilization_gpu") or 0) >= 80.0]
            if active_gpu_load and jobs_out:
                g0 = active_gpu_load[0]
                verified = f"GPU {g0.get('gpu_key')} đạt {g0.get('temp_celsius')}°C do tải tính toán kéo dài ({g0.get('utilization_gpu')}%, {len(jobs_out)} jobs đang chạy)."
                hypotheses.append({"claim": verified, "evidence_for": ev_refs[:2], "evidence_against": [], "status": "VERIFIED"})
            else:
                verified = None
                missing.append("Thiếu mẫu telemetry GPU ONLINE hoặc không phát hiện job tải cao tương ứng.")
                hypotheses.append({"claim": "GPU nóng do lưu thông khí hoặc tiến trình nền ngoài danh sách job.", "evidence_for": [], "evidence_against": ["Chưa xác minh tiến trình tải GPU trực tiếp"], "status": "INSUFFICIENT_EVIDENCE"})
            recs.append({"action": "Giảm tải GPU hoặc kiểm tra quạt tản nhiệt", "risk": "R1/R3 (Tác động job đang chạy)"})

        elif et in {"DOCKER_OOM_KILLED", "DOCKER_RESTART_LOOP"}:
            c_data = ((collected.get("get_container_stats") or {}).get("data") or {}).get("container") or {}
            if c_data.get("oom_killed"):
                verified = f"Container {c_data.get('name')} bị cgroup OOMKilled với giới hạn RAM {c_data.get('ram_mb_limit')} MB."
                hypotheses.append({"claim": verified, "evidence_for": ev_refs[:1], "evidence_against": [], "status": "VERIFIED"})
            else:
                hypotheses.append({"claim": "Container lỗi khởi động hoặc thiếu RAM.", "evidence_for": [], "evidence_against": ["oom_killed=False trên telemetry hiện tại"], "status": "UNVERIFIED"})
            recs.append({"action": "Giảm batch_size / num_workers trước khi tăng RAM limit", "risk": "R0"})

        else:
            hypotheses.append({"claim": f"Sự cố {et} ghi nhận trên {inc.get('resource_id')}.", "evidence_for": ev_refs, "evidence_against": [], "status": "UNDER_INVESTIGATION"})
            recs.append({"action": inc.get("recommended_action") or "Kiểm tra trực tiếp trên Dashboard", "risk": "R0"})

        return hypotheses, verified, recs

    def submit_feedback(self, run_id: str, *, reviewer: str, feedback: str) -> Dict[str, Any]:
        with self.db.session_scope() as session:
            rec = session.get(V4InvestigationRecord, run_id)
            if rec is None:
                raise LookupError(f"Investigation '{run_id}' not found")
            rec.reviewed_by = reviewer
            rec.review_feedback = feedback[:1000]
            session.flush()
            return self._serialize(rec)

    def list_investigations(self, *, incident_id: Optional[str] = None, limit: int = 30) -> List[Dict[str, Any]]:
        with self.db.session_scope() as session:
            stmt = select(V4InvestigationRecord)
            if incident_id:
                stmt = stmt.where(V4InvestigationRecord.incident_id == incident_id)
            rows = session.execute(stmt.order_by(V4InvestigationRecord.started_at.desc()).limit(limit)).scalars().all()
            return [self._serialize(r) for r in rows]

    def get_investigation(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session_scope() as session:
            row = session.get(V4InvestigationRecord, run_id)
            return self._serialize(row) if row else None

    @staticmethod
    def _serialize(r: V4InvestigationRecord) -> Dict[str, Any]:
        return {
            "run_id": r.run_id,
            "incident_id": r.incident_id,
            "episode_key": r.episode_key,
            "runbook_id": r.runbook_id,
            "state": r.state,
            "summary": r.summary,
            "facts": list(r.facts_json or []),
            "evidence_refs": list(r.evidence_refs_json or []),
            "hypotheses": list(r.hypotheses_json or []),
            "verified_root_cause": r.verified_root_cause,
            "missing_information": list(r.missing_information_json or []),
            "recommendations": list(r.recommendations_json or []),
            "model_route": r.model_route,
            "model_requests_used": int(r.model_requests_used or 0),
            "tool_calls_used": int(r.tool_calls_used or 0),
            "reviewed_by": r.reviewed_by,
            "review_feedback": r.review_feedback,
            "started_at": _as_utc(r.started_at).isoformat(),
            "completed_at": _as_utc(r.completed_at).isoformat() if r.completed_at else None,
        }
