"""
LabOps Personal AI Agent V4 — Controlled Administration & ActionProposal State Machine.
- Risk classification:
    R0: Read-only
    R1: Own reminders / watches / preferences
    R2: Ack incident, resolve incident (requires recovery proof or override), reject SSH request
        -> Protected by cryptographically random one-time confirmation code (hashed in DB),
           bound strictly to (action_id, actor_id, chat_id, session_id, args_hash, target_version),
           120s expiry, max 3 attempts, NO broad YES/OK/CÓ approval.
    R3: Approve SSH request, grant/revoke GPU, quota changes, workspace restart
        -> ALWAYS routed to authenticated Dashboard Step-Up UI (cannot execute via Zalo confirm).
    FORBIDDEN: Arbitrary shell, SSH, sudo, account/workspace delete, network changes, self-grant.
- Feature flag `write_actions` defaults to False (no live mutations unless explicitly enabled).
"""
from __future__ import annotations

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import select, update

from app.db.models import (
    AuditEventRecord,
    IncidentRecord,
    ServerAccessRequestRecord,
    V4ActionProposalRecord,
)
from app.db.session import DatabaseManager
from app.services.assistant_v4.event_store import redact_text
from app.services.zalo_assistant.identity_rbac import ZaloPrincipal
from app.services.zalo_assistant.incident_engine import UnifiedIncidentEngine

R0_ACTIONS = {"read_metrics", "read_report"}
R1_ACTIONS = {"create_watch", "create_reminder", "cancel_task", "set_preference"}
R2_ACTIONS = {"ack_incident", "resolve_incident", "reject_request"}
R3_ACTIONS = {"approve_request", "grant_gpu", "revoke_gpu", "change_quota", "restart_workspace"}
FORBIDDEN_ACTIONS = {"arbitrary_shell", "remote_ssh", "sudo_cmd", "delete_member", "delete_workspace", "modify_network", "self_grant_admin"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return _utcnow()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _hash_secret_code(code: str) -> str:
    return hashlib.sha256(str(code or "").strip().upper().encode("utf-8")).hexdigest()


def _canonical_args_hash(args: Dict[str, Any]) -> str:
    canon = json.dumps(args or {}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def classify_action_risk(action_type: str) -> str:
    at = str(action_type or "").strip().lower()
    if at in FORBIDDEN_ACTIONS:
        return "FORBIDDEN"
    if at in R3_ACTIONS:
        return "R3"
    if at in R2_ACTIONS:
        return "R2"
    if at in R1_ACTIONS:
        return "R1"
    if at in R0_ACTIONS:
        return "R0"
    return "FORBIDDEN"


class ActionProposalService:
    def __init__(
        self,
        db_manager: DatabaseManager,
        *,
        write_actions_enabled: bool = False,
        incident_engine: Optional[UnifiedIncidentEngine] = None,
        identity_reloader: Optional[Callable[[ZaloPrincipal], ZaloPrincipal]] = None,
    ):
        self.db = db_manager
        self.write_actions_enabled = bool(write_actions_enabled)
        self.incident_engine = incident_engine or UnifiedIncidentEngine(db_manager.data_dir, db_manager=db_manager)
        self.identity_reloader = identity_reloader

    @staticmethod
    def _write_audit(
        session: Any,
        *,
        actor: str,
        action: str,
        target_type: str,
        target_id: str,
        status: str,
        correlation_id: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        detail_str = json.dumps(details or {}, ensure_ascii=False, sort_keys=True)
        session.add(
            AuditEventRecord(
                id=f"aud4-{uuid.uuid4().hex[:12]}",
                timestamp=_utcnow().isoformat(),
                actor=str(actor or "unknown")[:128],
                action=str(action)[:96],
                target_type=str(target_type or "UNKNOWN")[:64],
                target_id=str(target_id or "")[:255],
                result=str(status)[:48],
                correlation_id=str(correlation_id or "corr-v4")[:96],
                details=redact_text(detail_str)[:1500],
            )
        )

    def _audit_standalone(
        self,
        *,
        actor: str,
        action: str,
        target_type: str,
        target_id: str,
        status: str,
        correlation_id: str = "",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            with self.db.session_scope() as session:
                self._write_audit(
                    session,
                    actor=actor,
                    action=action,
                    target_type=target_type,
                    target_id=target_id,
                    status=status,
                    correlation_id=correlation_id,
                    details=details,
                )
        except Exception:
            pass

    @staticmethod
    def _serialize_proposal(r: V4ActionProposalRecord) -> Dict[str, Any]:
        return {
            "action_id": r.action_id,
            "actor_id": r.actor_id,
            "actor_role": r.actor_role,
            "action_type": r.action_type,
            "risk_tier": r.risk_tier,
            "target_type": r.target_type,
            "target_id": r.target_id,
            "state": r.state,
            "reason": r.reason,
            "expires_at": _as_utc(r.expires_at).isoformat(),
            "created_at": _as_utc(r.created_at).isoformat(),
            "error_detail": r.error_detail,
        }

    def get_proposal(self, action_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session_scope() as session:
            r = session.get(V4ActionProposalRecord, action_id)
            return self._serialize_proposal(r) if r is not None else None

    def _current_target_version_and_state(self, target_type: str, target_id: str) -> tuple[str, Dict[str, Any]]:
        with self.db.session_scope() as session:
            tt = target_type.upper()
            if tt == "INCIDENT":
                inc = session.get(IncidentRecord, target_id)
                if inc is None:
                    raise LookupError(f"Incident '{target_id}' not found")
                ver = f"{inc.status}:ep{int(inc.reopen_count or 0) + 1}:v{inc.occurrence_count}"
                return ver, {"status": inc.status, "owner_id": inc.owner_id, "severity": inc.severity}
            if tt == "SSH_REQUEST":
                req = session.get(ServerAccessRequestRecord, target_id)
                if req is None:
                    raise LookupError(f"Request '{target_id}' not found")
                return str(req.version), {"status": req.status, "username": req.username}
        return "1", {}

    def propose_action(
        self,
        *,
        actor_id: str,
        actor_role: str,
        chat_id: str,
        session_id: str,
        action_type: str,
        target_type: str,
        target_id: str,
        canonical_args: Optional[Dict[str, Any]] = None,
        reason: str = "",
        idempotency_key: Optional[str] = None,
        ttl_seconds: int = 120,
    ) -> Dict[str, Any]:
        risk = classify_action_risk(action_type)
        if risk == "FORBIDDEN":
            self._audit_standalone(
                actor=actor_id,
                action="V4_ACTION_DENIED",
                target_type=target_type.upper(),
                target_id=target_id,
                status="DENIED",
                details={"action_type": action_type, "reason": "FORBIDDEN_ACTION"},
            )
            raise PermissionError(f"FORBIDDEN_ACTION: Thao tác '{action_type}' bị cấm tuyệt đối.")
        if action_type in {"resolve_incident", "reject_request", "approve_request", "grant_gpu", "revoke_gpu", "change_quota", "restart_workspace"} and actor_role.upper() != "ADMIN":
            self._audit_standalone(
                actor=actor_id,
                action="V4_ACTION_DENIED",
                target_type=target_type.upper(),
                target_id=target_id,
                status="DENIED",
                details={"action_type": action_type, "reason": "ADMIN_REQUIRED"},
            )
            raise PermissionError("Chỉ Admin mới có quyền đề xuất thao tác quản trị này.")

        target_ver, target_info = self._current_target_version_and_state(target_type, target_id)
        if action_type == "ack_incident" and actor_role.upper() != "ADMIN" and target_info.get("owner_id") != actor_id:
            self._audit_standalone(
                actor=actor_id,
                action="V4_ACTION_DENIED",
                target_type=target_type.upper(),
                target_id=target_id,
                status="DENIED",
                details={"action_type": action_type, "reason": "NOT_INCIDENT_OWNER"},
            )
            raise PermissionError("Bạn không có quyền xác nhận sự cố của thành viên khác.")

        args_obj = dict(canonical_args or {"target_id": target_id, "reason": reason})
        args_hash = _canonical_args_hash(args_obj)
        now = _utcnow()
        exp = now + timedelta(seconds=max(30, min(int(ttl_seconds), 600)))
        aid = f"act4-{uuid.uuid4().hex[:10]}"
        idem = idempotency_key or f"prop:{actor_id}:{action_type}:{target_id}:{uuid.uuid4().hex[:8]}"

        one_time_code: Optional[str] = None
        nonce_hash: Optional[str] = None
        if risk in {"R1", "R2"}:
            one_time_code = f"{secrets.token_hex(3).upper()}"
            nonce_hash = _hash_secret_code(one_time_code)

        with self.db.session_scope() as session:
            rec = V4ActionProposalRecord(
                action_id=aid,
                actor_id=actor_id,
                actor_role=actor_role.upper(),
                session_id=session_id,
                chat_id=chat_id,
                action_type=action_type,
                risk_tier=risk,
                target_type=target_type.upper(),
                target_id=target_id,
                target_version=target_ver,
                canonical_args_json=args_obj,
                canonical_args_hash=args_hash,
                nonce_hash=nonce_hash,
                attempt_count=0,
                max_attempts=3,
                expires_at=exp,
                state="PENDING_CONFIRMATION",
                version=1,
                idempotency_key=idem,
                reason=redact_text(reason)[:300],
                created_at=now,
                updated_at=now,
            )
            session.add(rec)
            self._write_audit(
                session,
                actor=actor_id,
                action="V4_ACTION_PROPOSED",
                target_type=target_type.upper(),
                target_id=target_id,
                status="PROPOSED",
                correlation_id=aid,
                details={"action_id": aid, "action_type": action_type, "risk_tier": risk, "target_version": target_ver},
            )
            session.flush()

        out: Dict[str, Any] = {
            "action_id": aid,
            "action_type": action_type,
            "risk_tier": risk,
            "target_type": target_type.upper(),
            "target_id": target_id,
            "target_version": target_ver,
            "state": "PENDING_CONFIRMATION",
            "expires_at": exp.isoformat(),
        }
        if risk == "R3":
            out["dashboard_required"] = True
            out["dashboard_path"] = f"/dashboard/ssh-requests/{target_id}/review" if target_type.upper() == "SSH_REQUEST" else "/dashboard/ai-assistant"
            out["instruction"] = f"Thao tác {action_type} thuộc nhóm R3 bắt buộc xác thực Step-Up trên Dashboard ({out['dashboard_path']}); không thể xác nhận qua Zalo."
        else:
            out["confirmation_code"] = one_time_code
            out["instruction"] = f"Để xác nhận {action_type} trên {target_id}, hãy gửi đúng lệnh: `/confirm {aid} {one_time_code}` (hết hạn sau {ttl_seconds}s)."
        return out

    def confirm_zalo_action(
        self,
        *,
        action_id: str,
        code: str,
        principal: ZaloPrincipal,
        chat_id: str,
        session_id: Optional[str] = None,
        override_reason: str = "",
        observed_recovered: bool = False,
        now_override: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        # Group chats where chat_id != sender zalo_user_id are NEVER trusted for write confirmations
        if principal.zalo_user_id and chat_id and chat_id != principal.zalo_user_id:
            self._audit_standalone(
                actor=principal.username,
                action="V4_ACTION_DENIED",
                target_type="ACTION_PROPOSAL",
                target_id=action_id,
                status="DENIED",
                correlation_id=action_id,
                details={"reason": "GROUP_CHAT_FORBIDDEN", "chat_id": chat_id},
            )
            raise PermissionError("Không chấp nhận xác nhận lệnh ghi trong nhóm chat. Chỉ cho phép trong DM 1-1 đã xác thực.")

        # Re-verify principal server-side before checking confirmation
        eff_principal = self.identity_reloader(principal) if self.identity_reloader else principal
        if not eff_principal.is_authorized:
            self._audit_standalone(
                actor=principal.username,
                action="V4_ACTION_DENIED",
                target_type="ACTION_PROPOSAL",
                target_id=action_id,
                status="DENIED",
                correlation_id=action_id,
                details={"reason": "PRINCIPAL_REVOKED"},
            )
            raise PermissionError("Tài khoản đã bị thu hồi quyền hoặc chưa được ủy quyền.")

        norm_code = str(code or "").strip().upper()
        if norm_code in {"", "OK", "YES", "CÓ", "CO", "Y", "CONFIRM"}:
            self._audit_standalone(
                actor=eff_principal.username,
                action="V4_ACTION_DENIED",
                target_type="ACTION_PROPOSAL",
                target_id=action_id,
                status="DENIED",
                correlation_id=action_id,
                details={"reason": "GENERIC_CONFIRMATION_REJECTED"},
            )
            raise PermissionError("Mã xác nhận chung chung (OK/YES/CÓ) bị từ chối. Phải dùng đúng `/confirm <action_id> <code>`.")

        now = _as_utc(now_override)
        deny_exc: Optional[Exception] = None
        action_snapshot: Optional[Dict[str, Any]] = None
        replay_res: Optional[Dict[str, Any]] = None

        with self.db.session_scope() as session:
            prop = session.get(V4ActionProposalRecord, action_id)
            if prop is None:
                raise LookupError(f"Không tìm thấy đề xuất hành động '{action_id}'.")

            if prop.risk_tier == "R3" or prop.risk_tier == "FORBIDDEN":
                self._write_audit(session, actor=eff_principal.username, action="V4_ACTION_DENIED", target_type=prop.target_type, target_id=prop.target_id, status="DENIED", correlation_id=action_id, details={"reason": "R3_ZALO_BLOCKED", "action_id": action_id})
                deny_exc = PermissionError("Thao tác R3 bắt buộc xác thực Step-Up trên Dashboard POST; bị chặn trên Zalo.")
            elif prop.actor_id != eff_principal.username:
                self._write_audit(session, actor=eff_principal.username, action="V4_ACTION_DENIED", target_type=prop.target_type, target_id=prop.target_id, status="DENIED", correlation_id=action_id, details={"reason": "ACTOR_MISMATCH", "action_id": action_id})
                deny_exc = PermissionError("ACTOR_MISMATCH: Chỉ đúng người đề xuất mới được xác nhận.")
            elif prop.chat_id and chat_id and prop.chat_id != chat_id:
                self._write_audit(session, actor=eff_principal.username, action="V4_ACTION_DENIED", target_type=prop.target_type, target_id=prop.target_id, status="DENIED", correlation_id=action_id, details={"reason": "CHAT_CONTEXT_MISMATCH", "action_id": action_id})
                deny_exc = PermissionError("CHAT_CONTEXT_MISMATCH")
            elif session_id and prop.session_id and prop.session_id != session_id and prop.session_id != "tool-proposal":
                self._write_audit(session, actor=eff_principal.username, action="V4_ACTION_DENIED", target_type=prop.target_type, target_id=prop.target_id, status="DENIED", correlation_id=action_id, details={"reason": "SESSION_MISMATCH", "action_id": action_id})
                deny_exc = PermissionError("SESSION_MISMATCH")
            elif prop.state in {"SUCCEEDED", "EXECUTING"}:
                self._write_audit(session, actor=eff_principal.username, action="V4_ACTION_REPLAY", target_type=prop.target_type, target_id=prop.target_id, status="REPLAY", correlation_id=action_id, details={"action_id": action_id, "state": prop.state})
                replay_res = {"action_id": prop.action_id, "idempotent_replay": True, "state": prop.state, "result": prop.core_result_ref}
            elif prop.state != "PENDING_CONFIRMATION":
                self._write_audit(session, actor=eff_principal.username, action="V4_ACTION_DENIED", target_type=prop.target_type, target_id=prop.target_id, status="DENIED", correlation_id=action_id, details={"reason": f"INVALID_STATE:{prop.state}", "action_id": action_id})
                deny_exc = PermissionError(f"Đề xuất đang ở trạng thái '{prop.state}', không thể dùng lại.")
            elif now > _as_utc(prop.expires_at):
                prop.state = "EXPIRED"
                prop.version = int(prop.version) + 1
                prop.updated_at = now
                self._write_audit(session, actor=eff_principal.username, action="V4_ACTION_DENIED", target_type=prop.target_type, target_id=prop.target_id, status="EXPIRED", correlation_id=action_id, details={"action_id": action_id})
                deny_exc = PermissionError("EXPIRED: Mã xác nhận đã hết hạn.")
            elif _canonical_args_hash(prop.canonical_args_json) != prop.canonical_args_hash:
                prop.state = "REVOKED"
                prop.version = int(prop.version) + 1
                prop.error_detail = "CANONICAL_ARGS_TAMPERED"
                prop.updated_at = now
                self._write_audit(session, actor=eff_principal.username, action="V4_ACTION_DENIED", target_type=prop.target_type, target_id=prop.target_id, status="TAMPERED", correlation_id=action_id, details={"action_id": action_id})
                deny_exc = PermissionError("CANONICAL_ARGS_TAMPERED: Tham số đề xuất không khớp hash ban đầu.")
            elif prop.attempt_count >= prop.max_attempts:
                prop.state = "REJECTED"
                prop.version = int(prop.version) + 1
                prop.error_detail = "TOO_MANY_FAILED_ATTEMPTS"
                prop.updated_at = now
                self._write_audit(session, actor=eff_principal.username, action="V4_ACTION_DENIED", target_type=prop.target_type, target_id=prop.target_id, status="LOCKED", correlation_id=action_id, details={"action_id": action_id})
                deny_exc = PermissionError("Mã xác nhận bị khóa vì thử sai quá số lần cho phép.")
            elif not prop.nonce_hash or _hash_secret_code(norm_code) != prop.nonce_hash:
                prop.attempt_count = int(prop.attempt_count) + 1
                if prop.attempt_count >= prop.max_attempts:
                    prop.state = "REJECTED"
                    prop.error_detail = "TOO_MANY_FAILED_ATTEMPTS"
                prop.version = int(prop.version) + 1
                prop.updated_at = now
                self._write_audit(session, actor=eff_principal.username, action="V4_ACTION_DENIED", target_type=prop.target_type, target_id=prop.target_id, status="INVALID_CODE", correlation_id=action_id, details={"action_id": action_id, "attempts": prop.attempt_count})
                deny_exc = PermissionError("INVALID_CONFIRMATION_CODE")
            elif prop.action_type in {"resolve_incident", "reject_request"} and not eff_principal.is_admin:
                prop.state = "REVOKED"
                prop.version = int(prop.version) + 1
                prop.updated_at = now
                self._write_audit(session, actor=eff_principal.username, action="V4_ACTION_DENIED", target_type=prop.target_type, target_id=prop.target_id, status="REVOKED", correlation_id=action_id, details={"action_id": action_id})
                deny_exc = PermissionError("ROLE_REVOKED: Không còn quyền ADMIN.")
            else:
                cur_ver, _ = self._current_target_version_and_state(prop.target_type, prop.target_id)
                if cur_ver != prop.target_version:
                    prop.state = "EXPIRED"
                    prop.version = int(prop.version) + 1
                    prop.error_detail = f"TARGET_CHANGED: {prop.target_version} -> {cur_ver}"
                    prop.updated_at = now
                    self._write_audit(session, actor=eff_principal.username, action="V4_ACTION_DENIED", target_type=prop.target_type, target_id=prop.target_id, status="TARGET_CHANGED", correlation_id=action_id, details={"action_id": action_id, "from_ver": prop.target_version, "to_ver": cur_ver})
                    deny_exc = PermissionError("TARGET_STATE_CHANGED: Tài nguyên đã thay đổi sau khi đề xuất; vui lòng tạo đề xuất mới.")
                elif not self.write_actions_enabled:
                    deny_exc = RuntimeError("WRITE_ACTIONS_DISABLED: Feature flag `write_actions` đang tắt.")
                elif prop.action_type == "resolve_incident" and not observed_recovered and len(str(override_reason or prop.reason or "").strip()) < 5:
                    deny_exc = ValueError("RESOLVE_REQUIRES_RECOVERY_OR_REASON: Cần bằng chứng phục hồi hoặc lý do ghi nhận (>= 5 ký tự).")
                else:
                    expected_ver = int(prop.version)
                    cas_res = session.execute(
                        update(V4ActionProposalRecord)
                        .where(
                            V4ActionProposalRecord.action_id == action_id,
                            V4ActionProposalRecord.state == "PENDING_CONFIRMATION",
                            V4ActionProposalRecord.version == expected_ver,
                        )
                        .values(
                            state="CONFIRMED",
                            confirmed_at=now,
                            version=expected_ver + 1,
                            updated_at=now,
                        )
                    )
                    if getattr(cas_res, "rowcount", 0) != 1:
                        session.expire_all()
                        fresh = session.get(V4ActionProposalRecord, action_id)
                        if fresh is not None and fresh.state in {"SUCCEEDED", "EXECUTING", "CONFIRMED"}:
                            replay_res = {"action_id": action_id, "idempotent_replay": True, "state": fresh.state, "result": fresh.core_result_ref}
                        else:
                            deny_exc = PermissionError("CONCURRENT_CONFIRMATION_CONFLICT")
                    else:
                        self._write_audit(session, actor=eff_principal.username, action="V4_ACTION_CONFIRMED", target_type=prop.target_type, target_id=prop.target_id, status="CONFIRMED", correlation_id=action_id, details={"action_id": action_id, "action_type": prop.action_type})
                        action_snapshot = {
                            "action_id": prop.action_id,
                            "action_type": prop.action_type,
                            "target_id": prop.target_id,
                            "target_type": prop.target_type,
                            "args": dict(prop.canonical_args_json or {}),
                            "actor_id": eff_principal.username,
                            "actor_role": eff_principal.role,
                            "idempotency_key": prop.idempotency_key,
                            "confirmed_version": expected_ver + 1,
                        }

        if deny_exc is not None:
            raise deny_exc
        if replay_res is not None:
            return replay_res
        assert action_snapshot is not None
        return self._execute_confirmed(action_snapshot, override_note=override_reason)

    def confirm_dashboard_action(
        self,
        *,
        action_id: str,
        actor_username: str,
        actor_role: str,
        step_up_ok: bool,
        reason: str = "",
        now_override: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        if actor_role.upper() != "ADMIN":
            self._audit_standalone(
                actor=actor_username,
                action="V4_ACTION_DENIED",
                target_type="ACTION_PROPOSAL",
                target_id=action_id,
                status="DENIED",
                correlation_id=action_id,
                details={"reason": "ADMIN_REQUIRED"},
            )
            raise PermissionError("Chỉ Admin mới được phép thực thi qua Dashboard approval endpoint.")

        now = _as_utc(now_override)
        deny_exc: Optional[Exception] = None
        snapshot: Optional[Dict[str, Any]] = None
        replay_res: Optional[Dict[str, Any]] = None

        with self.db.session_scope() as session:
            prop = session.get(V4ActionProposalRecord, action_id)
            if prop is None:
                raise LookupError(f"Action proposal '{action_id}' not found")
            if prop.state == "SUCCEEDED":
                self._write_audit(session, actor=actor_username, action="V4_ACTION_REPLAY", target_type=prop.target_type, target_id=prop.target_id, status="REPLAY", correlation_id=action_id, details={"action_id": action_id})
                replay_res = {"action_id": action_id, "idempotent_replay": True, "state": "SUCCEEDED", "result": prop.core_result_ref}
            elif prop.state == "EXECUTING":
                replay_res = {"action_id": action_id, "idempotent_replay": True, "state": "EXECUTING", "result": prop.core_result_ref}
            elif prop.state != "PENDING_CONFIRMATION":
                self._write_audit(session, actor=actor_username, action="V4_ACTION_DENIED", target_type=prop.target_type, target_id=prop.target_id, status="DENIED", correlation_id=action_id, details={"reason": f"INVALID_STATE:{prop.state}", "action_id": action_id})
                deny_exc = PermissionError(f"Đề xuất đang ở trạng thái '{prop.state}', không thể xác nhận.")
            elif now > _as_utc(prop.expires_at):
                prop.state = "EXPIRED"
                prop.version = int(prop.version) + 1
                prop.updated_at = now
                self._write_audit(session, actor=actor_username, action="V4_ACTION_DENIED", target_type=prop.target_type, target_id=prop.target_id, status="EXPIRED", correlation_id=action_id, details={"action_id": action_id})
                deny_exc = PermissionError("EXPIRED: Đề xuất hành động đã hết hạn.")
            elif _canonical_args_hash(prop.canonical_args_json) != prop.canonical_args_hash:
                prop.state = "REVOKED"
                prop.version = int(prop.version) + 1
                prop.error_detail = "CANONICAL_ARGS_TAMPERED"
                prop.updated_at = now
                self._write_audit(session, actor=actor_username, action="V4_ACTION_DENIED", target_type=prop.target_type, target_id=prop.target_id, status="TAMPERED", correlation_id=action_id, details={"action_id": action_id})
                deny_exc = PermissionError("CANONICAL_ARGS_TAMPERED")
            elif prop.risk_tier == "R3" and not step_up_ok:
                self._write_audit(session, actor=actor_username, action="V4_ACTION_DENIED", target_type=prop.target_type, target_id=prop.target_id, status="DENIED", correlation_id=action_id, details={"reason": "STEP_UP_REQUIRED", "action_id": action_id})
                deny_exc = PermissionError("STEP_UP_REQUIRED: Thao tác R3 cần Step-Up token hợp lệ.")
            elif not self.write_actions_enabled:
                deny_exc = RuntimeError("WRITE_ACTIONS_DISABLED: Feature flag `write_actions` đang tắt.")
            else:
                cur_ver, _ = self._current_target_version_and_state(prop.target_type, prop.target_id)
                if cur_ver != prop.target_version:
                    prop.state = "EXPIRED"
                    prop.version = int(prop.version) + 1
                    prop.error_detail = f"TARGET_CHANGED: {prop.target_version} -> {cur_ver}"
                    prop.updated_at = now
                    self._write_audit(session, actor=actor_username, action="V4_ACTION_DENIED", target_type=prop.target_type, target_id=prop.target_id, status="TARGET_CHANGED", correlation_id=action_id, details={"action_id": action_id})
                    deny_exc = PermissionError("TARGET_STATE_CHANGED")
                else:
                    expected_ver = int(prop.version)
                    cas_res = session.execute(
                        update(V4ActionProposalRecord)
                        .where(
                            V4ActionProposalRecord.action_id == action_id,
                            V4ActionProposalRecord.state == "PENDING_CONFIRMATION",
                            V4ActionProposalRecord.version == expected_ver,
                        )
                        .values(
                            state="CONFIRMED",
                            confirmed_at=now,
                            version=expected_ver + 1,
                            updated_at=now,
                        )
                    )
                    if getattr(cas_res, "rowcount", 0) != 1:
                        session.expire_all()
                        fresh = session.get(V4ActionProposalRecord, action_id)
                        if fresh is not None and fresh.state in {"SUCCEEDED", "EXECUTING", "CONFIRMED"}:
                            replay_res = {"action_id": action_id, "idempotent_replay": True, "state": fresh.state, "result": fresh.core_result_ref}
                        else:
                            deny_exc = PermissionError("CONCURRENT_CONFIRMATION_CONFLICT")
                    else:
                        self._write_audit(session, actor=actor_username, action="V4_ACTION_CONFIRMED", target_type=prop.target_type, target_id=prop.target_id, status="CONFIRMED", correlation_id=action_id, details={"action_id": action_id, "action_type": prop.action_type, "channel": "dashboard"})
                        snapshot = {
                            "action_id": prop.action_id,
                            "action_type": prop.action_type,
                            "target_id": prop.target_id,
                            "target_type": prop.target_type,
                            "args": dict(prop.canonical_args_json or {}),
                            "actor_id": actor_username,
                            "actor_role": actor_role,
                            "idempotency_key": prop.idempotency_key,
                            "confirmed_version": expected_ver + 1,
                        }

        if deny_exc is not None:
            raise deny_exc
        if replay_res is not None:
            return replay_res
        assert snapshot is not None
        return self._execute_confirmed(snapshot, override_note=reason)

    def _execute_confirmed(self, snapshot: Dict[str, Any], *, override_note: str = "") -> Dict[str, Any]:
        aid = snapshot["action_id"]
        now = _utcnow()
        expected_ver = int(snapshot.get("confirmed_version") or 0)
        with self.db.session_scope() as session:
            prop = session.get(V4ActionProposalRecord, aid)
            if prop is None:
                raise LookupError("Proposal missing")
            if prop.state in {"SUCCEEDED", "EXECUTING"}:
                return {"action_id": aid, "idempotent_replay": True, "state": prop.state, "result": prop.core_result_ref}
            cur_ver = expected_ver if expected_ver > 0 else int(prop.version)
            cas_exec = session.execute(
                update(V4ActionProposalRecord)
                .where(
                    V4ActionProposalRecord.action_id == aid,
                    V4ActionProposalRecord.state == "CONFIRMED",
                    V4ActionProposalRecord.version == cur_ver,
                )
                .values(
                    state="EXECUTING",
                    version=cur_ver + 1,
                    updated_at=now,
                )
            )
            if getattr(cas_exec, "rowcount", 0) != 1:
                session.expire_all()
                fresh = session.get(V4ActionProposalRecord, aid)
                if fresh is not None:
                    return {"action_id": aid, "idempotent_replay": True, "state": fresh.state, "result": fresh.core_result_ref}
                raise PermissionError("CONCURRENT_EXECUTION_CONFLICT")

        try:
            atype = snapshot["action_type"]
            tid = snapshot["target_id"]
            actor = snapshot["actor_id"]
            role = snapshot["actor_role"]
            note = str(override_note or snapshot["args"].get("reason") or "Confirmed via V4 Approval")

            if atype == "ack_incident":
                res = self.incident_engine.acknowledge_incident(tid, actor=actor, role=role, note=note)
            elif atype == "resolve_incident":
                res = self.incident_engine.resolve_incident(tid, actor=actor, role=role, note=note)
            elif atype == "reject_request":
                from app.services.mram_service import mram_service

                res = mram_service.decide_ssh_request(
                    request_id=tid,
                    decision="REJECTED",
                    approver_username=actor,
                    reason=note if len(note) >= 5 else "Từ chối theo yêu cầu Admin",
                )
            elif atype == "approve_request":
                from app.services.mram_service import mram_service

                res = mram_service.decide_ssh_request(
                    request_id=tid,
                    decision="APPROVED",
                    approver_username=actor,
                    reason=note,
                )
            else:
                raise ValueError(f"Unsupported execution type: {atype}")

            ref = json.dumps({"status": "OK", "target_id": tid}, ensure_ascii=False)
            with self.db.session_scope() as session:
                prop = session.get(V4ActionProposalRecord, aid)
                if prop is not None:
                    prop.state = "SUCCEEDED"
                    prop.core_result_ref = ref
                    prop.executed_at = _utcnow()
                    prop.updated_at = _utcnow()
                self._write_audit(
                    session,
                    actor=actor,
                    action="V4_ACTION_EXECUTED",
                    target_type=snapshot["target_type"],
                    target_id=tid,
                    status="SUCCEEDED",
                    correlation_id=aid,
                    details={"action_id": aid, "action_type": atype},
                )
            return {"action_id": aid, "state": "SUCCEEDED", "result": res}
        except Exception as exc:
            err = redact_text(str(exc))[:300]
            with self.db.session_scope() as session:
                prop = session.get(V4ActionProposalRecord, aid)
                if prop is not None:
                    prop.state = "FAILED"
                    prop.error_detail = err
                    prop.updated_at = _utcnow()
                self._write_audit(
                    session,
                    actor=snapshot.get("actor_id", "unknown"),
                    action="V4_ACTION_FAILED",
                    target_type=snapshot.get("target_type", "UNKNOWN"),
                    target_id=snapshot.get("target_id", aid),
                    status="FAILED",
                    correlation_id=aid,
                    details={"action_id": aid, "error": err},
                )
            raise

    def revoke_actor_proposals(self, actor_ids: List[str], *, reason: str = "PRINCIPAL_REVOKED") -> int:
        """Revoke all open PROPOSED / PENDING_CONFIRMATION proposals belonging to a revoked actor."""
        clean_ids = sorted({str(aid).strip() for aid in actor_ids if str(aid or "").strip()})
        if not clean_ids:
            return 0
        now = _utcnow()
        revoked = 0
        with self.db.session_scope() as session:
            rows = (
                session.execute(
                    select(V4ActionProposalRecord).where(
                        V4ActionProposalRecord.actor_id.in_(clean_ids),
                        V4ActionProposalRecord.state.in_(["PROPOSED", "PENDING_CONFIRMATION"]),
                    )
                )
                .scalars()
                .all()
            )
            for prop in rows:
                prop.state = "REVOKED"
                prop.error_detail = redact_text(reason)[:200]
                prop.version = int(prop.version) + 1
                prop.updated_at = now
                self._write_audit(
                    session,
                    actor=prop.actor_id,
                    action="V4_ACTION_REVOKED",
                    target_type=prop.target_type,
                    target_id=prop.target_id,
                    status="REVOKED",
                    correlation_id=prop.action_id,
                    details={"action_id": prop.action_id, "reason": reason},
                )
                revoked += 1
        return revoked

    def reconcile_stuck_executing(
        self,
        *,
        stale_after_seconds: int = 120,
        now_override: Optional[datetime] = None,
    ) -> Dict[str, int]:
        """
        Crash recovery for proposals stranded in EXECUTING after a worker/backend crash.
        Inspects authoritative core target state WITHOUT blindly re-executing the mutation.
        """
        now = _as_utc(now_override)
        cutoff = now - timedelta(seconds=max(10, int(stale_after_seconds)))
        reconciled_succeeded = 0
        marked_unknown = 0

        with self.db.session_scope() as session:
            stuck_rows = (
                session.execute(
                    select(V4ActionProposalRecord).where(
                        V4ActionProposalRecord.state == "EXECUTING",
                        V4ActionProposalRecord.updated_at <= cutoff,
                    )
                )
                .scalars()
                .all()
            )
            for prop in stuck_rows:
                target_ok = False
                obs_status = "MISSING"
                tt = str(prop.target_type or "").upper()
                at = str(prop.action_type or "").lower()
                if tt == "INCIDENT":
                    inc = session.get(IncidentRecord, prop.target_id)
                    if inc is not None:
                        obs_status = str(inc.status or "").upper()
                        if at == "ack_incident" and obs_status in {"ACKNOWLEDGED", "RESOLVED"}:
                            target_ok = True
                        elif at == "resolve_incident" and obs_status == "RESOLVED":
                            target_ok = True
                elif tt == "SSH_REQUEST":
                    req = session.get(ServerAccessRequestRecord, prop.target_id)
                    if req is not None:
                        obs_status = str(req.status or "").upper()
                        if at == "reject_request" and obs_status == "REJECTED":
                            target_ok = True
                        elif at == "approve_request" and obs_status in {"APPROVED", "PROVISIONING", "COMPLETED"}:
                            target_ok = True

                old_ver = int(prop.version)
                if target_ok:
                    prop.state = "SUCCEEDED"
                    prop.core_result_ref = json.dumps(
                        {"status": "OK", "reconciled_from_crash": True, "observed_target_status": obs_status},
                        ensure_ascii=False,
                    )
                    prop.executed_at = prop.executed_at or now
                    reconciled_succeeded += 1
                    res_label = "SUCCEEDED"
                else:
                    prop.state = "UNKNOWN_OUTCOME"
                    prop.error_detail = f"CRASH_RECOVERY_AMBIGUOUS (target_status={obs_status})"
                    marked_unknown += 1
                    res_label = "UNKNOWN_OUTCOME"
                prop.version = old_ver + 1
                prop.updated_at = now
                self._write_audit(
                    session,
                    actor=prop.actor_id,
                    action="V4_ACTION_CRASH_RECONCILED",
                    target_type=prop.target_type,
                    target_id=prop.target_id,
                    status=res_label,
                    correlation_id=prop.action_id,
                    details={"action_id": prop.action_id, "action_type": prop.action_type, "observed_target_status": obs_status},
                )
        return {"reconciled_succeeded": reconciled_succeeded, "marked_unknown": marked_unknown}

    def list_proposals(self, *, actor_id: Optional[str] = None, is_admin: bool = False, limit: int = 50) -> list[Dict[str, Any]]:
        with self.db.session_scope() as session:
            stmt = select(V4ActionProposalRecord)
            if not is_admin:
                stmt = stmt.where(V4ActionProposalRecord.actor_id == actor_id)
            rows = session.execute(stmt.order_by(V4ActionProposalRecord.created_at.desc()).limit(limit)).scalars().all()
            return [self._serialize_proposal(r) for r in rows]
