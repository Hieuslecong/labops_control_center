"""
Zalo Identity & LabOps RBAC Linking Service
Enforces verified binding between Zalo User IDs and LabOps Member Accounts
via short-lived one-time verification codes (preventing identity spoofing).
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update

from app.db.models import NotificationSubscriptionRecord, ZaloIdentityLinkRecord
from app.db.session import DatabaseManager

DAILY_BRIEF_TARGET_ZALO_ID = "0c00ed813eccd7928edd"
DAILY_BRIEF_TARGET_DISPLAY_NAME = "Hiếu Lê"
DAILY_BRIEF_TARGET_ROLE = "ADMIN"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _hash_code(code: str) -> str:
    norm = str(code or "").strip().upper()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


@dataclass
class ZaloPrincipal:
    zalo_user_id: str
    username: str
    member_id: str
    role: str  # ADMIN | RESEARCHER | STUDENT | ANONYMOUS
    display_name: str
    is_authorized: bool
    is_linked: bool

    @property
    def is_admin(self) -> bool:
        return self.role.upper() == "ADMIN" and self.is_authorized


class ZaloIdentityRBACService:
    def __init__(self, data_dir: str, db_manager: Optional[DatabaseManager] = None):
        self.data_dir = os.path.abspath(data_dir)
        self.db = db_manager or DatabaseManager(self.data_dir)
        self._daily_cfg_path = os.path.join(self.data_dir, "daily_brief_config.json")
        self._daily_cfg_lock = threading.Lock()

    def _default_daily_cfg(self) -> Dict[str, Any]:
        return {
            "enabled": True,
            "schedule_time": "08:00",
            "timezone": "Asia/Ho_Chi_Minh",
            "catchup_hours": 4,
            "target_zalo_id": DAILY_BRIEF_TARGET_ZALO_ID,
            "target_display_name": DAILY_BRIEF_TARGET_DISPLAY_NAME,
            "target_role": DAILY_BRIEF_TARGET_ROLE,
            "observed_private_chats": {},
        }

    def _load_daily_cfg(self) -> Dict[str, Any]:
        base = self._default_daily_cfg()
        if os.path.exists(self._daily_cfg_path):
            try:
                with open(self._daily_cfg_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    base.update(loaded)
                    if not isinstance(base.get("observed_private_chats"), dict):
                        base["observed_private_chats"] = {}
            except Exception:
                pass
        return base

    def _save_daily_cfg(self, cfg: Dict[str, Any]) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        tmp = f"{self._daily_cfg_path}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._daily_cfg_path)

    def get_daily_brief_config(self) -> Dict[str, Any]:
        with self._daily_cfg_lock:
            return self._load_daily_cfg()

    def update_daily_brief_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        with self._daily_cfg_lock:
            cfg = self._load_daily_cfg()
            if "enabled" in updates and updates["enabled"] is not None:
                cfg["enabled"] = bool(updates["enabled"])
            if "catchup_hours" in updates and updates["catchup_hours"] is not None:
                cfg["catchup_hours"] = max(1, min(int(updates["catchup_hours"]), 23))
            if "target_zalo_id" in updates and updates["target_zalo_id"]:
                tid = str(updates["target_zalo_id"]).strip()
                if tid == "*":
                    raise ValueError("Wildcard '*' is forbidden")
                cfg["target_zalo_id"] = tid
            if "target_display_name" in updates and updates["target_display_name"]:
                cfg["target_display_name"] = str(updates["target_display_name"]).strip()[:120]
            self._save_daily_cfg(cfg)
            return cfg

    def record_observed_private_chat(self, zalo_user_id: str, chat_id: str, display_name: str = "") -> bool:
        """Record verified 1-1 DM interaction (excludes group chats where chat_id != sender_id)."""
        uid = str(zalo_user_id or "").strip()
        cid = str(chat_id or "").strip()
        if not uid or not cid or uid == "*" or cid == "*" or cid != uid:
            return False
        now_iso = _utcnow().isoformat()
        with self._daily_cfg_lock:
            cfg = self._load_daily_cfg()
            observed = cfg.setdefault("observed_private_chats", {})
            observed[uid] = {
                "chat_id": cid,
                "last_seen_at": now_iso,
                "observed_display_name": str(display_name or "").strip()[:120],
            }
            self._save_daily_cfg(cfg)
        return True

    def verify_daily_brief_recipient(self, target_zalo_id: Optional[str] = None) -> Dict[str, Any]:
        """Strictly verify recipient has ACTIVE ADMIN link + verified 1-1 DM chat_id (never trusts display_name or falls back)."""
        cfg = self.get_daily_brief_config()
        target_id = str(target_zalo_id or cfg.get("target_zalo_id") or DAILY_BRIEF_TARGET_ZALO_ID).strip()
        disp_name = str(cfg.get("target_display_name") or DAILY_BRIEF_TARGET_DISPLAY_NAME)

        active_admin_link = None
        if target_id and target_id != "*":
            with self.db.session_scope() as session:
                active_admin_link = session.execute(
                    select(ZaloIdentityLinkRecord)
                    .where(
                        ZaloIdentityLinkRecord.zalo_user_id == target_id,
                        ZaloIdentityLinkRecord.status == "ACTIVE",
                        ZaloIdentityLinkRecord.role == "ADMIN",
                    )
                    .order_by(ZaloIdentityLinkRecord.verified_at.desc())
                ).scalars().first()

        obs = (cfg.get("observed_private_chats") or {}).get(target_id)
        verified_chat_id = (
            str(obs.get("chat_id")).strip()
            if isinstance(obs, dict) and str(obs.get("chat_id") or "").strip() == target_id
            else ""
        )

        if active_admin_link is not None and verified_chat_id:
            return {
                "verified": True,
                "status": "VERIFIED",
                "zalo_user_id": target_id,
                "chat_id": verified_chat_id,
                "username": active_admin_link.username,
                "member_id": active_admin_link.member_id,
                "role": "ADMIN",
                "display_name": disp_name,
                "verified_at": _as_utc(active_admin_link.verified_at).isoformat() if active_admin_link.verified_at else None,
                "last_interaction_at": obs.get("last_seen_at") if isinstance(obs, dict) else None,
            }

        reasons: List[str] = []
        if active_admin_link is None:
            reasons.append("chưa có liên kết ACTIVE với vai trò ADMIN trong LabOps")
        if not verified_chat_id:
            reasons.append("chưa ghi nhận chat_id riêng 1-1 hợp lệ với Bot")

        return {
            "verified": False,
            "status": "RECIPIENT_NOT_VERIFIED",
            "zalo_user_id": target_id,
            "chat_id": None,
            "display_name": disp_name,
            "role": "ADMIN",
            "has_active_admin_link": active_admin_link is not None,
            "has_verified_private_chat": bool(verified_chat_id),
            "reason": "; ".join(reasons),
            "instruction": (
                f"Người nhận {disp_name} ({target_id}) {', '.join(reasons)}. "
                "Vui lòng tạo mã /link từ Dashboard → AI Assistant và gửi `/link <MÃ>` trong tin nhắn riêng 1-1 với Zalo Bot."
            ),
        }

    def bind_daily_brief_admin(
        self,
        *,
        zalo_user_id: Optional[str] = None,
        username: str = "admin",
        member_id: str = "mem-admin",
        admin_actor: str = "admin",
        verified_chat_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        cfg = self.get_daily_brief_config()
        target_id = str(zalo_user_id or cfg.get("target_zalo_id") or DAILY_BRIEF_TARGET_ZALO_ID).strip()
        disp_name = str(cfg.get("target_display_name") or DAILY_BRIEF_TARGET_DISPLAY_NAME)
        link_res = self.admin_bind_identity(
            zalo_user_id=target_id,
            username=username,
            member_id=member_id,
            role="ADMIN",
            display_name=disp_name,
            admin_actor=admin_actor,
        )
        if verified_chat_id and str(verified_chat_id).strip() == target_id:
            self.record_observed_private_chat(target_id, verified_chat_id, display_name=disp_name)
        return {
            "link": link_res,
            "verification": self.verify_daily_brief_recipient(target_id),
        }

    def ensure_configured_approver_verified(self, target_zalo_id: Optional[str] = None) -> Dict[str, Any]:
        """Auto-sync configured MRAM_APPROVER_ZALO_ID / authorized admin into ZaloIdentityLinkRecord when a 1-1 DM chat exists."""
        target_id = str(target_zalo_id or os.getenv("MRAM_APPROVER_ZALO_ID") or "").strip()
        cur = self.verify_daily_brief_recipient(target_id)
        if cur.get("verified") or not target_id or target_id == "*":
            return cur

        configured_approver = os.getenv("MRAM_APPROVER_ZALO_ID", "").strip()
        auth_users = {
            u.strip()
            for u in os.getenv("ZALO_AUTHORIZED_USERS", "").split(",")
            if u.strip() and u.strip() != "*"
        }
        if target_id != configured_approver and target_id not in auth_users:
            return cur

        cfg = self.get_daily_brief_config()
        obs = (cfg.get("observed_private_chats") or {}).get(target_id)
        if not isinstance(obs, dict) or str(obs.get("chat_id") or "").strip() != target_id:
            return cur

        disp = str(obs.get("observed_display_name") or cfg.get("target_display_name") or DAILY_BRIEF_TARGET_DISPLAY_NAME).strip()
        self.admin_bind_identity(
            zalo_user_id=target_id,
            username="admin",
            member_id="mem-admin",
            role="ADMIN",
            display_name=disp,
            admin_actor="system-approver-sync",
        )
        return self.verify_daily_brief_recipient(target_id)

    def generate_link_code(
        self,
        *,
        username: str,
        member_id: str,
        role: str,
        actor: str,
        display_name: str = "",
        ttl_minutes: int = 15,
    ) -> Dict[str, Any]:
        clean_user = str(username or "").strip()
        if not clean_user:
            raise ValueError("username is required")
        norm_role = str(role or "RESEARCHER").strip().upper()
        if norm_role not in {"ADMIN", "RESEARCHER", "STUDENT"}:
            norm_role = "RESEARCHER"

        raw_code = f"LAB-{secrets.token_hex(3).upper()}"
        link_id = f"zlink-{uuid.uuid4().hex[:12]}"
        expires = _utcnow() + timedelta(minutes=max(1, min(int(ttl_minutes), 120)))

        with self.db.session_scope() as session:
            session.add(
                ZaloIdentityLinkRecord(
                    id=link_id,
                    zalo_user_id=None,
                    member_id=str(member_id or ""),
                    username=clean_user,
                    role=norm_role,
                    display_name=str(display_name or clean_user),
                    status="PENDING_CODE",
                    link_code_hash=_hash_code(raw_code),
                    code_expires_at=expires,
                    linked_by=str(actor or "admin"),
                    created_at=_utcnow(),
                )
            )

        return {
            "link_id": link_id,
            "username": clean_user,
            "member_id": str(member_id or ""),
            "role": norm_role,
            "code": raw_code,
            "expires_at": expires.isoformat(),
            "instruction": f"Gửi lệnh '/link {raw_code}' tới Zalo Bot để hoàn tất xác thực danh tính.",
        }

    def claim_link_code(self, *, zalo_user_id: str, code: str, display_name: str = "") -> Dict[str, Any]:
        uid = str(zalo_user_id or "").strip()
        if not uid or uid == "*":
            return {"success": False, "error": "INVALID_ZALO_USER_ID", "message": "Zalo User ID không hợp lệ."}

        code_clean = str(code or "").strip().upper()
        if not code_clean:
            return {"success": False, "error": "EMPTY_CODE", "message": "Thiếu mã liên kết."}

        code_h = _hash_code(code_clean)
        now = _utcnow()

        with self.db.session_scope() as session:
            pending = session.execute(
                select(ZaloIdentityLinkRecord).where(
                    ZaloIdentityLinkRecord.link_code_hash == code_h,
                    ZaloIdentityLinkRecord.status == "PENDING_CODE",
                )
            ).scalar_one_or_none()

            if pending is None:
                return {
                    "success": False,
                    "error": "INVALID_LINK_CODE",
                    "message": "Mã liên kết không hợp lệ hoặc đã được sử dụng.",
                }

            exp = _as_utc(pending.code_expires_at)
            if exp is not None and exp < now:
                pending.status = "EXPIRED"
                return {
                    "success": False,
                    "error": "EXPIRED_LINK_CODE",
                    "message": "Mã liên kết đã hết hạn. Vui lòng tạo mã mới trên Dashboard.",
                }

            # Revoke previous active links for this Zalo User ID to prevent stale roles
            existing_active = session.execute(
                select(ZaloIdentityLinkRecord).where(
                    ZaloIdentityLinkRecord.zalo_user_id == uid,
                    ZaloIdentityLinkRecord.status == "ACTIVE",
                )
            ).scalars().all()
            for old in existing_active:
                old.status = "REVOKED"
                old.revoked_at = now

            pending.zalo_user_id = uid
            pending.status = "ACTIVE"
            pending.link_code_hash = None
            pending.verified_at = now
            if display_name:
                pending.display_name = display_name[:160]

            self._upsert_subscription_in_session(
                session,
                zalo_user_id=uid,
                member_id=pending.member_id,
                username=pending.username,
                role=pending.role,
            )

            return {
                "success": True,
                "link_id": pending.id,
                "zalo_user_id": uid,
                "username": pending.username,
                "member_id": pending.member_id,
                "role": pending.role,
                "verified_at": now.isoformat(),
            }

    def admin_bind_identity(
        self,
        *,
        zalo_user_id: str,
        username: str,
        member_id: str,
        role: str,
        display_name: str = "",
        admin_actor: str = "admin",
    ) -> Dict[str, Any]:
        uid = str(zalo_user_id or "").strip()
        if not uid or uid == "*":
            raise ValueError("Zalo User ID không hợp lệ")
        clean_user = str(username or "").strip()
        if not clean_user:
            raise ValueError("username không được rỗng")
        norm_role = str(role or "RESEARCHER").strip().upper()
        if norm_role not in {"ADMIN", "RESEARCHER", "STUDENT"}:
            norm_role = "RESEARCHER"

        now = _utcnow()
        link_id = f"zlink-{uuid.uuid4().hex[:12]}"
        with self.db.session_scope() as session:
            existing_active = session.execute(
                select(ZaloIdentityLinkRecord).where(
                    ZaloIdentityLinkRecord.zalo_user_id == uid,
                    ZaloIdentityLinkRecord.status == "ACTIVE",
                )
            ).scalars().all()
            for old in existing_active:
                old.status = "REVOKED"
                old.revoked_at = now

            rec = ZaloIdentityLinkRecord(
                id=link_id,
                zalo_user_id=uid,
                member_id=str(member_id or ""),
                username=clean_user,
                role=norm_role,
                display_name=str(display_name or clean_user),
                status="ACTIVE",
                link_code_hash=None,
                linked_by=str(admin_actor or "admin"),
                created_at=now,
                verified_at=now,
            )
            session.add(rec)
            self._upsert_subscription_in_session(
                session,
                zalo_user_id=uid,
                member_id=str(member_id or ""),
                username=clean_user,
                role=norm_role,
            )

        return {
            "id": link_id,
            "zalo_user_id": uid,
            "username": clean_user,
            "member_id": str(member_id or ""),
            "role": norm_role,
            "display_name": str(display_name or clean_user),
            "status": "ACTIVE",
            "verified_at": now.isoformat(),
        }

    def revoke_link(self, link_id_or_zalo_id: str, actor: str = "admin") -> bool:
        target = str(link_id_or_zalo_id or "").strip()
        if not target:
            return False
        now = _utcnow()
        changed = False
        revoked_zalo_ids = {target}
        revoked_usernames: set[str] = set()
        with self.db.session_scope() as session:
            rows = session.execute(
                select(ZaloIdentityLinkRecord).where(
                    (ZaloIdentityLinkRecord.id == target) | (ZaloIdentityLinkRecord.zalo_user_id == target)
                )
            ).scalars().all()
            for row in rows:
                if row.zalo_user_id:
                    revoked_zalo_ids.add(row.zalo_user_id)
                if row.username:
                    revoked_usernames.add(row.username)
                if row.status in ("ACTIVE", "PENDING_CODE"):
                    row.status = "REVOKED"
                    row.revoked_at = now
                    changed = True
            for zid in list(revoked_zalo_ids):
                sub = session.execute(
                    select(NotificationSubscriptionRecord).where(
                        NotificationSubscriptionRecord.recipient_zalo_id == zid
                    )
                ).scalar_one_or_none()
                if sub is not None:
                    sub.enabled = False
                    sub.receive_repo_alerts = False

            if changed:
                # Immediately cascade revocation into V4 sessions, tasks, watches, proposals, and unsent outbox
                try:
                    with session.begin_nested():
                        from app.db.models import (
                            AuditEventRecord,
                            V4ActionProposalRecord,
                            V4AssistantTaskRecord,
                            V4ChatSessionRecord,
                            V4NotificationOutboxRecord,
                            V4WatchRuleRecord,
                        )

                        active_unames = {
                            u
                            for u in session.execute(
                                select(ZaloIdentityLinkRecord.username).where(
                                    ZaloIdentityLinkRecord.username.in_(list(revoked_usernames)),
                                    ZaloIdentityLinkRecord.status == "ACTIVE",
                                )
                            )
                            .scalars()
                            .all()
                            if u
                        } if revoked_usernames else set()
                        unlinked_unames = sorted(revoked_usernames - active_unames)
                        principal_ids = sorted(revoked_zalo_ids | set(unlinked_unames))

                        sess_res = session.execute(
                            update(V4ChatSessionRecord)
                            .where(
                                V4ChatSessionRecord.status == "ACTIVE",
                                (
                                    V4ChatSessionRecord.principal_id.in_(principal_ids)
                                    | V4ChatSessionRecord.chat_id.in_(list(revoked_zalo_ids))
                                ),
                            )
                            .values(status="REVOKED", summary_text="", last_active_at=now)
                        )
                        watch_res = session.execute(
                            update(V4WatchRuleRecord)
                            .where(
                                V4WatchRuleRecord.owner_id.in_(principal_ids),
                                V4WatchRuleRecord.status == "ACTIVE",
                            )
                            .values(status="CANCELLED", updated_at=now)
                        )
                        prop_res = session.execute(
                            update(V4ActionProposalRecord)
                            .where(
                                V4ActionProposalRecord.actor_id.in_(principal_ids),
                                V4ActionProposalRecord.state.in_(["PROPOSED", "PENDING_CONFIRMATION"]),
                            )
                            .values(state="REVOKED", error_detail="LINK_REVOKED", updated_at=now)
                        )
                        out_res = session.execute(
                            update(V4NotificationOutboxRecord)
                            .where(
                                V4NotificationOutboxRecord.recipient_id.in_(list(revoked_zalo_ids) + unlinked_unames),
                                V4NotificationOutboxRecord.status.in_(["PENDING", "FAILED_RETRYABLE"]),
                            )
                            .values(
                                status="SUPPRESSED",
                                last_error_class="PRINCIPAL_REVOKED",
                                last_error=f"Identity link revoked by {actor}"[:200],
                                updated_at=now,
                            )
                        )
                        session.add(
                            AuditEventRecord(
                                id=f"aud4-{uuid.uuid4().hex[:12]}",
                                timestamp=now.isoformat(),
                                actor=str(actor or "admin")[:128],
                                action="V4_PRINCIPAL_REVOKED",
                                target_type="ZALO_IDENTITY_LINK",
                                target_id=target[:255],
                                result="REVOKED",
                                correlation_id=f"rev-{uuid.uuid4().hex[:8]}",
                                details=json.dumps(
                                    {
                                        "revoked_zalo_ids": sorted(revoked_zalo_ids),
                                        "revoked_usernames": unlinked_unames,
                                        "sessions": int(getattr(sess_res, "rowcount", 0) or 0),
                                        "watches": int(getattr(watch_res, "rowcount", 0) or 0),
                                        "proposals": int(getattr(prop_res, "rowcount", 0) or 0),
                                        "outbox_suppressed": int(getattr(out_res, "rowcount", 0) or 0),
                                    },
                                    ensure_ascii=False,
                                ),
                            )
                        )
                except Exception:
                    pass
        return changed

    def list_links(self) -> List[Dict[str, Any]]:
        with self.db.session_scope() as session:
            rows = session.execute(
                select(ZaloIdentityLinkRecord).order_by(ZaloIdentityLinkRecord.created_at.desc())
            ).scalars().all()
            return [
                {
                    "id": r.id,
                    "zalo_user_id": r.zalo_user_id,
                    "member_id": r.member_id,
                    "username": r.username,
                    "role": r.role,
                    "display_name": r.display_name,
                    "status": r.status,
                    "linked_by": r.linked_by,
                    "created_at": _as_utc(r.created_at).isoformat() if r.created_at else None,
                    "verified_at": _as_utc(r.verified_at).isoformat() if r.verified_at else None,
                    "revoked_at": _as_utc(r.revoked_at).isoformat() if r.revoked_at else None,
                }
                for r in rows
            ]

    def _upsert_subscription_in_session(
        self,
        session: Any,
        *,
        zalo_user_id: str,
        member_id: str,
        username: str,
        role: str,
    ) -> NotificationSubscriptionRecord:
        is_admin = role.upper() == "ADMIN"
        sub = session.execute(
            select(NotificationSubscriptionRecord).where(
                NotificationSubscriptionRecord.recipient_zalo_id == zalo_user_id
            )
        ).scalar_one_or_none()
        if sub is None:
            sub = NotificationSubscriptionRecord(
                id=f"zsub-{uuid.uuid4().hex[:12]}",
                recipient_zalo_id=zalo_user_id,
                member_id=member_id,
                username=username,
                role=role.upper(),
                enabled=True,
                receive_p0=True,
                receive_p1=True,
                receive_daily_report=is_admin,
                receive_weekly_report=is_admin,
                receive_repo_alerts=is_admin,
                bypass_quiet_for_p0=True,
                updated_at=_utcnow(),
            )
            session.add(sub)
        else:
            sub.member_id = member_id
            sub.username = username
            sub.role = role.upper()
            sub.enabled = True
            if not is_admin:
                # Strict RBAC: Researchers cannot keep Admin-only alerts
                sub.receive_repo_alerts = False
            else:
                sub.receive_repo_alerts = True
                sub.receive_daily_report = True
            sub.updated_at = _utcnow()
        return sub

    def list_subscriptions(self) -> List[Dict[str, Any]]:
        with self.db.session_scope() as session:
            rows = session.execute(
                select(NotificationSubscriptionRecord).order_by(NotificationSubscriptionRecord.role.asc())
            ).scalars().all()
            return [
                {
                    "id": s.id,
                    "recipient_zalo_id": s.recipient_zalo_id,
                    "member_id": s.member_id,
                    "username": s.username,
                    "role": s.role,
                    "enabled": bool(s.enabled),
                    "receive_p0": bool(s.receive_p0),
                    "receive_p1": bool(s.receive_p1),
                    "receive_daily_report": bool(s.receive_daily_report),
                    "receive_weekly_report": bool(s.receive_weekly_report),
                    "receive_repo_alerts": bool(s.receive_repo_alerts) and s.role.upper() == "ADMIN",
                    "quiet_hours_start": s.quiet_hours_start,
                    "quiet_hours_end": s.quiet_hours_end,
                    "bypass_quiet_for_p0": bool(s.bypass_quiet_for_p0),
                    "updated_at": _as_utc(s.updated_at).isoformat() if s.updated_at else None,
                }
                for s in rows
            ]

    def update_subscription(self, recipient_zalo_id: str, updates: Dict[str, Any]) -> Dict[str, Any]:
        uid = str(recipient_zalo_id or "").strip()
        if not uid:
            raise ValueError("recipient_zalo_id is required")
        with self.db.session_scope() as session:
            sub = session.execute(
                select(NotificationSubscriptionRecord).where(
                    NotificationSubscriptionRecord.recipient_zalo_id == uid
                )
            ).scalar_one_or_none()
            if sub is None:
                principal = self.resolve_zalo_principal(uid)
                sub = self._upsert_subscription_in_session(
                    session,
                    zalo_user_id=uid,
                    member_id=principal.member_id,
                    username=principal.username,
                    role=principal.role if principal.role != "ANONYMOUS" else "RESEARCHER",
                )

            for field in (
                "enabled",
                "receive_p0",
                "receive_p1",
                "receive_daily_report",
                "receive_weekly_report",
            ):
                if field in updates and updates[field] is not None:
                    setattr(sub, field, bool(updates[field]))
            if "receive_repo_alerts" in updates and updates["receive_repo_alerts"] is not None:
                # Non-admin accounts NEVER subscribe to repo security alerts
                sub.receive_repo_alerts = bool(updates["receive_repo_alerts"]) and sub.role.upper() == "ADMIN"
            if "quiet_hours_start" in updates:
                sub.quiet_hours_start = str(updates["quiet_hours_start"]).strip()[:5] if updates["quiet_hours_start"] else None
            if "quiet_hours_end" in updates:
                sub.quiet_hours_end = str(updates["quiet_hours_end"]).strip()[:5] if updates["quiet_hours_end"] else None
            if "bypass_quiet_for_p0" in updates and updates["bypass_quiet_for_p0"] is not None:
                sub.bypass_quiet_for_p0 = bool(updates["bypass_quiet_for_p0"])
            sub.updated_at = _utcnow()
            session.flush()
            return {
                "id": sub.id,
                "recipient_zalo_id": sub.recipient_zalo_id,
                "member_id": sub.member_id,
                "username": sub.username,
                "role": sub.role,
                "enabled": bool(sub.enabled),
                "receive_p0": bool(sub.receive_p0),
                "receive_p1": bool(sub.receive_p1),
                "receive_daily_report": bool(sub.receive_daily_report),
                "receive_weekly_report": bool(sub.receive_weekly_report),
                "receive_repo_alerts": bool(sub.receive_repo_alerts) and sub.role.upper() == "ADMIN",
                "quiet_hours_start": sub.quiet_hours_start,
                "quiet_hours_end": sub.quiet_hours_end,
                "bypass_quiet_for_p0": bool(sub.bypass_quiet_for_p0),
            }

    def resolve_zalo_principal(
        self,
        zalo_user_id: str,
        *,
        fallback_authorized_users: Optional[List[str]] = None,
    ) -> ZaloPrincipal:
        uid = str(zalo_user_id or "").strip()
        if not uid or uid == "*":
            return ZaloPrincipal(
                zalo_user_id=uid,
                username="",
                member_id="",
                role="ANONYMOUS",
                display_name="Unknown",
                is_authorized=False,
                is_linked=False,
            )

        with self.db.session_scope() as session:
            active_link = session.execute(
                select(ZaloIdentityLinkRecord)
                .where(
                    ZaloIdentityLinkRecord.zalo_user_id == uid,
                    ZaloIdentityLinkRecord.status == "ACTIVE",
                )
                .order_by(ZaloIdentityLinkRecord.verified_at.desc())
            ).scalars().first()
            if active_link is not None:
                return ZaloPrincipal(
                    zalo_user_id=uid,
                    username=active_link.username,
                    member_id=active_link.member_id,
                    role=active_link.role.upper(),
                    display_name=active_link.display_name or active_link.username,
                    is_authorized=True,
                    is_linked=True,
                )

        # Fallback to explicit allowlist (for bootstrap/legacy Admin IDs)
        allowed_set = {
            str(u).strip()
            for u in (fallback_authorized_users or [])
            if str(u).strip() and str(u).strip() != "*"
        }
        if uid not in allowed_set:
            return ZaloPrincipal(
                zalo_user_id=uid,
                username="",
                member_id="",
                role="ANONYMOUS",
                display_name="Unauthorized",
                is_authorized=False,
                is_linked=False,
            )

        # If legacy ID happens to be an actual non-admin LabOps username, bind its real role
        try:
            from app.services.labops_service import service as core_svc

            for m in getattr(core_svc, "members", []) or []:
                if str(m.get("username") or "").strip() == uid:
                    m_role = str(m.get("role") or "RESEARCHER").strip().upper()
                    return ZaloPrincipal(
                        zalo_user_id=uid,
                        username=uid,
                        member_id=str(m.get("id") or ""),
                        role=m_role,
                        display_name=str(m.get("full_name") or uid),
                        is_authorized=True,
                        is_linked=False,
                    )
        except Exception:
            pass

        # Explicit legacy allowlisted Zalo operator IDs (not matching a researcher username) default to ADMIN
        return ZaloPrincipal(
            zalo_user_id=uid,
            username="admin",
            member_id="",
            role="ADMIN",
            display_name=f"Zalo Admin ({uid[:8]})",
            is_authorized=True,
            is_linked=False,
        )

    def get_verified_admin_recipients(
        self,
        *,
        fallback_authorized_users: Optional[List[str]] = None,
        explicit_admin_ids: Optional[List[str]] = None,
        alert_category: str = "repo_alerts",
    ) -> List[str]:
        """Return ONLY Zalo IDs bound to verified ADMIN principals who have alerts enabled.
        Strictly excludes RESEARCHER / STUDENT / unlinked researcher identities.
        """
        candidates: List[str] = []
        if explicit_admin_ids:
            candidates.extend([c.strip() for c in explicit_admin_ids if c and c.strip() and c.strip() != "*"])

        with self.db.session_scope() as session:
            active_admins = session.execute(
                select(ZaloIdentityLinkRecord).where(
                    ZaloIdentityLinkRecord.status == "ACTIVE",
                    ZaloIdentityLinkRecord.role == "ADMIN",
                )
            ).scalars().all()
            for link in active_admins:
                if link.zalo_user_id:
                    candidates.append(link.zalo_user_id.strip())

            subs = {
                s.recipient_zalo_id: s
                for s in session.execute(select(NotificationSubscriptionRecord)).scalars().all()
            }

        # Only inspect fallback_authorized_users if they don't resolve to non-ADMIN active links
        if not candidates and fallback_authorized_users:
            candidates.extend([u.strip() for u in fallback_authorized_users if u and u.strip() and u.strip() != "*"])
        elif fallback_authorized_users:
            # Also include legacy unlinked Admin IDs if explicit_admin_ids wasn't restricting
            if not explicit_admin_ids:
                for u in fallback_authorized_users:
                    u_clean = str(u or "").strip()
                    if u_clean and u_clean != "*" and u_clean not in candidates:
                        candidates.append(u_clean)

        verified: List[str] = []
        for cid in dict.fromkeys(candidates):
            p = self.resolve_zalo_principal(cid, fallback_authorized_users=fallback_authorized_users)
            if not p.is_admin:
                continue
            sub = subs.get(cid)
            if sub is not None:
                if not sub.enabled:
                    continue
                if alert_category == "repo_alerts" and not sub.receive_repo_alerts:
                    continue
                if alert_category == "daily_report" and not sub.receive_daily_report:
                    continue
                if alert_category == "p0" and not sub.receive_p0:
                    continue
                if alert_category == "p1" and not sub.receive_p1:
                    continue
            verified.append(cid)
        return verified

    def get_owner_recipients(
        self,
        owner_username: Optional[str],
        *,
        include_admins: bool = True,
        fallback_authorized_users: Optional[List[str]] = None,
        alert_category: str = "p1",
    ) -> List[str]:
        recipients: List[str] = []
        clean_owner = str(owner_username or "").strip()
        if clean_owner:
            with self.db.session_scope() as session:
                links = session.execute(
                    select(ZaloIdentityLinkRecord).where(
                        ZaloIdentityLinkRecord.username == clean_owner,
                        ZaloIdentityLinkRecord.status == "ACTIVE",
                    )
                ).scalars().all()
                for lk in links:
                    if lk.zalo_user_id:
                        recipients.append(lk.zalo_user_id)
        if include_admins:
            recipients.extend(
                self.get_verified_admin_recipients(
                    fallback_authorized_users=fallback_authorized_users,
                    alert_category=alert_category,
                )
            )
        return list(dict.fromkeys(recipients))
