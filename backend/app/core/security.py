"""
Xác thực & phân quyền LabOps V2 (thuần stdlib để dùng cho cả FastAPI và fallback server).

- Token phiên: base64url(payload_json).base64url(HMAC-SHA256(secret, payload))
  payload = {"sub": username, "role": "ADMIN"|"RESEARCHER", "member_id": ...,
             "jti": ..., "iat": ..., "exp": epoch, "scope": "session"}
- Step-up token: token dùng một lần (single-use JTI) gắn với action & target_id.
- Node Agent: Bearer token máy riêng (LABOPS_NODE_AGENT_TOKEN), không bao giờ có quyền người dùng.
- Không tin username/role/X-LabOps-Actor do client gửi. Danh tính luôn lấy từ token đã kiểm chữ ký.
- Fail-closed: thiếu secret -> 503, token sai/hết hạn/đã thu hồi -> 401, sai vai trò -> 403.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.core.config import settings
from app.db.atomic_json import atomic_write_json, safe_load_json

ROLES = {"ADMIN", "RESEARCHER"}
MAX_SESSION_TTL_SECONDS = 8 * 3600  # 8 hours max


class AuthError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class Principal:
    username: str
    role: str
    member_id: Optional[str] = None
    kind: str = "user"  # "user" | "node_agent"
    jti: Optional[str] = None
    iat: Optional[int] = None
    scope: str = "session"
    workspace_id: Optional[str] = None
    node_id: Optional[str] = None

    @property
    def is_admin(self) -> bool:
        return self.kind == "user" and self.role == "ADMIN"


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _secret() -> str:
    if "LABOPS_SESSION_SECRET" in os.environ:
        return os.environ["LABOPS_SESSION_SECRET"]
    return getattr(settings, "LABOPS_SESSION_SECRET", "")


class TokenRevocationStore:
    """Thread-safe persistent JTI and subject-wide token revocation store."""

    def __init__(self, data_dir: str) -> None:
        self.data_dir = os.path.abspath(data_dir)
        self.store_path = os.path.join(self.data_dir, "revoked_tokens.json")
        self._lock = threading.RLock()
        self._db_manager: Any = None
        os.makedirs(self.data_dir, exist_ok=True)

    def bind_db(self, db_manager: Any) -> None:
        self._db_manager = db_manager

    def _load(self) -> Dict[str, Any]:
        raw = safe_load_json(
            self.store_path,
            lambda: {"jtis": {}, "subjects": {}, "members": {}, "active_members": {}},
        )
        if not isinstance(raw, dict):
            return {"jtis": {}, "subjects": {}, "members": {}, "active_members": {}}
        raw.setdefault("jtis", {})
        raw.setdefault("subjects", {})
        raw.setdefault("members", {})
        raw.setdefault("active_members", {})
        return raw

    def _prune(self, state: Dict[str, Any], now: int) -> None:
        jtis = state.get("jtis", {})
        expired = [k for k, v in jtis.items() if isinstance(v, dict) and int(v.get("exp", 0)) < now - 60]
        for k in expired:
            jtis.pop(k, None)

    def revoke_jti(self, jti: str, exp: int, reason: str = "logout", *, member_id: Optional[str] = None) -> None:
        if not jti:
            return
        now = int(time.time())
        with self._lock:
            state = self._load()
            self._prune(state, now)
            state["jtis"][jti] = {"exp": int(exp), "revoked_at": now, "reason": reason, "member_id": member_id}
            atomic_write_json(self.store_path, state)
        self._persist_db(jti=jti, username=None, member_id=member_id, revoked_before=None, exp=int(exp), reason=reason)

    def revoke_member(
        self,
        member_id: str,
        *,
        username: Optional[str] = None,
        revoked_before: Optional[int] = None,
        reason: str = "deleted",
        permanent: bool = False,
    ) -> None:
        if not member_id:
            return
        now = int(time.time())
        cutoff = int(revoked_before) if revoked_before is not None else now + 1
        with self._lock:
            state = self._load()
            self._prune(state, now)
            state["members"][member_id] = {
                "revoked_before": cutoff,
                "username": username,
                "updated_at": now,
                "reason": reason,
                "permanent": bool(permanent),
            }
            if username:
                active = state.get("active_members", {}).get(username)
                if isinstance(active, dict) and active.get("member_id") == member_id:
                    state["active_members"].pop(username, None)
            atomic_write_json(self.store_path, state)
        self._persist_db(
            jti=None,
            username=username,
            member_id=member_id,
            revoked_before=cutoff,
            exp=0,
            reason=reason,
            permanent=bool(permanent),
        )

    def revoke_subject(
        self,
        username: str,
        revoked_before: Optional[int] = None,
        reason: str = "suspended",
        *,
        member_id: Optional[str] = None,
    ) -> None:
        if not username:
            return
        now = int(time.time())
        cutoff = int(revoked_before) if revoked_before is not None else now + 1
        with self._lock:
            state = self._load()
            self._prune(state, now)
            entry: Dict[str, Any] = {
                "revoked_before": cutoff,
                "updated_at": now,
                "reason": reason,
            }
            if member_id:
                entry["member_id"] = member_id
            state["subjects"][username] = entry
            if member_id:
                active = state.get("active_members", {}).get(username)
                if isinstance(active, dict) and active.get("member_id") == member_id:
                    state["active_members"].pop(username, None)
            atomic_write_json(self.store_path, state)
        self._persist_db(
            jti=None,
            username=username,
            member_id=member_id,
            revoked_before=cutoff,
            exp=0,
            reason=reason,
        )

    def register_active_member(self, username: str, member_id: str) -> None:
        """
        Register newly committed (username, member_id) without clearing any historical JTI
        or prior-member revocation records. Legacy subject-level revocations lacking
        member_id continue to apply to legacy/prior tokens while exempting only the new member_id.
        """
        if not username or not member_id:
            return
        now = int(time.time())
        with self._lock:
            state = self._load()
            self._prune(state, now)
            state["active_members"][username] = {"member_id": member_id, "activated_at": now}
            subj = state.get("subjects", {}).get(username)
            if isinstance(subj, dict) and not subj.get("member_id"):
                subj["applies_except_member_id"] = member_id
            atomic_write_json(self.store_path, state)

    def unrevoke_subject(self, username: str) -> None:
        if not username:
            return
        with self._lock:
            state = self._load()
            if username in state.get("subjects", {}):
                state["subjects"].pop(username, None)
                atomic_write_json(self.store_path, state)

    def is_revoked(
        self,
        jti: Optional[str],
        username: str,
        iat: Optional[int],
        *,
        member_id: Optional[str] = None,
    ) -> bool:
        now = int(time.time())
        with self._lock:
            state = self._load()
            if jti and jti in state.get("jtis", {}):
                return True
            if member_id:
                m_rec = state.get("members", {}).get(member_id)
                if isinstance(m_rec, dict):
                    if m_rec.get("permanent"):
                        return True
                    cutoff = int(m_rec.get("revoked_before", 0))
                    if iat is None or int(iat) <= cutoff:
                        return True
            subj = state.get("subjects", {}).get(username)
            if isinstance(subj, dict):
                subj_mid = subj.get("member_id")
                exempt_mid = subj.get("applies_except_member_id")
                applies = False
                if subj_mid:
                    # Legacy tokens lacking member_id or tokens belonging to the revoked member_id remain blocked;
                    # tokens belonging to a distinctly recreated member_id are unaffected.
                    applies = (member_id is None) or (member_id == subj_mid)
                elif exempt_mid:
                    applies = (member_id is None) or (member_id != exempt_mid)
                else:
                    applies = True
                if applies:
                    cutoff = int(subj.get("revoked_before", 0))
                    if iat is None or int(iat) <= cutoff:
                        return True
            if now <= 0:
                return False
        return False

    def _persist_db(
        self,
        *,
        jti: Optional[str],
        username: Optional[str],
        revoked_before: Optional[int],
        exp: int,
        reason: str,
        member_id: Optional[str] = None,
        permanent: bool = False,
    ) -> None:
        if self._db_manager is None:
            return
        try:
            from app.db.models import RevokedTokenRecord

            with self._db_manager.session_scope() as s:
                if getattr(self._db_manager, "revoked_tokens_has_member_id", True):
                    s.add(
                        RevokedTokenRecord(
                            jti=jti,
                            username=username,
                            member_id=member_id,
                            revoked_before_epoch=revoked_before,
                            reason=reason,
                            permanent=bool(permanent) if getattr(self._db_manager, "revoked_tokens_has_permanent", True) else False,
                            expires_at=exp,
                        )
                    )
                else:
                    from sqlalchemy import text

                    s.execute(
                        text(
                            "INSERT INTO revoked_tokens (jti, username, revoked_before_epoch, reason, expires_at, created_at) "
                            "VALUES (:jti, :username, :revoked_before_epoch, :reason, :expires_at, CURRENT_TIMESTAMP)"
                        ),
                        {
                            "jti": jti,
                            "username": username,
                            "revoked_before_epoch": revoked_before,
                            "reason": reason,
                            "expires_at": exp,
                        },
                    )
        except Exception:
            pass


_REVOCATION_STORES: Dict[str, TokenRevocationStore] = {}
_REVOCATION_LOCK = threading.Lock()


def get_revocation_store() -> TokenRevocationStore:
    data_dir = os.path.abspath(
        os.getenv("LABOPS_DATA_DIR")
        or getattr(settings, "LABOPS_DATA_DIR", "")
        or os.path.join(os.path.dirname(__file__), "..", "..", "data")
    )
    with _REVOCATION_LOCK:
        store = _REVOCATION_STORES.get(data_dir)
        if store is None:
            store = TokenRevocationStore(data_dir)
            _REVOCATION_STORES[data_dir] = store
        return store


def _sign_payload(payload_obj: Dict[str, Any], secret: str) -> str:
    payload = json.dumps(payload_obj, separators=(",", ":")).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return f"{_b64e(payload)}.{_b64e(sig)}"


def _decode_and_verify_raw(token: str, secret: Optional[str] = None) -> Dict[str, Any]:
    resolved_secret = secret if secret is not None else _secret()
    if not resolved_secret:
        raise AuthError(503, "Xác thực chưa được cấu hình (LABOPS_SESSION_SECRET)")
    try:
        p64, s64 = token.split(".", 1)
        payload = _b64d(p64)
        expected = hmac.new(resolved_secret.encode("utf-8"), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64d(s64)):
            raise ValueError("bad signature")
        data = json.loads(payload)
    except AuthError:
        raise
    except Exception:
        raise AuthError(401, "Token không hợp lệ")
    if not isinstance(data, dict):
        raise AuthError(401, "Token không hợp lệ")
    return data


def issue_token(
    username: str,
    role: str,
    member_id: Optional[str] = None,
    ttl: int = 3600,
    secret: Optional[str] = None,
    jti: Optional[str] = None,
    scope: str = "session",
) -> str:
    resolved_secret = secret if secret is not None else _secret()
    if not resolved_secret:
        raise AuthError(503, "LABOPS_SESSION_SECRET chưa được cấu hình")
    if role not in ROLES:
        raise AuthError(400, "Vai trò không hợp lệ")
    now = int(time.time())
    effective_ttl = min(ttl, MAX_SESSION_TTL_SECONDS) if ttl > 0 else ttl
    return _sign_payload(
        {
            "sub": username,
            "role": role,
            "member_id": member_id,
            "jti": jti or uuid.uuid4().hex,
            "iat": now,
            "exp": now + effective_ttl,
            "scope": scope,
        },
        resolved_secret,
    )


def verify_token(token: str, secret: Optional[str] = None) -> Principal:
    data = _decode_and_verify_raw(token, secret=secret)
    exp = data.get("exp")
    role = data.get("role")
    subject = data.get("sub")
    scope = data.get("scope", "session")
    jti = data.get("jti")
    iat = data.get("iat")
    if type(exp) is not int or not isinstance(role, str) or role not in ROLES:
        raise AuthError(401, "Token không hợp lệ")
    if not isinstance(subject, str) or not subject.strip():
        raise AuthError(401, "Token không hợp lệ")
    if scope != "session":
        raise AuthError(401, "Loại token không hợp lệ cho phiên đăng nhập")
    if exp <= time.time():
        raise AuthError(401, "Phiên đăng nhập đã hết hạn")
    if get_revocation_store().is_revoked(
        jti=str(jti) if isinstance(jti, str) else None,
        username=subject,
        iat=int(iat) if type(iat) is int else None,
        member_id=str(data["member_id"]) if isinstance(data.get("member_id"), str) and data["member_id"].strip() else None,
    ):
        raise AuthError(401, "Phiên đăng nhập đã bị thu hồi")
    return Principal(
        username=str(subject),
        role=role,
        member_id=data.get("member_id"),
        jti=str(jti) if isinstance(jti, str) else None,
        iat=int(iat) if type(iat) is int else None,
        scope=str(scope),
    )


def issue_step_up_token(
    username: str,
    action: str,
    target_id: str,
    *,
    ttl: int = 300,
    secret: Optional[str] = None,
) -> str:
    resolved_secret = secret if secret is not None else _secret()
    if not resolved_secret:
        raise AuthError(503, "LABOPS_SESSION_SECRET chưa được cấu hình")
    now = int(time.time())
    clamped_ttl = min(max(int(ttl), 30), 300)
    return _sign_payload(
        {
            "sub": username,
            "role": "ADMIN",
            "scope": "step_up",
            "act": action,
            "tgt": target_id,
            "jti": f"stp-{uuid.uuid4().hex}",
            "iat": now,
            "exp": now + clamped_ttl,
        },
        resolved_secret,
    )


def verify_step_up_token(
    token: str,
    *,
    expected_username: str,
    expected_action: str,
    expected_target_id: str,
    secret: Optional[str] = None,
) -> Dict[str, Any]:
    if not token or not token.strip():
        raise AuthError(403, "STEP_UP_REQUIRED: Yêu cầu xác thực lại mật khẩu quản trị viên")
    data = _decode_and_verify_raw(token.strip(), secret=secret)
    exp = data.get("exp")
    if type(exp) is not int or exp <= time.time():
        raise AuthError(403, "STEP_UP_EXPIRED: Token xác thực nâng cao đã hết hạn")
    if data.get("scope") != "step_up" or data.get("role") != "ADMIN":
        raise AuthError(403, "STEP_UP_INVALID: Token không phải loại step-up ADMIN")
    if data.get("sub") != expected_username:
        raise AuthError(403, "STEP_UP_ACTOR_MISMATCH: Token không thuộc về quản trị viên hiện tại")
    if data.get("act") != expected_action or data.get("tgt") != expected_target_id:
        raise AuthError(403, "STEP_UP_TARGET_MISMATCH: Token không khớp thao tác hoặc đối tượng xóa")
    jti = data.get("jti")
    if not isinstance(jti, str) or not jti:
        raise AuthError(403, "STEP_UP_INVALID: Thiếu mã đơn nhất jti")
    store = get_revocation_store()
    if store.is_revoked(jti=jti, username=str(data.get("sub")), iat=int(data.get("iat") or 0)):
        raise AuthError(403, "STEP_UP_REPLAYED: Token xác thực nâng cao đã được sử dụng")
    store.revoke_jti(jti, exp, reason=f"step_up_consumed:{expected_action}")
    return data


def principal_from_authorization(header: Optional[str]) -> Principal:
    if not header or not header.lower().startswith("bearer "):
        raise AuthError(401, "Yêu cầu đăng nhập")
    token = header.split(" ", 1)[1].strip()
    agent_token = os.getenv("LABOPS_NODE_AGENT_TOKEN", "")
    if agent_token and hmac.compare_digest(token, agent_token):
        return Principal(username="node-agent", role="RESEARCHER", kind="node_agent")
    return verify_token(token)


def require_role(p: Principal, role: str) -> None:
    if p.kind != "user":
        raise AuthError(403, "Danh tính máy không được phép thực hiện thao tác người dùng")
    if role == "ADMIN" and not p.is_admin:
        raise AuthError(403, "Chỉ quản trị viên được phép")


def require_owner_or_admin(p: Principal, owner: Optional[str]) -> None:
    if p.is_admin:
        return
    if p.kind != "user" or not owner or owner != p.username:
        raise AuthError(403, "Bạn không có quyền với tài nguyên này")


def require_node_agent(p: Principal) -> None:
    if p.kind != "node_agent":
        raise AuthError(403, "Chỉ Node Agent được phép")
