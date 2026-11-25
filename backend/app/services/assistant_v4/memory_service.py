"""
LabOps Personal AI Agent V4 — Scoped PostgreSQL/SQLite Conversational Memory & Preferences.
- Strictly isolated per (tenant_id, principal_id, channel, bot_id, chat_id, session_id).
- Stores typed user / assistant / tool_call / tool_result messages, preserving tool pairs.
- Redacts secrets before DB persistence; never stores raw credential tokens or raw private logs.
- Enforces idle TTL (60m), max_messages (20), and approximate context token budget (4096).
- `/clear` resets chat history only — NEVER deletes audit logs or events.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import V4ChatMessageRecord, V4ChatSessionRecord, V4UserPreferenceRecord
from app.db.session import DatabaseManager
from app.services.assistant_v4.config import MemoryConfig
from app.services.assistant_v4.event_store import redact_payload, redact_text


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return _utcnow()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _estimate_tokens(text: str) -> int:
    # Conservative token estimate (~3 chars/token for mixed Vietnamese/English)
    return max(1, (len(text or "") + 2) // 3)


def make_scope_key(
    *,
    tenant_id: str = "aiot-lab",
    principal_id: str,
    channel: str,
    bot_id: str = "default",
    chat_id: str,
    session_key: str = "active",
) -> str:
    return f"{tenant_id}:{principal_id}:{channel}:{bot_id}:{chat_id}:{session_key}"


class ScopedSessionMemory:
    def __init__(self, db_manager: DatabaseManager, *, cfg: Optional[MemoryConfig] = None):
        self.db = db_manager
        self.cfg = cfg or MemoryConfig()

    def get_or_create_session(
        self,
        *,
        tenant_id: str = "aiot-lab",
        principal_id: str,
        channel: str = "zalo",
        bot_id: str = "default",
        chat_id: str,
        now_override: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        now = _as_utc(now_override)
        scope_prefix = f"{tenant_id}:{principal_id}:{channel}:{bot_id}:{chat_id}:"
        with self.db.session_scope() as session:
            existing = (
                session.execute(
                    select(V4ChatSessionRecord)
                    .where(
                        V4ChatSessionRecord.scope_key.like(f"{scope_prefix}%"),
                        V4ChatSessionRecord.status == "ACTIVE",
                    )
                    .order_by(V4ChatSessionRecord.last_active_at.desc())
                )
                .scalars()
                .first()
            )
            if existing is not None:
                idle_cutoff = now - timedelta(minutes=self.cfg.idle_ttl_minutes)
                if _as_utc(existing.last_active_at) >= idle_cutoff:
                    return self._serialize_session(existing)
                existing.status = "EXPIRED"

            sid = f"sess4-{uuid.uuid4().hex[:12]}"
            rec = V4ChatSessionRecord(
                session_id=sid,
                scope_key=f"{scope_prefix}{sid}",
                tenant_id=tenant_id,
                principal_id=principal_id,
                channel=channel,
                bot_id=bot_id,
                chat_id=chat_id,
                status="ACTIVE",
                sequence_no=0,
                version=1,
                last_active_at=now,
                expires_at=now + timedelta(days=self.cfg.raw_chat_retention_days),
                created_at=now,
            )
            session.add(rec)
            session.flush()
            return self._serialize_session(rec)

    def start_new_session(
        self,
        *,
        tenant_id: str = "aiot-lab",
        principal_id: str,
        channel: str = "zalo",
        bot_id: str = "default",
        chat_id: str,
    ) -> Dict[str, Any]:
        now = _utcnow()
        scope_prefix = f"{tenant_id}:{principal_id}:{channel}:{bot_id}:{chat_id}:"
        with self.db.session_scope() as session:
            actives = (
                session.execute(
                    select(V4ChatSessionRecord).where(
                        V4ChatSessionRecord.scope_key.like(f"{scope_prefix}%"),
                        V4ChatSessionRecord.status == "ACTIVE",
                    )
                )
                .scalars()
                .all()
            )
            for a in actives:
                a.status = "CLOSED"
            sid = f"sess4-{uuid.uuid4().hex[:12]}"
            rec = V4ChatSessionRecord(
                session_id=sid,
                scope_key=f"{scope_prefix}{sid}",
                tenant_id=tenant_id,
                principal_id=principal_id,
                channel=channel,
                bot_id=bot_id,
                chat_id=chat_id,
                status="ACTIVE",
                sequence_no=0,
                version=1,
                last_active_at=now,
                expires_at=now + timedelta(days=self.cfg.raw_chat_retention_days),
                created_at=now,
            )
            session.add(rec)
            session.flush()
            return self._serialize_session(rec)

    def clear_active_context(
        self,
        *,
        principal_id: str,
        channel: str = "zalo",
        bot_id: str = "default",
        chat_id: str,
        tenant_id: str = "aiot-lab",
    ) -> Dict[str, Any]:
        """Clear ONLY active conversation messages/context in the scoped chat — audit logs remain immutable."""
        scope_prefix = f"{tenant_id}:{principal_id}:{channel}:{bot_id}:{chat_id}:"
        deleted_msgs = 0
        with self.db.session_scope() as session:
            actives = (
                session.execute(
                    select(V4ChatSessionRecord).where(
                        V4ChatSessionRecord.scope_key.like(f"{scope_prefix}%"),
                        V4ChatSessionRecord.status == "ACTIVE",
                    )
                )
                .scalars()
                .all()
            )
            for s_rec in actives:
                res = session.execute(delete(V4ChatMessageRecord).where(V4ChatMessageRecord.session_id == s_rec.session_id))
                deleted_msgs += int(getattr(res, "rowcount", 0) or 0)
                s_rec.summary_text = ""
                s_rec.status = "CLEARED"
        return {"cleared_messages": deleted_msgs, "scope": scope_prefix.rstrip(":"), "audit_preserved": True}

    def append_turn_messages(
        self,
        *,
        session_id: str,
        expected_version: Optional[int],
        entries: List[Dict[str, Any]],
        now_override: Optional[datetime] = None,
    ) -> int:
        """
        Append typed user / tool_call / tool_result / assistant entries atomically in sequence.
        Optimistic version check rejects stale out-of-order writers cleanly.
        """
        now = _as_utc(now_override)
        with self.db.session_scope() as session:
            s_rec = session.get(V4ChatSessionRecord, session_id)
            if s_rec is None or s_rec.status != "ACTIVE":
                raise LookupError("Session is not active")
            if expected_version is not None and int(s_rec.version) != int(expected_version):
                raise RuntimeError("STALE_SESSION_VERSION")

            seq = int(s_rec.sequence_no or 0)
            for item in entries:
                seq += 1
                role = str(item.get("role") or "user")
                raw_content = str(item.get("content") or "")
                redacted = redact_text(raw_content)
                safe_payload = redact_payload(item.get("payload") or {})
                toks = _estimate_tokens(redacted + json.dumps(safe_payload, ensure_ascii=False, default=str))
                session.add(
                    V4ChatMessageRecord(
                        id=f"cmsg4-{uuid.uuid4().hex[:12]}",
                        session_id=session_id,
                        seq=seq,
                        role=role,
                        tool_call_id=item.get("tool_call_id"),
                        tool_name=item.get("tool_name"),
                        content_redacted=redacted[:4000],
                        payload_json=safe_payload if isinstance(safe_payload, dict) else {"data": safe_payload},
                        token_estimate=toks,
                        created_at=now,
                    )
                )
            s_rec.sequence_no = seq
            s_rec.version = int(s_rec.version) + 1
            s_rec.last_active_at = now
            session.flush()
            return int(s_rec.version)

    def get_bounded_context(
        self,
        *,
        session_id: str,
        principal_id: str,
        channel: str,
        chat_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Load bounded conversation history for AI inference:
        - Strictly verifies principal_id + channel + chat_id match session (prevents cross-session leakage!).
        - Enforces max_messages and max_context_tokens without ever splitting tool_call / tool_result pairs!
        """
        with self.db.session_scope() as session:
            s_rec = session.get(V4ChatSessionRecord, session_id)
            if s_rec is None:
                return []
            if s_rec.status != "ACTIVE":
                raise PermissionError(f"SESSION_NOT_ACTIVE:{s_rec.status}")
            if s_rec.principal_id != principal_id or s_rec.channel != channel or s_rec.chat_id != chat_id:
                raise PermissionError("CROSS_SESSION_ISOLATION_VIOLATION")
            rows = (
                session.execute(
                    select(V4ChatMessageRecord)
                    .where(V4ChatMessageRecord.session_id == session_id)
                    .order_by(V4ChatMessageRecord.seq.asc())
                )
                .scalars()
                .all()
            )
            if not rows:
                return []

            # Group into complete atomic turns (starting at role=='user') so tool pairs are NEVER split
            turns: List[List[V4ChatMessageRecord]] = []
            cur_turn: List[V4ChatMessageRecord] = []
            for r in rows:
                if r.role == "user" and cur_turn:
                    turns.append(cur_turn)
                    cur_turn = [r]
                else:
                    cur_turn.append(r)
            if cur_turn:
                turns.append(cur_turn)

            kept_turns: List[List[V4ChatMessageRecord]] = []
            msg_count = 0
            tok_count = 0
            for turn in reversed(turns):
                t_msgs = len(turn)
                t_toks = sum(int(m.token_estimate or 0) for m in turn)
                if kept_turns and (
                    msg_count + t_msgs > self.cfg.max_messages
                    or tok_count + t_toks > self.cfg.max_context_tokens
                ):
                    break
                kept_turns.insert(0, turn)
                msg_count += t_msgs
                tok_count += t_toks

            flat: List[Dict[str, Any]] = []
            for turn in kept_turns:
                for m in turn:
                    entry: Dict[str, Any] = {
                        "role": "assistant" if m.role == "tool_call" else ("tool" if m.role == "tool_result" else m.role),
                        "content": m.content_redacted,
                    }
                    if m.role == "tool_call" and m.payload_json.get("tool_calls"):
                        entry["tool_calls"] = m.payload_json["tool_calls"]
                    if m.role == "tool_result" and m.tool_call_id:
                        entry["tool_call_id"] = m.tool_call_id
                        if m.tool_name:
                            entry["name"] = m.tool_name
                    flat.append(entry)
            return flat

    def set_preference(self, principal_id: str, key: str, value: Any, *, timezone_name: Optional[str] = None) -> Dict[str, Any]:
        clean_k = str(key or "").strip()[:64]
        safe_v = redact_payload(value)
        now = _utcnow()
        with self.db.session_scope() as session:
            pref = session.get(V4UserPreferenceRecord, principal_id)
            if pref is None:
                pref = V4UserPreferenceRecord(
                    principal_id=principal_id,
                    timezone=timezone_name or "Asia/Ho_Chi_Minh",
                    notification_prefs_json={},
                    opted_in_preferences_json={clean_k: safe_v} if clean_k else {},
                    authorized_subscriptions_json=[],
                    version=1,
                    updated_at=now,
                )
                session.add(pref)
            else:
                if timezone_name:
                    pref.timezone = timezone_name
                if clean_k:
                    cur = dict(pref.opted_in_preferences_json or {})
                    cur[clean_k] = safe_v
                    pref.opted_in_preferences_json = cur
                pref.version = int(pref.version) + 1
                pref.updated_at = now
            session.flush()
            return self._serialize_prefs(pref)

    def get_preferences(self, principal_id: str) -> Dict[str, Any]:
        with self.db.session_scope() as session:
            pref = session.get(V4UserPreferenceRecord, principal_id)
            if pref is None:
                return {"principal_id": principal_id, "timezone": "Asia/Ho_Chi_Minh", "notification_prefs": {}, "opted_in_preferences": {}, "authorized_subscriptions": []}
            return self._serialize_prefs(pref)

    def get_memory_overview(self, *, principal_id: str, channel: str = "zalo", chat_id: str = "") -> Dict[str, Any]:
        with self.db.session_scope() as session:
            stmt = select(V4ChatSessionRecord).where(
                V4ChatSessionRecord.principal_id == principal_id,
                V4ChatSessionRecord.status == "ACTIVE",
            )
            if channel:
                stmt = stmt.where(V4ChatSessionRecord.channel == channel)
            if chat_id:
                stmt = stmt.where(V4ChatSessionRecord.chat_id == chat_id)
            sessions = session.execute(stmt.order_by(V4ChatSessionRecord.last_active_at.desc())).scalars().all()
            prefs = session.get(V4UserPreferenceRecord, principal_id)
            return {
                "principal_id": principal_id,
                "active_sessions": [self._serialize_session(s) for s in sessions[:5]],
                "preferences": self._serialize_prefs(prefs) if prefs else {},
                "note": "Chỉ lưu preference đã chọn (opt-in) và lịch sử hội thoại ngắn hạn được kiểm soát quyền.",
            }

    def revoke_principal_sessions(self, principal_ids: List[str]) -> int:
        """Immediately revoke all ACTIVE chat sessions belonging to revoked principal IDs."""
        clean_ids = sorted({str(pid).strip() for pid in principal_ids if str(pid or "").strip()})
        if not clean_ids:
            return 0
        revoked = 0
        now = _utcnow()
        with self.db.session_scope() as session:
            rows = (
                session.execute(
                    select(V4ChatSessionRecord).where(
                        V4ChatSessionRecord.status == "ACTIVE",
                        (V4ChatSessionRecord.principal_id.in_(clean_ids) | V4ChatSessionRecord.chat_id.in_(clean_ids)),
                    )
                )
                .scalars()
                .all()
            )
            for row in rows:
                row.status = "REVOKED"
                row.summary_text = ""
                row.version = int(row.version or 1) + 1
                row.last_active_at = now
                revoked += 1
        return revoked

    @staticmethod
    def _serialize_session(s: V4ChatSessionRecord) -> Dict[str, Any]:
        return {
            "session_id": s.session_id,
            "scope_key": s.scope_key,
            "principal_id": s.principal_id,
            "channel": s.channel,
            "chat_id": s.chat_id,
            "status": s.status,
            "sequence_no": int(s.sequence_no or 0),
            "version": int(s.version or 1),
            "last_active_at": _as_utc(s.last_active_at).isoformat(),
        }

    @staticmethod
    def _serialize_prefs(p: V4UserPreferenceRecord) -> Dict[str, Any]:
        return {
            "principal_id": p.principal_id,
            "timezone": p.timezone,
            "notification_prefs": dict(p.notification_prefs_json or {}),
            "opted_in_preferences": redact_payload(p.opted_in_preferences_json or {}),
            "authorized_subscriptions": list(p.authorized_subscriptions_json or []),
            "version": int(p.version or 1),
            "updated_at": _as_utc(p.updated_at).isoformat(),
        }
