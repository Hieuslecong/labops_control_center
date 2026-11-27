"""
LabOps Member & Research Access Management (MRAM v1.0) Service.

Implements:
- P1: Separate Web Login credentials (salted scrypt/Argon2id hash, lockout, first-login password change)
      from SSH/Workspace provisioning; Admin-only initial web account creation (no public signup).
- P2: Member Profiles & immutable-ID Supervisor Assignments + Supervisor Grouping for /dashboard/users
      and read-only scoped view for verified Lecturers.
- P3: Post-login SSH Request form (STUDENT vs LECTURER) with exact 24-hour review SLA & re-apply.
- P4: Dynamic host & storage pool resource recommendation, DB capacity reservations, pre-validation,
      optimistic concurrency Admin approval with immutable config snapshot & SHA-256 hash.
- P5: Secure Zalo Approval Invitations (CSPRNG 32-byte token, SHA-256 hash in DB, GET-safe verification,
      strictly targeted at verified ADMIN 0c00ed813eccd7928edd).
- P6: Idempotent Durable Provisioning integration with existing LabOps provisioning / runtime checks.
- P7: Verified Handover, atomic one-time credential claim/reissue, student SSH expiration preserving web
      login & disk data, and renewal workflow.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.config import settings
from app.db.models import (
    ApprovalInvitationRecord,
    CapacityReservationRecord,
    CredentialClaimRecord,
    MemberProfileRecord,
    MemberRecord,
    MemberWebCredentialRecord,
    NotificationOutboxRecord,
    PolicyAcceptanceRecord,
    ProvisioningOperationRecord,
    RequestApprovalRecord,
    RequestResourceRecommendationRecord,
    ServerAccessRequestRecord,
    SupervisorAssignmentRecord,
    ZaloIdentityLinkRecord,
)
from app.db.session import DatabaseManager
from app.services.labops_service import MemberConflictError, service as labops_service
from app.services.storage_inventory_service import get_pool_priority_tier, storage_service

logger = logging.getLogger("labops.mram")

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
POLICY_VERSION = "mram-v1.0"
POLICY_TEXT_DIGEST = hashlib.sha256(b"LabOps-Acceptable-Use-Security-Policy-mram-v1.0").hexdigest()
TARGET_APPROVER_ZALO_ID = "0c00ed813eccd7928edd"
TARGET_APPROVER_NAME = "Hiếu Lê"
DEFAULT_PUBLIC_URL = "https://aiotserver.fitdnu.id.vn"
DEFAULT_TARGET_HOST_ID = "workstation"
STUDENT_DEFAULT_STORAGE_GB = int(os.getenv("MRAM_DEFAULT_STUDENT_STORAGE_GB", "50"))
STUDENT_MAX_RAM_GB = int(os.getenv("MRAM_MAX_STUDENT_RAM_GB", "16"))


_ACADEMIC_TITLE_TOKENS = {
    "TS.",
    "PGS.",
    "PGS.TS.",
    "GS.",
    "GS.TS.",
    "THS.",
    "TS",
    "PGS",
    "PGS.TS",
    "GS",
    "GS.TS",
    "THS",
}


_VN_TONE_PAIRS = (
    # oa / oe ở cuối âm tiết (hoà -> hòa, khoẻ -> khỏe...)
    (re.compile(r"(?<![qQ])o([àáảãạÀÁẢÃẠ])(?=\s|$)"), lambda m: {"à": "òa", "á": "óa", "ả": "ỏa", "ã": "õa", "ạ": "ọa", "À": "Òa", "Á": "Óa", "Ả": "Ỏa", "Ã": "Õa", "Ạ": "Ọa"}[m.group(1)]),
    (re.compile(r"(?<![qQ])O([àáảãạÀÁẢÃẠ])(?=\s|$)"), lambda m: {"à": "Òa", "á": "Óa", "ả": "Ỏa", "ã": "Õa", "ạ": "Ọa", "À": "Òa", "Á": "Óa", "Ả": "Ỏa", "Ã": "Õa", "Ạ": "Ọa"}[m.group(1)]),
    (re.compile(r"(?<![qQ])o([èéẻẽẹÈÉẺẼẸ])(?=\s|$)"), lambda m: {"è": "òe", "é": "óe", "ẻ": "ỏe", "ẽ": "õe", "ẹ": "ọe", "È": "Òe", "É": "Óe", "Ẻ": "Ỏe", "Ẽ": "Õe", "Ẹ": "Ọe"}[m.group(1)]),
    (re.compile(r"(?<![qQ])O([èéẻẽẹÈÉẺẼẸ])(?=\s|$)"), lambda m: {"è": "Òe", "é": "Óe", "ẻ": "Ỏe", "ẽ": "Õe", "ẹ": "Ọe", "È": "Òe", "É": "Óe", "Ẻ": "Ỏe", "Ẽ": "Õe", "Ẹ": "Ọe"}[m.group(1)]),
    # uy không đứng sau q/Q (Thuỳ -> Thùy, Huỳnh vẫn giữ nguyên vì có nh sau y)
    (re.compile(r"(?<![qQ])u([ỳýỷỹỵỲÝỶỸỴ])(?=\s|$)"), lambda m: {"ỳ": "ùy", "ý": "úy", "ỷ": "ủy", "ỹ": "ũy", "ỵ": "ụy", "Ỳ": "Ùy", "Ý": "Úy", "Ỷ": "Ủy", "Ỹ": "Ũy", "Ỵ": "Ụy"}[m.group(1)]),
    (re.compile(r"(?<![qQ])U([ỳýỷỹỵỲÝỶỸỴ])(?=\s|$)"), lambda m: {"ỳ": "Ùy", "ý": "Úy", "ỷ": "Ủy", "ỹ": "Ũy", "ỵ": "Ụy", "Ỳ": "Ùy", "Ý": "Úy", "Ỷ": "Ủy", "Ỹ": "Ũy", "Ỵ": "Ụy"}[m.group(1)]),
)


def _normalize_vn_tone_placement(text: str) -> str:
    normalized = unicodedata.normalize("NFC", text)
    for pattern, repl in _VN_TONE_PAIRS:
        normalized = pattern.sub(repl, normalized)
    return normalized


def _strip_vn_accents(text: str) -> str:
    """Lowercase and strip Vietnamese diacritics (handling đ/Đ) for fuzzy name matching."""
    cleaned = str(text or "").replace("đ", "d").replace("Đ", "D")
    nfkd = unicodedata.normalize("NFKD", cleaned)
    no_marks = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return " ".join(no_marks.lower().split())


def _normalize_supervisor_name(raw: Optional[str]) -> str:
    """Loại bỏ học hàm/học vị, chuẩn hóa Unicode NFC + vị trí dấu thanh và viết hoa chữ cái đầu mỗi từ."""
    cleaned = _normalize_vn_tone_placement(" ".join(str(raw or "").strip().split()))
    if not cleaned or cleaned.upper() in {"N/A", "NONE", "NULL"}:
        return ""
    parts: List[str] = []
    for w in cleaned.split(" "):
        if w.upper() in _ACADEMIC_TITLE_TOKENS:
            continue
        if w.startswith("(") and len(w) > 1:
            parts.append("(" + w[1].upper() + w[2:].lower())
        else:
            parts.append(w[0].upper() + w[1:].lower())
    return " ".join(parts)
REVIEW_SLA_HOURS = 24
OPEN_INITIAL_STATUSES = {"DRAFT", "PENDING_APPROVAL", "NEED_INFO", "APPROVED", "QUEUED", "PROVISIONING"}
ACTIVE_MEMBER_BAD_STATUSES = {"SUSPENDED", "ARCHIVED", "DELETE_ELIGIBLE", "DELETION_IN_PROGRESS", "DELETED", "REVOKED"}
SAFE_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{2,31}$")
FORBIDDEN_REQUEST_CLIENT_KEYS = {
    "member_id",
    "role",
    "approved_resources",
    "mount_point",
    "approved_host_id",
    "approved_pool_id",
    "filesystem_uuid",
    "status",
    "version",
    "decided_by",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _fmt_vn(dt: Optional[datetime]) -> str:
    utc_dt = _as_utc(dt)
    if not utc_dt:
        return "Không giới hạn"
    return utc_dt.astimezone(VN_TZ).strftime("%H:%M %d/%m/%Y (ICT)")


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def hash_web_password(password: str) -> Tuple[str, str]:
    """Hash a web password using Argon2id if available, else stdlib salted scrypt."""
    try:
        from argon2 import PasswordHasher  # type: ignore

        ph = PasswordHasher(time_cost=2, memory_cost=65536, parallelism=2)
        return ph.hash(password), "argon2id"
    except Exception:
        salt = os.urandom(16)
        dk = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=16384,
            r=8,
            p=1,
            maxmem=64 * 1024 * 1024,
            dklen=32,
        )
        return f"scrypt$16384$8$1${salt.hex()}${dk.hex()}", "scrypt"


def verify_web_password_hash(password: str, stored_hash: str) -> bool:
    if not stored_hash or not password:
        return False
    if stored_hash.startswith("$argon2"):
        try:
            from argon2 import PasswordHasher  # type: ignore

            ph = PasswordHasher()
            return bool(ph.verify(stored_hash, password))
        except Exception:
            return False
    if stored_hash.startswith("scrypt$"):
        parts = stored_hash.split("$")
        if len(parts) != 6:
            return False
        _, n_s, r_s, p_s, salt_hex, expected_hex = parts
        try:
            salt = bytes.fromhex(salt_hex)
            n_val, r_val, p_val = int(n_s), int(r_s), int(p_s)
            dklen = len(bytes.fromhex(expected_hex))
            dk = hashlib.scrypt(
                password.encode("utf-8"),
                salt=salt,
                n=n_val,
                r=r_val,
                p=p_val,
                maxmem=128 * 1024 * 1024,
                dklen=dklen,
            )
            return hmac.compare_digest(dk.hex(), expected_hex)
        except Exception:
            return False
    return False


class MramConflictError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 409, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details or {}


class MramValidationError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 400, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details or {}


class MramService:
    """Service orchestrating LabOps MRAM v1.0 across DB, LabOps provisioning, storage, and Zalo."""

    def __init__(self, data_dir: Optional[str] = None, db_manager: Optional[DatabaseManager] = None):
        base_dir = data_dir or getattr(labops_service, "data_dir", None) or os.getenv("LABOPS_DATA_DIR") or "./data"
        self.data_dir = os.path.abspath(base_dir)
        self.db = db_manager or getattr(labops_service, "db", None) or DatabaseManager(self.data_dir)
        self._test_dry_run_provisioning = os.getenv("MRAM_DEMO_DRY_RUN", "").strip().lower() in {"1", "true", "yes"}

    def _public_base_url(self) -> str:
        return (os.getenv("LABOPS_PUBLIC_URL") or DEFAULT_PUBLIC_URL).rstrip("/")

    # ------------------------------------------------------------------
    # P1: Separate Web Login Credential Lifecycle
    # ------------------------------------------------------------------
    def _ensure_legacy_profile(self, member: Dict[str, Any]) -> MemberProfileRecord:
        """Create or fetch MemberProfileRecord for existing pre-MRAM members without breaking compat."""
        m_id = str(member["id"])
        with self.db.session_scope() as session:
            prof = session.get(MemberProfileRecord, m_id)
            if prof is not None:
                session.expunge(prof)
                return prof

            raw_cat = str(member.get("member_category") or member.get("category") or "").upper()
            if raw_cat not in {"STUDENT", "LECTURER"}:
                raw_cat = "LECTURER" if str(member.get("role", "")).upper() in {"LECTURER", "INSTRUCTOR"} else "STUDENT"

            prov_state = str(member.get("provisioning_state") or "").upper()
            if prov_state == "WEB_ONLY":
                ssh_state = "NONE"
            elif str(member.get("status") or "").upper() == "REVOKED":
                ssh_state = "REVOKED"
            elif str(member.get("status") or "").upper() == "SUSPENDED":
                ssh_state = "SUSPENDED"
            elif member.get("ssh_port") or member.get("overall_readiness") == "READY" or member.get("workspace_id"):
                ssh_state = "VERIFIED_READY"
            else:
                ssh_state = "NONE"

            prof = MemberProfileRecord(
                member_id=m_id,
                username=str(member.get("username") or ""),
                full_name=str(member.get("fullName") or member.get("full_name") or member.get("username") or ""),
                email=str(member.get("email") or ""),
                member_code=str(member.get("member_code") or member.get("student_code") or ""),
                member_category=raw_cat,
                department_class=str(member.get("department_class") or ""),
                primary_supervisor_id=member.get("primary_supervisor_id"),
                direct_manager_id=member.get("direct_manager_id"),
                web_account_status="SUSPENDED" if member.get("status") == "SUSPENDED" else "WEB_ACTIVE",
                ssh_access_state=str(member.get("ssh_access_state") or ssh_state),
                verified_by="legacy-sync",
                created_by="system",
            )
            session.add(prof)
            session.flush()
            session.expunge(prof)
            return prof

    def _serialize_profile(self, prof: MemberProfileRecord, member: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {
            "member_id": prof.member_id,
            "username": prof.username,
            "full_name": prof.full_name,
            "email": prof.email,
            "member_code": prof.member_code,
            "member_category": prof.member_category,
            "department_class": prof.department_class,
            "primary_supervisor_id": prof.primary_supervisor_id,
            "direct_manager_id": prof.direct_manager_id,
            "web_account_status": prof.web_account_status,
            "ssh_access_state": prof.ssh_access_state,
            "ssh_expires_at": _as_utc(prof.ssh_expires_at).isoformat() if prof.ssh_expires_at else None,
            "verified_by": prof.verified_by,
            "created_by": prof.created_by,
            "created_at": _as_utc(prof.created_at).isoformat() if prof.created_at else None,
            "assigned_server": (member or {}).get("assigned_server"),
            "storage_quota_gb": (member or {}).get("storage_quota_gb", 0),
            "cpu_limit_cores": (member or {}).get("cpu_limit_cores", 0),
            "ram_limit_gb": (member or {}).get("ram_limit_gb", 0),
            "gpu_permissions": list((member or {}).get("gpu_permissions") or []),
            "ssh_port": (member or {}).get("ssh_port"),
        }

    def has_web_credential(self, username: str) -> bool:
        uname = str(username or "").strip().lower()
        with self.db.session_scope() as session:
            rec = session.execute(
                select(MemberWebCredentialRecord).where(MemberWebCredentialRecord.username == uname)
            ).scalar_one_or_none()
            return rec is not None

    def verify_member_web_password(self, username: str, password: str) -> Dict[str, Any]:
        """Verify salted web credential. Raises LookupError('NO_WEB_CREDENTIAL') for legacy fallback."""
        uname = str(username or "").strip().lower()
        now = _utcnow()
        with self.db.session_scope() as session:
            cred = session.execute(
                select(MemberWebCredentialRecord).where(MemberWebCredentialRecord.username == uname)
            ).scalar_one_or_none()
            if cred is None:
                raise LookupError("NO_WEB_CREDENTIAL")

            prof = session.get(MemberProfileRecord, cred.member_id)
            if prof and prof.web_account_status in {"SUSPENDED", "REVOKED", "DELETED"}:
                raise PermissionError(f"ACCOUNT_REVOKED: Tài khoản web đang ở trạng thái {prof.web_account_status}")

            locked_until = _as_utc(cred.locked_until)
            if locked_until and locked_until > now:
                raise PermissionError("ACCOUNT_LOCKED: Tài khoản tạm khóa do nhập sai mật khẩu quá nhiều lần")

            if not verify_web_password_hash(password, cred.password_hash):
                cred.failed_attempts = int(cred.failed_attempts or 0) + 1
                if cred.failed_attempts >= 5:
                    cred.locked_until = now + timedelta(minutes=15)
                raise PermissionError("INVALID_PASSWORD: Tài khoản hoặc mật khẩu không chính xác")

            cred.failed_attempts = 0
            cred.locked_until = None
            return {
                "authenticated": True,
                "member_id": cred.member_id,
                "username": cred.username,
                "must_change_password": bool(cred.must_change_password),
                "member_category": prof.member_category if prof else "STUDENT",
                "ssh_access_state": prof.ssh_access_state if prof else "NONE",
            }

    def change_member_web_password(
        self,
        *,
        username: str,
        current_password: str,
        new_password: str,
    ) -> Dict[str, Any]:
        uname = str(username or "").strip().lower()
        if not new_password or len(new_password) < 12:
            raise MramValidationError("WEAK_PASSWORD", "Mật khẩu web mới phải có ít nhất 12 ký tự")
        if current_password == new_password:
            raise MramValidationError("PASSWORD_REUSE", "Mật khẩu mới phải khác mật khẩu hiện tại")

        labops_service.ensure_members_fresh()
        # Verify current password (either web credential or legacy workspace password)
        member = next((m for m in labops_service.members if (m.get("username") or "").lower() == uname), None)
        if not member:
            raise LookupError(f"Không tìm thấy thành viên '{uname}'")

        has_web = self.has_web_credential(uname)
        if has_web:
            self.verify_member_web_password(uname, current_password)
        else:
            labops_service.verify_member_workspace_password(uname, current_password)

        new_hash, algo = hash_web_password(new_password)
        now = _utcnow()
        with self.db.session_scope() as session:
            cred = session.execute(
                select(MemberWebCredentialRecord).where(MemberWebCredentialRecord.username == uname)
            ).scalar_one_or_none()
            if cred is None:
                cred = MemberWebCredentialRecord(
                    member_id=str(member["id"]),
                    username=uname,
                    password_hash=new_hash,
                    hash_algorithm=algo,
                    must_change_password=False,
                    failed_attempts=0,
                    password_changed_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add(cred)
            else:
                cred.password_hash = new_hash
                cred.hash_algorithm = algo
                cred.must_change_password = False
                cred.failed_attempts = 0
                cred.locked_until = None
                cred.password_changed_at = now

        member["must_change_password"] = False
        member["version"] = int(member.get("version") or 1) + 1
        labops_service._audit(uname, "WEB_PASSWORD_CHANGED", "MEMBER", uname, "SUCCESS", "Thành viên đổi mật khẩu cổng web")
        labops_service._save_persistent_data()
        return {"success": True, "username": uname, "must_change_password": False}

    def recover_member_web_password(
        self,
        *,
        username: str,
        member_code: str,
        email: str,
        new_password: str,
    ) -> Dict[str, Any]:
        """Allow students/researchers to self-recover their web login password by verifying profile identity (strictly forbidden for ADMIN)."""
        uname = str(username or "").strip().lower()
        code = str(member_code or "").strip()
        mail = str(email or "").strip().lower()
        admin_user = (os.getenv("LABOPS_ADMIN_USERNAME") or settings.LABOPS_ADMIN_USERNAME or "admin").strip().lower()

        if not uname:
            raise MramValidationError("MISSING_USERNAME", "Vui lòng nhập Tên đăng nhập", 400)
        if uname in {"admin", admin_user}:
            raise MramValidationError(
                "ADMIN_RECOVERY_FORBIDDEN",
                "Chức năng lấy lại mật khẩu Web chỉ dành cho Sinh viên / Nghiên cứu viên, không áp dụng cho Quản trị viên.",
                403,
            )
        if not code or not mail:
            raise MramValidationError(
                "MISSING_IDENTITY_FIELDS",
                "Vui lòng nhập đầy đủ Tên đăng nhập, Mã Sinh viên/Giảng viên và Email đã đăng ký",
                400,
            )
        if not new_password or len(new_password) < 12:
            raise MramValidationError("WEAK_PASSWORD", "Mật khẩu mới phải có ít nhất 12 ký tự", 400)

        labops_service.ensure_members_fresh()
        member = next((m for m in labops_service.members if (m.get("username") or "").lower() == uname), None)
        if member and str(member.get("role") or "").strip().upper() == "ADMIN":
            raise MramValidationError(
                "ADMIN_RECOVERY_FORBIDDEN",
                "Chức năng lấy lại mật khẩu Web không áp dụng cho tài khoản Quản trị viên.",
                403,
            )

        now = _utcnow()
        new_hash, algo = hash_web_password(new_password)
        with self.db.session_scope() as session:
            prof = session.execute(
                select(MemberProfileRecord).where(MemberProfileRecord.username == uname)
            ).scalar_one_or_none()
            cred = session.execute(
                select(MemberWebCredentialRecord).where(MemberWebCredentialRecord.username == uname)
            ).scalar_one_or_none()
            if prof is None or cred is None:
                raise MramValidationError(
                    "RECOVERY_VERIFICATION_FAILED",
                    "Thông tin Tên đăng nhập, Mã Sinh viên/Giảng viên hoặc Email không chính xác.",
                    401,
                )
            if not str(prof.member_code or "").strip() or not str(prof.email or "").strip():
                raise MramValidationError(
                    "RECOVERY_PROFILE_INCOMPLETE",
                    "Tài khoản chưa có đầy đủ Mã SV/GV hoặc Email để tự khôi phục; vui lòng liên hệ Admin cấp lại mật khẩu Web.",
                    400,
                )
            if (
                str(prof.member_code).strip().lower() != code.lower()
                or str(prof.email).strip().lower() != mail
            ):
                raise MramValidationError(
                    "RECOVERY_VERIFICATION_FAILED",
                    "Thông tin Tên đăng nhập, Mã Sinh viên/Giảng viên hoặc Email không chính xác.",
                    401,
                )

            cred.password_hash = new_hash
            cred.hash_algorithm = algo
            cred.must_change_password = False
            cred.failed_attempts = 0
            cred.locked_until = None
            cred.password_changed_at = now
            cred.updated_at = now

        if member:
            member["must_change_password"] = False
            member["version"] = int(member.get("version") or 1) + 1
        labops_service._audit(
            uname,
            "MRAM_WEB_PASSWORD_RECOVERED",
            "MEMBER",
            uname,
            "SUCCESS",
            "Sinh viên/Nghiên cứu viên tự đặt lại mật khẩu đăng nhập Web sau khi xác minh định danh",
        )
        labops_service._save_persistent_data()
        return {
            "success": True,
            "username": uname,
            "message": "Đã đặt lại mật khẩu tài khoản Web thành công. Bạn có thể đăng nhập bằng mật khẩu mới.",
        }

    def admin_reissue_member_web_password(
        self,
        *,
        member_id: str,
        new_password: Optional[str] = None,
        actor: str = "admin",
    ) -> Dict[str, Any]:
        """Admin reissues a temporary web login password for a student/researcher (strictly forbidden for ADMIN)."""
        target_key = str(member_id or "").strip()
        admin_user = (os.getenv("LABOPS_ADMIN_USERNAME") or settings.LABOPS_ADMIN_USERNAME or "admin").strip().lower()
        if not target_key:
            raise MramValidationError("MISSING_MEMBER_ID", "Thiếu mã thành viên", 400)
        if target_key.lower() in {"admin", admin_user}:
            raise MramValidationError(
                "ADMIN_REISSUE_FORBIDDEN",
                "Không áp dụng cấp lại mật khẩu Web cho tài khoản Quản trị viên.",
                403,
            )

        raw_pw = str(new_password or "").strip()
        if raw_pw:
            if len(raw_pw) < 12:
                raise MramValidationError("WEAK_PASSWORD", "Mật khẩu mới phải có ít nhất 12 ký tự", 400)
        else:
            raw_pw = f"LabOpsWeb!{secrets.token_urlsafe(9)}#"

        pw_hash, algo = hash_web_password(raw_pw)
        now = _utcnow()
        member: Optional[Dict[str, Any]] = None

        with self.db.session_scope() as session:
            prof = session.get(MemberProfileRecord, target_key)
            if prof is None:
                prof = session.execute(
                    select(MemberProfileRecord).where(MemberProfileRecord.username == target_key.lower())
                ).scalar_one_or_none()
            if prof is None:
                raise LookupError(f"Không tìm thấy hồ sơ tài khoản Web '{target_key}'")

            labops_service.ensure_members_fresh()
            uname = prof.username.lower()
            member = next((m for m in labops_service.members if m.get("id") == prof.member_id or (m.get("username") or "").lower() == uname), None)
            if uname in {"admin", admin_user} or (member and str(member.get("role") or "").strip().upper() == "ADMIN"):
                raise MramValidationError(
                    "ADMIN_REISSUE_FORBIDDEN",
                    "Không áp dụng cấp lại mật khẩu Web cho tài khoản Quản trị viên.",
                    403,
                )

            cred = session.get(MemberWebCredentialRecord, prof.member_id)
            if cred is None:
                cred = MemberWebCredentialRecord(
                    member_id=prof.member_id,
                    username=uname,
                    password_hash=pw_hash,
                    hash_algorithm=algo,
                    must_change_password=True,
                    failed_attempts=0,
                    password_changed_at=now,
                    created_at=now,
                    updated_at=now,
                )
                session.add(cred)
            else:
                cred.password_hash = pw_hash
                cred.hash_algorithm = algo
                cred.must_change_password = True
                cred.failed_attempts = 0
                cred.locked_until = None
                cred.password_changed_at = now
                cred.updated_at = now

            prof_full_name = prof.full_name or uname
            prof_member_id = prof.member_id
            prof_username = prof.username

        if member:
            member["must_change_password"] = True
            member["version"] = int(member.get("version") or 1) + 1
        labops_service._audit(
            actor,
            "MRAM_WEB_PASSWORD_ADMIN_REISSUED",
            "MEMBER",
            prof_username,
            "SUCCESS",
            f"Admin cấp lại mật khẩu tài khoản Web cho @{prof_username}",
        )
        labops_service._save_persistent_data()
        return {
            "success": True,
            "member_id": prof_member_id,
            "username": prof_username,
            "full_name": prof_full_name,
            "reissued_web_password": raw_pw,
            "must_change_password": True,
            "one_time_notice": "Mật khẩu đăng nhập Web mới chỉ hiển thị 1 lần. Hãy sao chép và gửi cho sinh viên.",
        }

    def list_web_accounts(self) -> Dict[str, Any]:
        """List web-registered member accounts separately from host SSH accounts, showing automatic SSH linkage upon approval."""
        self.expire_overdue_requests()
        self.sweep_expired_ssh_access()

        members_by_id: Dict[str, Dict[str, Any]] = {}
        for m in labops_service.members:
            if m.get("status") == "DELETED":
                continue
            members_by_id[str(m["id"])] = m

        now = _utcnow()
        with self.db.session_scope() as session:
            creds = session.execute(select(MemberWebCredentialRecord)).scalars().all()
            profiles = session.execute(select(MemberProfileRecord)).scalars().all()
            requests = session.execute(select(ServerAccessRequestRecord)).scalars().all()

        cred_by_member = {c.member_id: c for c in creds}
        prof_by_member = {p.member_id: p for p in profiles}
        lecturers = {p.member_id: p for p in profiles if p.member_category == "LECTURER"}

        reqs_by_member: Dict[str, List[ServerAccessRequestRecord]] = {}
        for req in requests:
            reqs_by_member.setdefault(req.member_id, []).append(req)

        items: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()

        for p in profiles:
            m = members_by_id.get(p.member_id)
            if m is None:
                continue
            cred = cred_by_member.get(p.member_id)
            is_web = (
                cred is not None
                or m.get("provisioning_state") == "WEB_ONLY"
                or bool(m.get("is_web_registered"))
                or str(p.created_by or "") in {"self-register", "admin"}
            )
            if not is_web:
                continue
            seen_ids.add(p.member_id)

            m_reqs = sorted(reqs_by_member.get(p.member_id, []), key=lambda x: x.created_at or now, reverse=True)
            latest_req = m_reqs[0] if m_reqs else None

            supervisor_name = ""
            if p.primary_supervisor_id and p.primary_supervisor_id in lecturers:
                lec_prof = lecturers[p.primary_supervisor_id]
                supervisor_name = _normalize_supervisor_name(lec_prof.full_name) or lec_prof.full_name
            else:
                for req_item in m_reqs:
                    cand = _normalize_supervisor_name((req_item.metadata_json or {}).get("supervisor_name"))
                    if cand:
                        supervisor_name = cand
                        break

            ssh_port = m.get("ssh_port")
            is_ssh_linked = bool(
                p.ssh_access_state == "VERIFIED_READY"
                and ssh_port
                and m.get("provisioning_state") != "WEB_ONLY"
            )
            ssh_server = str(m.get("assigned_server") or "").strip() if is_ssh_linked else None
            ssh_host = (
                str(m.get("ssh_ip") or ("192.168.167.251" if ssh_server == "workstation" else "192.168.167.250"))
                if is_ssh_linked
                else None
            )

            items.append({
                "member_id": p.member_id,
                "username": p.username,
                "full_name": p.full_name or m.get("fullName") or p.username,
                "email": p.email or m.get("email") or "",
                "member_code": p.member_code or "",
                "member_category": p.member_category or "STUDENT",
                "department_class": p.department_class or "",
                "primary_supervisor_id": p.primary_supervisor_id,
                "supervisor_name": supervisor_name or None,
                "web_account_status": p.web_account_status or "WEB_ACTIVE",
                "ssh_access_state": p.ssh_access_state or "NONE",
                "ssh_expires_at": _as_utc(p.ssh_expires_at).isoformat() if p.ssh_expires_at else None,
                "must_change_password": bool(cred.must_change_password) if cred else False,
                "has_web_credential": cred is not None,
                "created_by": p.created_by or "self-register",
                "created_at": _as_utc(p.created_at).isoformat() if p.created_at else m.get("createdAt"),
                "is_ssh_linked": is_ssh_linked,
                "linked_ssh_member_id": p.member_id if is_ssh_linked else None,
                "linked_ssh_username": str(m.get("ssh_login_user") or p.username) if is_ssh_linked else None,
                "linked_ssh_server": ssh_server,
                "linked_ssh_host": ssh_host,
                "linked_ssh_port": int(ssh_port) if (is_ssh_linked and ssh_port is not None) else None,
                "latest_request": (
                    {
                        "id": latest_req.id,
                        "status": latest_req.status,
                        "project_title": latest_req.project_title,
                        "gpu_requested": bool(latest_req.gpu_requested),
                        "requested_duration_days": latest_req.requested_duration_days,
                        "submitted_at": _as_utc(latest_req.submitted_at).isoformat() if latest_req.submitted_at else None,
                        "decided_at": _as_utc(latest_req.decided_at).isoformat() if latest_req.decided_at else None,
                    }
                    if latest_req
                    else None
                ),
            })

        items.sort(key=lambda x: str(x.get("created_at") or ""), reverse=True)
        linked_cnt = sum(1 for item in items if item["is_ssh_linked"])
        pending_cnt = sum(
            1
            for item in items
            if not item["is_ssh_linked"]
            and (item.get("latest_request") or {}).get("status") in {"PENDING_APPROVAL", "NEED_INFO"}
        )
        return {
            "accounts": items,
            "summary": {
                "total": len(items),
                "ssh_linked": linked_cnt,
                "pending_ssh_approval": pending_cnt,
                "unlinked": len(items) - linked_cnt,
            },
        }

    def get_auth_context_for_member(self, username: str) -> Dict[str, Any]:
        uname = str(username or "").strip().lower()
        labops_service.ensure_members_fresh()
        member = next((m for m in labops_service.members if (m.get("username") or "").lower() == uname), None)
        if not member:
            return {"member_category": "STUDENT", "ssh_access_state": "NONE", "must_change_password": False}
        prof = self._ensure_legacy_profile(member)
        with self.db.session_scope() as session:
            cred = session.execute(
                select(MemberWebCredentialRecord).where(MemberWebCredentialRecord.username == uname)
            ).scalar_one_or_none()
            must_change = bool(cred.must_change_password) if cred else (member.get("password_status") == "CHANGE_REQUIRED")
        return {
            "member_id": prof.member_id,
            "full_name": prof.full_name,
            "member_category": prof.member_category,
            "ssh_access_state": prof.ssh_access_state,
            "web_account_status": prof.web_account_status,
            "ssh_expires_at": _as_utc(prof.ssh_expires_at).isoformat() if prof.ssh_expires_at else None,
            "must_change_password": must_change,
            "primary_supervisor_id": prof.primary_supervisor_id,
        }

    def create_web_member_account(self, payload: Dict[str, Any], actor: str = "admin") -> Dict[str, Any]:
        """Admin creates an initial Web-Only member account WITHOUT Linux/SSH/VPN/Docker/Storage/GPU."""
        labops_service.ensure_members_fresh()
        username = str(payload.get("username") or "").strip().lower()
        if not SAFE_USERNAME_RE.match(username):
            raise MramValidationError(
                "INVALID_USERNAME",
                "Username phải từ 3–32 ký tự chữ thường, số, gạch dưới hoặc gạch ngang",
            )
        if username in {"root", "admin", "ubuntu", "debian", "sshd", "daemon", "nobody", "postgres", "docker", "incus"}:
            raise MramValidationError("RESERVED_USERNAME", f"Username '{username}' thuộc danh sách hệ thống bị cấm")

        full_name = str(payload.get("full_name") or payload.get("fullName") or "").strip()
        email = str(payload.get("email") or "").strip().lower()
        member_code = str(payload.get("member_code") or payload.get("student_code") or "").strip()
        department_class = str(payload.get("department_class") or "").strip()
        category = str(payload.get("member_category") or "STUDENT").strip().upper()
        if category not in {"STUDENT", "LECTURER"}:
            raise MramValidationError("INVALID_CATEGORY", "member_category phải là STUDENT hoặc LECTURER")
        if len(full_name) < 2:
            raise MramValidationError("INVALID_FULL_NAME", "Vui lòng nhập họ tên đầy đủ")
        if not member_code:
            raise MramValidationError("MISSING_MEMBER_CODE", "Vui lòng nhập Mã sinh viên hoặc Mã cán bộ")

        # Duplicate check across existing LabOps members and DB
        if any((m.get("username") or "").lower() == username and m.get("status") != "DELETED" for m in labops_service.members):
            raise MemberConflictError("USERNAME_ALREADY_EXISTS", f"Tên đăng nhập '{username}' đã tồn tại", 409)

        sup_id = str(payload.get("primary_supervisor_id") or "").strip() or None
        direct_mgr_id = str(payload.get("direct_manager_id") or "").strip() or None
        if sup_id:
            self._validate_lecturer_id(sup_id)
        if direct_mgr_id:
            self._validate_member_exists(direct_mgr_id)

        raw_pw = str(payload.get("initial_password") or "").strip()
        if raw_pw:
            if len(raw_pw) < 12:
                raise MramValidationError("WEAK_PASSWORD", "Mật khẩu ban đầu phải có ít nhất 12 ký tự")
        else:
            raw_pw = f"Lab-{secrets.token_urlsafe(10)}!"

        pw_hash, algo = hash_web_password(raw_pw)
        member_id = f"mem-{uuid.uuid4().hex[:10]}"
        now = _utcnow()
        now_iso = now.isoformat()

        # Persist MemberRecord with ZERO compute/SSH/VPN/Storage allocations
        member_dict: Dict[str, Any] = {
            "id": member_id,
            "username": username,
            "fullName": full_name,
            "email": email or f"{username}@fitdnu.id.vn",
            "role": "RESEARCHER",
            "member_category": category,
            "member_code": member_code,
            "department_class": department_class,
            "primary_supervisor_id": sup_id,
            "direct_manager_id": direct_mgr_id,
            "status": "WEB_ACTIVE",
            "provisioning_state": "WEB_ONLY",
            "ssh_access_state": "NONE",
            "overall_readiness": "NOT_REQUESTED",
            "assigned_server": "",
            "workspace_name": "",
            "workspace_id": None,
            "container_id": None,
            "ssh_ip": None,
            "ssh_port": None,
            "ssh_status": "NONE",
            "ssh_key_status": "NONE",
            "cpu_limit_cores": 0,
            "ram_limit_gb": 0,
            "storage_quota_gb": 0,
            "vram_quota_gb": 0,
            "gpu_permissions": [],
            "must_change_password": True,
            "is_web_registered": True,
            "linked_web_member_id": member_id,
            "linked_web_username": username,
            "wireguard": {"status": "NOT_PROVISIONED", "devices": []},
            "version": 1,
            "createdAt": now_iso,
            "created_by": actor,
        }

        with self.db.session_scope() as session:
            m_row = MemberRecord(
                id=member_id,
                username=username,
                email=member_dict["email"],
                full_name=full_name,
                role="RESEARCHER",
                status="WEB_ACTIVE",
                provisioning_state="WEB_ONLY",
                assigned_server="",
                workspace_name="",
                workspace_dir=None,
                ssh_port=None,
                storage_quota_gb=0,
                cpu_limit_cores=0,
                ram_limit_gb=0,
                vram_quota_gb=0,
                gpu_permissions_json=[],
                payload_json=member_dict,
                created_at=now_iso,
                updated_at=now,
            )
            session.add(m_row)
            session.add(
                MemberWebCredentialRecord(
                    member_id=member_id,
                    username=username,
                    password_hash=pw_hash,
                    hash_algorithm=algo,
                    must_change_password=True,
                    failed_attempts=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            prof = MemberProfileRecord(
                member_id=member_id,
                username=username,
                full_name=full_name,
                email=member_dict["email"],
                member_code=member_code,
                member_category=category,
                department_class=department_class,
                primary_supervisor_id=sup_id,
                direct_manager_id=direct_mgr_id,
                web_account_status="WEB_ACTIVE",
                ssh_access_state="NONE",
                verified_by=actor,
                created_by=actor,
                created_at=now,
                updated_at=now,
            )
            session.add(prof)
            if sup_id:
                session.add(
                    SupervisorAssignmentRecord(
                        id=f"supa-{uuid.uuid4().hex[:10]}",
                        student_member_id=member_id,
                        supervisor_member_id=sup_id,
                        direct_manager_id=direct_mgr_id,
                        assignment_role="PRIMARY",
                        is_active=True,
                        assigned_by=actor,
                        change_reason="Phân công ban đầu khi tạo tài khoản web",
                        effective_from=now,
                    )
                )

        labops_service.members.insert(0, member_dict)
        labops_service._audit(
            actor,
            "MRAM_WEB_ACCOUNT_CREATED",
            "MEMBER",
            username,
            "SUCCESS",
            f"Admin tạo tài khoản web ban đầu ({category}, code={member_code}) — chưa cấp SSH/GPU/Storage",
        )
        labops_service._save_persistent_data()

        return {
            "member": member_dict,
            "profile": {
                "member_id": member_id,
                "username": username,
                "full_name": full_name,
                "email": member_dict["email"],
                "member_code": member_code,
                "member_category": category,
                "department_class": department_class,
                "primary_supervisor_id": sup_id,
                "web_account_status": "WEB_ACTIVE",
                "ssh_access_state": "NONE",
            },
            "initial_web_password": raw_pw,
            "must_change_password": True,
            "provisioning_note": "Chỉ tạo tài khoản cổng web. Chưa tạo Linux user, SSH key, WireGuard, Docker Workspace hay GPU.",
        }

    def check_username_availability(self, username: str) -> Dict[str, Any]:
        uname = str(username or "").strip().lower()
        if not uname:
            return {"available": False, "valid_format": False, "reason": "EMPTY", "message": "Vui lòng nhập tên đăng nhập"}
        if not SAFE_USERNAME_RE.match(uname):
            return {
                "available": False,
                "valid_format": False,
                "reason": "INVALID_FORMAT",
                "message": "3–32 ký tự chữ thường (a-z), số (0-9), '_' hoặc '-', bắt đầu bằng chữ cái hoặc '_'",
            }
        if uname in {"root", "admin", "ubuntu", "debian", "sshd", "daemon", "nobody", "postgres", "docker", "incus"}:
            return {
                "available": False,
                "valid_format": False,
                "reason": "RESERVED",
                "message": f"Tên đăng nhập '{uname}' thuộc hệ thống, không được sử dụng",
            }
        exists_mem = any(
            (m.get("username") or "").lower() == uname and m.get("status") != "DELETED"
            for m in labops_service.members
        )
        if not exists_mem:
            with self.db.session_scope() as session:
                exists_mem = (
                    session.execute(
                        select(MemberWebCredentialRecord).where(MemberWebCredentialRecord.username == uname)
                    ).scalar_one_or_none()
                    is not None
                )
        if exists_mem:
            return {
                "available": False,
                "valid_format": True,
                "reason": "ALREADY_EXISTS",
                "message": f"Tên đăng nhập '{uname}' đã tồn tại trên hệ thống",
            }
        return {
            "available": True,
            "valid_format": True,
            "reason": "OK",
            "message": f"Tên đăng nhập '{uname}' khả dụng",
        }

    def register_public_web_account(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Public self-registration strictly creates a WEB_ONLY RESEARCHER account without compute/SSH."""
        req_role = str(payload.get("role") or "RESEARCHER").strip().upper()
        if req_role != "RESEARCHER":
            raise MramValidationError("FORBIDDEN_ROLE_ASSIGNMENT", "Không được phép tự gán quyền quản trị khi đăng ký", 403)
        raw_pw = str(payload.get("password") or "")
        if len(raw_pw) < 12:
            raise MramValidationError("WEAK_PASSWORD", "Mật khẩu đăng ký phải có ít nhất 12 ký tự")

        clean_payload = {
            "username": payload.get("username"),
            "full_name": payload.get("full_name") or payload.get("fullName"),
            "email": payload.get("email"),
            "member_code": payload.get("member_code") or payload.get("student_code"),
            "department_class": payload.get("department_class"),
            "member_category": str(payload.get("member_category") or "STUDENT").strip().upper(),
            "primary_supervisor_id": payload.get("primary_supervisor_id"),
            "initial_password": raw_pw,
        }
        res = self.create_web_member_account(clean_payload, actor="self-register")
        mid = res["profile"]["member_id"]
        with self.db.session_scope() as session:
            cred = session.get(MemberWebCredentialRecord, mid)
            if cred is not None:
                cred.must_change_password = False
        res["member"]["must_change_password"] = False
        res["member"]["version"] = int(res["member"].get("version") or 1) + 1
        labops_service._save_persistent_data()
        res["must_change_password"] = False
        res.pop("initial_web_password", None)
        return res

    # ------------------------------------------------------------------
    # P2: Member Profiles, Supervisor Assignments & Grouping
    # ------------------------------------------------------------------
    def _validate_member_exists(self, member_id: str) -> Dict[str, Any]:
        for m in labops_service.members:
            if m.get("id") == member_id or m.get("username") == member_id:
                self._ensure_legacy_profile(m)
                return m
        with self.db.session_scope() as session:
            prof = session.get(MemberProfileRecord, member_id)
            if prof:
                return {"id": prof.member_id, "username": prof.username, "fullName": prof.full_name}
        raise MramValidationError("MEMBER_NOT_FOUND", f"Không tìm thấy hồ sơ thành viên '{member_id}'", 404)

    def _validate_lecturer_id(self, supervisor_id: str) -> Dict[str, Any]:
        m = self._validate_member_exists(supervisor_id)
        mid = str(m["id"])
        with self.db.session_scope() as session:
            prof = session.get(MemberProfileRecord, mid)
            if not prof or prof.member_category != "LECTURER":
                raise MramValidationError(
                    "SUPERVISOR_NOT_LECTURER",
                    f"Tài khoản '{supervisor_id}' không phải Giảng viên (LECTURER) đã xác minh",
                    400,
                )
            return self._serialize_profile(prof, m)

    def list_lecturers(self) -> List[Dict[str, Any]]:
        for m in labops_service.members:
            self._ensure_legacy_profile(m)
        with self.db.session_scope() as session:
            rows = session.execute(
                select(MemberProfileRecord)
                .where(MemberProfileRecord.member_category == "LECTURER")
                .order_by(MemberProfileRecord.full_name.asc())
            ).scalars().all()
            return [
                {
                    "member_id": r.member_id,
                    "username": r.username,
                    "full_name": r.full_name or r.username,
                    "email": r.email,
                    "member_code": r.member_code,
                    "department_class": r.department_class,
                }
                for r in rows
            ]

    def assign_supervisor(
        self,
        *,
        student_member_id: str,
        primary_supervisor_id: str,
        direct_manager_id: Optional[str] = None,
        actor: str = "admin",
        change_reason: str = "Cập nhật giảng viên hướng dẫn",
    ) -> Dict[str, Any]:
        labops_service.ensure_members_fresh()
        student_m = self._validate_member_exists(student_member_id)
        sid = str(student_m["id"])
        sup_prof = self._validate_lecturer_id(primary_supervisor_id)
        sup_id = str(sup_prof["member_id"])
        if sid == sup_id:
            raise MramValidationError("SELF_SUPERVISION_FORBIDDEN", "Không thể tự gán chính mình làm giảng viên hướng dẫn")
        if direct_manager_id:
            self._validate_member_exists(direct_manager_id)

        now = _utcnow()
        with self.db.session_scope() as session:
            active_rows = session.execute(
                select(SupervisorAssignmentRecord).where(
                    SupervisorAssignmentRecord.student_member_id == sid,
                    SupervisorAssignmentRecord.is_active == True,  # noqa: E712
                )
            ).scalars().all()
            for r in active_rows:
                r.is_active = False
                r.effective_to = now

            assign = SupervisorAssignmentRecord(
                id=f"supa-{uuid.uuid4().hex[:10]}",
                student_member_id=sid,
                supervisor_member_id=sup_id,
                direct_manager_id=direct_manager_id,
                assignment_role="PRIMARY",
                is_active=True,
                assigned_by=actor,
                change_reason=change_reason,
                effective_from=now,
            )
            session.add(assign)
            prof = session.get(MemberProfileRecord, sid)
            if prof:
                prof.primary_supervisor_id = sup_id
                prof.direct_manager_id = direct_manager_id
                prof.updated_at = now

        student_m["primary_supervisor_id"] = sup_id
        student_m["direct_manager_id"] = direct_manager_id
        student_m["version"] = int(student_m.get("version") or 1) + 1
        labops_service._audit(
            actor,
            "SUPERVISOR_ASSIGNED",
            "MEMBER",
            student_m.get("username", sid),
            "SUCCESS",
            f"Gán GVHD '{sup_prof['full_name']}' ({sup_id}): {change_reason}",
        )
        labops_service._save_persistent_data()
        return {
            "student_member_id": sid,
            "primary_supervisor_id": sup_id,
            "direct_manager_id": direct_manager_id,
            "supervisor": sup_prof,
        }

    def get_supervisor_groups(self) -> Dict[str, Any]:
        """Group students by primary supervisor for /dashboard/users."""
        self.expire_overdue_requests()
        self.sweep_expired_ssh_access()
        members_by_id: Dict[str, Dict[str, Any]] = {}
        for m in labops_service.members:
            if m.get("status") == "DELETED":
                continue
            members_by_id[str(m["id"])] = m
            self._ensure_legacy_profile(m)

        now = _utcnow()
        soon_threshold = now + timedelta(days=7)

        with self.db.session_scope() as session:
            profiles = session.execute(select(MemberProfileRecord)).scalars().all()
            requests = session.execute(select(ServerAccessRequestRecord)).scalars().all()
            web_cred_ids = set(
                session.execute(select(MemberWebCredentialRecord.member_id)).scalars().all()
            )

        reqs_by_member: Dict[str, List[ServerAccessRequestRecord]] = {}
        for req in requests:
            reqs_by_member.setdefault(req.member_id, []).append(req)

        lecturers = {p.member_id: p for p in profiles if p.member_category == "LECTURER"}
        groups_map: Dict[str, Dict[str, Any]] = {}

        def _ensure_group(gid: str, sup_prof: Optional[MemberProfileRecord]) -> Dict[str, Any]:
            if gid not in groups_map:
                sup_display = (
                    _normalize_supervisor_name(sup_prof.full_name) or sup_prof.full_name
                    if sup_prof
                    else "Chưa phân công GVHD"
                )
                groups_map[gid] = {
                    "supervisor_id": gid,
                    "supervisor_name": sup_display,
                    "supervisor_username": sup_prof.username if sup_prof else None,
                    "department_class": sup_prof.department_class if sup_prof else "",
                    "email": sup_prof.email if sup_prof else "",
                    "total_students": 0,
                    "pending_requests": 0,
                    "active_ssh_count": 0,
                    "expiring_soon_count": 0,
                    "projects": [],
                    "hosts": [],
                    "total_storage_quota_gb": 0,
                    "total_gpu_count": 0,
                    "students": [],
                }
            return groups_map[gid]

        for sup_id, sup_prof in lecturers.items():
            _ensure_group(sup_id, sup_prof)

        for p in profiles:
            if p.member_id not in members_by_id:
                continue
            if p.member_category == "LECTURER":
                continue
            m = members_by_id[p.member_id]
            has_requests = p.member_id in reqs_by_member
            is_web_member = (
                p.member_id in web_cred_ids
                or m.get("provisioning_state") == "WEB_ONLY"
                or bool(m.get("is_web_registered"))
                or str(p.created_by or "") in {"self-register", "admin"}
            )
            # Only group Web-registered members or students with SSH requests / supervisor assignment
            if not is_web_member and not has_requests and not p.primary_supervisor_id:
                continue
            m_reqs = sorted(reqs_by_member.get(p.member_id, []), key=lambda x: x.created_at or now, reverse=True)
            latest_req = m_reqs[0] if m_reqs else None
            entered_sup_name = ""
            for req_item in m_reqs:
                cand_sup = _normalize_supervisor_name((req_item.metadata_json or {}).get("supervisor_name"))
                if cand_sup:
                    entered_sup_name = cand_sup
                    break

            if p.primary_supervisor_id and p.primary_supervisor_id in lecturers:
                sup_id = p.primary_supervisor_id
                grp = _ensure_group(sup_id, lecturers.get(sup_id))
            elif entered_sup_name:
                matched_lec_id = next(
                    (
                        lid
                        for lid, lprof in lecturers.items()
                        if _normalize_supervisor_name(lprof.full_name).lower() == entered_sup_name.lower()
                    ),
                    None,
                )
                if matched_lec_id:
                    grp = _ensure_group(matched_lec_id, lecturers.get(matched_lec_id))
                else:
                    dyn_gid = f"sup-name:{entered_sup_name.lower()}"
                    if dyn_gid not in groups_map:
                        groups_map[dyn_gid] = {
                            "supervisor_id": dyn_gid,
                            "supervisor_name": entered_sup_name,
                            "supervisor_username": None,
                            "department_class": "Theo tên GVHD sinh viên khai báo",
                            "email": "",
                            "total_students": 0,
                            "pending_requests": 0,
                            "active_ssh_count": 0,
                            "expiring_soon_count": 0,
                            "projects": [],
                            "hosts": [],
                            "total_storage_quota_gb": 0,
                            "total_gpu_count": 0,
                            "students": [],
                        }
                    grp = groups_map[dyn_gid]
            else:
                grp = _ensure_group("UNASSIGNED", None)

            pending_cnt = sum(1 for r in m_reqs if r.status in {"PENDING_APPROVAL", "NEED_INFO"})
            is_ssh_active = p.ssh_access_state in {"VERIFIED_READY", "ACTIVE", "EXPIRING_SOON"}
            exp_dt = _as_utc(p.ssh_expires_at)
            is_expiring_soon = bool(is_ssh_active and exp_dt and exp_dt <= soon_threshold)

            quota_gb = int(m.get("storage_quota_gb") or 0)
            gpus = list(m.get("gpu_permissions") or [])
            host = str(m.get("assigned_server") or "").strip()

            grp["total_students"] += 1
            grp["pending_requests"] += pending_cnt
            if is_ssh_active:
                grp["active_ssh_count"] += 1
            if is_expiring_soon:
                grp["expiring_soon_count"] += 1
            grp["total_storage_quota_gb"] += quota_gb
            grp["total_gpu_count"] += len(gpus)
            if latest_req and latest_req.project_title and latest_req.project_title not in grp["projects"]:
                grp["projects"].append(latest_req.project_title)
            if host and host not in grp["hosts"]:
                grp["hosts"].append(host)

            grp["students"].append({
                "member_id": p.member_id,
                "username": p.username,
                "full_name": p.full_name,
                "member_code": p.member_code,
                "department_class": p.department_class,
                "email": p.email,
                "web_account_status": p.web_account_status,
                "ssh_access_state": p.ssh_access_state,
                "ssh_expires_at": exp_dt.isoformat() if exp_dt else None,
                "is_expiring_soon": is_expiring_soon,
                "pending_request_id": latest_req.id if latest_req and latest_req.status in {"PENDING_APPROVAL", "NEED_INFO"} else None,
                "latest_project": latest_req.project_title if latest_req else None,
                "assigned_server": host or None,
                "cpu_limit_cores": int(m.get("cpu_limit_cores") or 0),
                "ram_limit_gb": int(m.get("ram_limit_gb") or 0),
                "storage_quota_gb": quota_gb,
                "gpu_permissions": gpus,
                "direct_manager_id": p.direct_manager_id,
            })

        ordered = sorted(
            (g for g in groups_map.values() if not (g["supervisor_id"] == "UNASSIGNED" and g["total_students"] == 0)),
            key=lambda g: (1 if g["supervisor_id"] == "UNASSIGNED" else 0, -(g["pending_requests"]), -(g["total_students"]), g["supervisor_name"]),
        )
        return {
            "groups": ordered,
            "total_groups": len(ordered),
            "lecturers": self.list_lecturers(),
        }

    def get_supervised_students(self, actor_username: str, is_admin: bool = False) -> Dict[str, Any]:
        """Read-only scoped view for verified LECTURERs showing ONLY their supervised students without secrets."""
        uname = str(actor_username or "").strip().lower()
        groups_data = self.get_supervisor_groups()
        if is_admin:
            return {"scoped_role": "ADMIN", "groups": groups_data["groups"], "students": [s for g in groups_data["groups"] for s in g["students"]]}

        labops_service.ensure_members_fresh()
        member = next((m for m in labops_service.members if (m.get("username") or "").lower() == uname), None)
        if not member:
            raise PermissionError("Tài khoản không tồn tại")
        prof = self._ensure_legacy_profile(member)
        if prof.member_category != "LECTURER":
            raise PermissionError("Chỉ Giảng viên (LECTURER) hoặc Admin mới được xem danh sách sinh viên hướng dẫn")

        my_id = prof.member_id
        supervised: List[Dict[str, Any]] = []
        for g in groups_data["groups"]:
            for st in g["students"]:
                if g["supervisor_id"] == my_id or st.get("direct_manager_id") == my_id:
                    # Ensure strict secret stripping
                    supervised.append(dict(st))

        return {
            "scoped_role": "LECTURER",
            "supervisor_member_id": my_id,
            "supervisor_name": prof.full_name,
            "total_students": len(supervised),
            "students": supervised,
        }

    # ------------------------------------------------------------------
    # P3 & P4: Resource Recommendation, Submission & 24-hour SLA
    # ------------------------------------------------------------------
    def _get_active_db_reservations(self, host_id: str, pool_id: str) -> int:
        with self.db.session_scope() as session:
            rows = session.execute(
                select(CapacityReservationRecord).where(
                    CapacityReservationRecord.host_id == host_id,
                    CapacityReservationRecord.pool_id == pool_id,
                    CapacityReservationRecord.status.in_(["RESERVED", "COMMITTED"]),
                )
            ).scalars().all()
            return sum(int(r.storage_quota_gb or 0) for r in rows)

    def _get_active_gpu_reservations(self, host_id: str) -> Dict[str, List[Dict[str, Any]]]:
        with self.db.session_scope() as session:
            rows = session.execute(
                select(CapacityReservationRecord).where(
                    CapacityReservationRecord.host_id == host_id,
                    CapacityReservationRecord.status.in_(["RESERVED", "COMMITTED"]),
                    CapacityReservationRecord.gpu_uuid.is_not(None),
                )
            ).scalars().all()
            out: Dict[str, List[Dict[str, Any]]] = {}
            for r in rows:
                out.setdefault(str(r.gpu_uuid), []).append({"member_id": r.member_id, "gpu_mode": r.gpu_mode})
            return out

    def get_host_pool_distribution(
        self,
        host_id: str = DEFAULT_TARGET_HOST_ID,
        *,
        required_storage_gb: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """
        Compute live allocatable free space, assigned member count, and balanced score
        for every storage pool on `host_id` so new allocations are distributed fairly
        across eligible disks by both free space and allocated member count.
        """
        labops_service.ensure_members_fresh()
        hid = str(host_id or DEFAULT_TARGET_HOST_ID).strip().lower()
        pools = storage_service.get_storage_pools(hid)

        def _canonical_pool_id(raw_pid: str) -> str:
            q = str(raw_pid or "").strip()
            if not q:
                return ""
            for p in pools:
                p_sid = str(p.get("storage_id") or p.get("id") or "").strip()
                if p_sid == q:
                    return p_sid
            from app.services.storage_inventory_service import _pool_matches_identifier

            for p in pools:
                if _pool_matches_identifier(p, q):
                    return str(p.get("storage_id") or p.get("id") or q).strip()
            return q

        res_gib_by_pool: Dict[str, int] = {}
        res_members_by_pool: Dict[str, set[str]] = {}
        with self.db.session_scope() as session:
            rows = session.execute(
                select(CapacityReservationRecord).where(
                    CapacityReservationRecord.host_id == hid,
                    CapacityReservationRecord.status.in_(["RESERVED", "COMMITTED"]),
                )
            ).scalars().all()
            for r in rows:
                pid = _canonical_pool_id(str(r.pool_id or ""))
                if not pid:
                    continue
                res_gib_by_pool[pid] = res_gib_by_pool.get(pid, 0) + int(r.storage_quota_gb or 0)
                if r.member_id:
                    res_members_by_pool.setdefault(pid, set()).add(str(r.member_id))

        dist: List[Dict[str, Any]] = []
        raw_mem_res = getattr(storage_service, "_reservations", {}) or {}
        mem_res_map: Dict[str, int] = {}
        for k, v in raw_mem_res.items():
            ck = _canonical_pool_id(str(k))
            if ck:
                mem_res_map[ck] = mem_res_map.get(ck, 0) + int(v or 0)
        inactive_statuses = set(ACTIVE_MEMBER_BAD_STATUSES) | {"EXPIRED"}

        for p in pools:
            pid = str(p.get("storage_id") or p.get("id") or "").strip()
            mount = str(p.get("mount_point") or "").rstrip("/")
            runtime_members: set[str] = set()
            for m in getattr(labops_service, "members", []) or []:
                m_host = str(m.get("assigned_server") or "").strip().lower()
                m_status = str(m.get("status") or "ACTIVE").strip().upper()
                if m_host != hid or m_status in inactive_statuses:
                    continue
                m_pool = _canonical_pool_id(str(m.get("storage_id") or m.get("storage_pool_id") or "").strip())
                m_mount = str(m.get("storage_mount_point") or m.get("host_storage_root") or "").rstrip("/")
                m_ws_dir = str(
                    m.get("member_storage_directory")
                    or m.get("workspace_dir")
                    or m.get("host_storage_directory")
                    or ""
                )
                matched = False
                if pid and m_pool == pid:
                    matched = True
                elif mount and mount != "/" and (
                    m_mount == mount
                    or m_ws_dir.startswith(f"{mount}/")
                    or m_ws_dir == mount
                ):
                    matched = True
                if matched:
                    m_key = str(m.get("id") or m.get("username") or "").strip()
                    if m_key:
                        runtime_members.add(m_key)

            assigned_members = set(res_members_by_pool.get(pid, set())) | runtime_members
            assigned_count = len(assigned_members)
            db_res_gb = int(res_gib_by_pool.get(pid, 0))
            mem_res_gb = int(mem_res_map.get(pid, 0))
            committed_gib = float(max(db_res_gb, mem_res_gb))
            raw_alloc_gib = float(p.get("allocatable_gib") or 0.0)
            net_alloc_gib = round(max(0.0, raw_alloc_gib - committed_gib), 2)
            # Fair balancing score: available GiB per allocated member (+1 for upcoming allocation)
            effective_free_per_member = round(net_alloc_gib / float(assigned_count + 1), 2)
            p_rank, p_tier = get_pool_priority_tier(p)
            is_eligible_pool = bool(
                p.get("eligible")
                and not p.get("read_only")
                and p.get("mounted", True)
                and p.get("filesystem_uuid")
                and net_alloc_gib >= float(required_storage_gb)
            )
            dist.append(
                {
                    "host_id": hid,
                    "pool_id": pid,
                    "mount_point": p.get("mount_point") or "",
                    "device": p.get("device") or "",
                    "filesystem_uuid": p.get("filesystem_uuid") or "",
                    "allocatable_gib": round(raw_alloc_gib, 2),
                    "db_reserved_gib": db_res_gb,
                    "committed_gib": round(committed_gib, 2),
                    "net_allocatable_gib": net_alloc_gib,
                    "assigned_member_count": assigned_count,
                    "effective_free_per_member_gib": effective_free_per_member,
                    "priority_rank": p_rank,
                    "priority_tier": p_tier,
                    "eligible": bool(p.get("eligible")),
                    "eligible_for_request": is_eligible_pool,
                    "pool": p,
                }
            )
        return dist

    def compute_resource_recommendation(
        self,
        *,
        member_category: str,
        requested_duration_days: Optional[int],
        gpu_requested: bool,
    ) -> Dict[str, Any]:
        default_cpu = int(os.getenv("MRAM_DEFAULT_CPU_CORES", "4"))
        default_ram_raw = int(os.getenv("MRAM_DEFAULT_RAM_GB", "16"))
        student_max_ram = int(os.getenv("MRAM_MAX_STUDENT_RAM_GB", str(STUDENT_MAX_RAM_GB)))
        if member_category == "STUDENT":
            default_ram = min(default_ram_raw, student_max_ram)
            default_storage = int(os.getenv("MRAM_DEFAULT_STUDENT_STORAGE_GB", str(STUDENT_DEFAULT_STORAGE_GB)))
        else:
            default_ram = default_ram_raw
            default_storage = int(os.getenv("MRAM_DEFAULT_LECTURER_STORAGE_GB", "100"))
        default_gpu_vram = int(os.getenv("MRAM_DEFAULT_SHARED_GPU_VRAM_GB", "12"))
        duration = requested_duration_days if member_category == "STUDENT" else None
        if member_category == "STUDENT" and duration is None:
            duration = int(os.getenv("MRAM_DEFAULT_STUDENT_DURATION_DAYS", "30"))

        hosts = labops_service.get_v2_hosts()
        # Prefer online hosts dynamically; always prefer workstation first when available
        eligible_hosts = [
            h for h in hosts
            if str(h.get("observed_state") or h.get("freshness") or h.get("freshness_status") or h.get("status") or "").upper()
            in {"ONLINE", "ACTIVE", "DEGRADED"}
        ]
        eligible_hosts.sort(key=lambda h: (0 if str(h.get("id") or "").lower() == DEFAULT_TARGET_HOST_ID else 1, str(h.get("id"))))

        chosen_host: Optional[Dict[str, Any]] = None
        chosen_entry: Optional[Dict[str, Any]] = None
        chosen_dist: List[Dict[str, Any]] = []
        manual_reason: Optional[str] = None

        for h in eligible_hosts:
            hid = str(h["id"])
            dist = self.get_host_pool_distribution(hid, required_storage_gb=float(default_storage))
            candidates = [d for d in dist if d["eligible_for_request"]]
            if candidates:
                # 2-tier priority + fair load balancing across physical disks on the host:
                # 0. Priority tier (Tier 0 PREFERRED: sdd/sdb before Tier 1 FALLBACK_RESTRICTED: /mnt/HDD3)
                # 1. Highest effective free GiB per member (net_allocatable_gib / (assigned_member_count + 1))
                # 2. Fewest assigned members
                # 3. Highest raw net free GiB
                # 4. Deterministic pool_id tie-breaker
                candidates.sort(
                    key=lambda d: (
                        int(d.get("priority_rank", 0)),
                        -float(d["effective_free_per_member_gib"]),
                        int(d["assigned_member_count"]),
                        -float(d["net_allocatable_gib"]),
                        str(d["pool_id"]),
                    )
                )
                chosen_host = h
                chosen_entry = candidates[0]
                chosen_dist = [
                    {
                        "pool_id": d["pool_id"],
                        "mount_point": d["mount_point"],
                        "assigned_member_count": d["assigned_member_count"],
                        "net_allocatable_gib": d["net_allocatable_gib"],
                        "effective_free_per_member_gib": d["effective_free_per_member_gib"],
                        "priority_rank": int(d.get("priority_rank", 0)),
                        "priority_tier": d.get("priority_tier", "PREFERRED"),
                        "eligible": d["eligible_for_request"],
                    }
                    for d in sorted(
                        dist,
                        key=lambda x: (
                            0 if x["eligible_for_request"] else 1,
                            int(x.get("priority_rank", 0)),
                            -float(x["effective_free_per_member_gib"]),
                            int(x["assigned_member_count"]),
                            str(x["pool_id"]),
                        ),
                    )
                ]
                break

        chosen_pool = chosen_entry["pool"] if chosen_entry else None
        manual_review = chosen_host is None or chosen_pool is None
        if manual_review:
            if not eligible_hosts:
                manual_reason = "Hiện không có máy chủ ONLINE/verified nào để đề xuất tự động; Admin cần chọn thủ công."
            else:
                manual_reason = f"Không tìm thấy storage pool đủ {default_storage} GiB khả dụng (kể cả phần đã giữ chỗ); cần Admin kiểm tra."

        rec_gpu_mode = "SHARED" if gpu_requested else "NONE"
        rec_gpu_uuid: Optional[str] = None
        if gpu_requested and chosen_host:
            cid = str(chosen_host["id"]).lower()
            matching_gpus = [
                g for g in labops_service.gpus
                if str(g.get("server_id") or g.get("host_id") or cid).lower() == cid
            ]
            if matching_gpus:
                rec_gpu_uuid = str(matching_gpus[0].get("uuid") or matching_gpus[0].get("id") or "") or None
            elif cid == "workstation":
                rec_gpu_uuid = "GPU-workstation-a30-01"

        return {
            "recommended_host_id": chosen_host["id"] if chosen_host else None,
            "recommended_pool_id": (chosen_pool.get("storage_id") or chosen_pool.get("id")) if chosen_pool else None,
            "recommended_filesystem_uuid": chosen_pool.get("filesystem_uuid") if chosen_pool else None,
            "recommended_mount_point": chosen_pool.get("mount_point") if chosen_pool else None,
            "cpu_cores": default_cpu,
            "ram_gb": default_ram,
            "storage_quota_gb": default_storage,
            "gpu_mode": rec_gpu_mode,
            "gpu_uuid": rec_gpu_uuid,
            "gpu_vram_gb": default_gpu_vram if rec_gpu_mode == "SHARED" else None,
            "duration_days": duration,
            "manual_review_required": manual_review,
            "rationale": {
                "profile": f"Default {default_cpu} vCPU shared / {default_ram} GiB RAM / {default_storage} GiB storage / GPU={rec_gpu_mode}",
                "balancing_strategy": "FAIR_FREE_SPACE_AND_MEMBER_COUNT",
                "student_max_ram_gb": student_max_ram if member_category == "STUDENT" else None,
                "chosen_pool_assigned_members": chosen_entry["assigned_member_count"] if chosen_entry else None,
                "chosen_pool_net_allocatable_gib": chosen_entry["net_allocatable_gib"] if chosen_entry else None,
                "chosen_pool_effective_free_per_member_gib": chosen_entry["effective_free_per_member_gib"] if chosen_entry else None,
                "pool_distribution": chosen_dist,
                "gpu_requested_by_member": gpu_requested,
                "gpu_vram_gb": default_gpu_vram if rec_gpu_mode == "SHARED" else None,
                "manual_reason": manual_reason,
                "evaluated_hosts": [h.get("id") for h in eligible_hosts],
            },
        }

    def _build_review_url(self, request_id: str, raw_token: Optional[str] = None) -> str:
        base = f"{self._public_base_url()}/dashboard/ssh-requests/{request_id}/review"
        return f"{base}?invite={raw_token}" if raw_token else base

    def _target_approver_zalo_id(self) -> str:
        return (os.getenv("MRAM_APPROVER_ZALO_ID") or TARGET_APPROVER_ZALO_ID).strip()

    def _issue_and_notify_zalo_invitation(
        self,
        *,
        req: ServerAccessRequestRecord,
        prof: MemberProfileRecord,
        rec: RequestResourceRecommendationRecord,
        supervisor_name: str,
    ) -> Dict[str, Any]:
        """Issue 32-byte CSPRNG approval invitation (hash in DB) and DM verified approver only."""
        target_zalo_id = self._target_approver_zalo_id()
        raw_token = secrets.token_urlsafe(32)
        tok_hash = _hash_token(raw_token)
        now = _utcnow()
        inv_exp = min(now + timedelta(hours=REVIEW_SLA_HOURS), _as_utc(req.review_expires_at) or (now + timedelta(hours=REVIEW_SLA_HOURS)))
        inv_id = f"inv-{uuid.uuid4().hex[:10]}"

        with self.db.session_scope() as session:
            # Revoke older active invitations for the same request
            olds = session.execute(
                select(ApprovalInvitationRecord).where(
                    ApprovalInvitationRecord.request_id == req.id,
                    ApprovalInvitationRecord.status == "ACTIVE",
                )
            ).scalars().all()
            for o in olds:
                o.status = "REVOKED"
                o.revoked_at = now

            session.add(
                ApprovalInvitationRecord(
                    id=inv_id,
                    request_id=req.id,
                    request_version=req.version,
                    admin_username="admin",
                    admin_zalo_id=target_zalo_id,
                    token_hash=tok_hash,
                    status="ACTIVE",
                    expires_at=inv_exp,
                    issued_at=now,
                )
            )

        review_link = self._build_review_url(req.id, raw_token)
        reason_snippet = (req.registration_reason or req.ssh_purpose or "").strip().replace("\n", " ")[:180]
        rat = dict(rec.rationale_json or {})
        pool_lbl = (
            f"{rec.recommended_pool_id} ({rec.recommended_mount_point})"
            if rec.recommended_pool_id
            else "Chờ chọn"
        )
        pool_extra = ""
        if rat.get("chosen_pool_assigned_members") is not None and rat.get("chosen_pool_net_allocatable_gib") is not None:
            pool_extra = f" | Đang có {rat['chosen_pool_assigned_members']} TV, trống {rat['chosen_pool_net_allocatable_gib']} GiB"
        rec_summary = (
            f"Host: {rec.recommended_host_id or 'workstation'} | Pool: {pool_lbl}{pool_extra} | "
            f"{rec.cpu_cores} vCPU | {rec.ram_gb} GiB RAM | {rec.storage_quota_gb} GiB Storage | GPU: {rec.gpu_mode}"
        )

        # Verify target recipient strictly via ZaloIdentityRBACService (no group/wildcard broadcast)
        delivery_status = "RECIPIENT_NOT_VERIFIED"
        delivery_detail = ""
        provider_msg_id: Optional[str] = None
        try:
            from app.services.zalo_assistant.service import zalo_assistant_service

            ver = zalo_assistant_service.identity_rbac.ensure_configured_approver_verified(target_zalo_id)
            if ver.get("verified") and ver.get("chat_id"):
                chat_id = str(ver["chat_id"])
                v4_notif_enabled = False
                try:
                    from app.services.assistant_v4.notification_policy import resolve_notification_owner

                    v4_obj = getattr(zalo_assistant_service, "v4", None)
                    v4_cfg = getattr(v4_obj, "config", None) if v4_obj is not None else None
                    if v4_cfg is None:
                        from app.services.assistant_v4.config import load_assistant_v4_config

                        v4_cfg = load_assistant_v4_config()
                    v4_notif_enabled = resolve_notification_owner(v4_cfg) == "V4_ACTIVE"
                except Exception:
                    v4_notif_enabled = False

                if v4_notif_enabled:
                    delivery_status = "DELEGATED_TO_V4_POLICY"
                    delivery_detail = (
                        f"Đơn {req.id} được xử lý qua V4 EventPolicyEngine cho "
                        f"{ver.get('display_name') or TARGET_APPROVER_NAME} ({chat_id}); bỏ qua V2 để tránh gửi trùng"
                    )
                else:
                    msg = (
                        f"📩 YÊU CẦU CẤP TÀI KHOẢN SSH MỚI ({req.id})\n"
                        f"━━━━━━━━━━━━━━━━━━━━\n"
                        f"• Thành viên: {prof.full_name} (@{prof.username}, mã: {prof.member_code or 'N/A'})\n"
                        f"• Phân loại: {prof.member_category} | GVHD: {supervisor_name}\n"
                        f"• Dự án: {req.project_title}\n"
                        f"• Lý do: {reason_snippet}\n"
                        f"• Cấu hình đề xuất: {rec_summary}\n"
                        f"• Thời điểm gửi: {_fmt_vn(req.submitted_at)}\n"
                        f"• Hạn duyệt (24h): {_fmt_vn(req.review_expires_at)}\n"
                        f"💬 Phản hồi tự nhiên cho AI Agent ngay trên Zalo (Agent sẽ hỏi xác nhận lại 1 lần):\n"
                        f"  • \"@{prof.username} đồng ý duyệt cấp tài nguyên mặc định\"\n"
                        f"  • \"@{prof.username} tài khoản này tôi từ chối cấp vì lý do ...\"\n"
                        f"🔗 Hoặc duyệt trực tiếp trên Dashboard:\n{review_link}"
                    )
                    enqueued = zalo_assistant_service.notification_dispatcher.enqueue(
                        idempotency_key=f"mram-req:{req.id}:v{req.version}:{inv_id}",
                        recipient_id=chat_id,
                        event_type="MRAM_SSH_REQUEST_SUBMITTED",
                        severity="P1",
                        is_critical=False,
                        message_text=msg,
                        correlation_id=req.id,
                    )
                    delivery_status = "QUEUED_FOR_VERIFIED_ADMIN"
                    delivery_detail = f"Đã xếp hàng gửi riêng tới {ver.get('display_name') or TARGET_APPROVER_NAME} ({chat_id})"

                    bot_token = (os.getenv("ZALO_BOT_TOKEN") or getattr(zalo_assistant_service.bot_engine, "bot_token", "") or "").strip()
                    if (
                        os.getenv("MRAM_IMMEDIATE_ZALO_SEND", "").strip() == "1"
                        and "PYTEST_CURRENT_TEST" not in os.environ
                        and bot_token
                    ):
                        try:
                            import asyncio
                            import concurrent.futures
                            from zalo_bot import Bot

                            def _sync_send() -> Optional[str]:
                                async def _do_send() -> Optional[str]:
                                    bot = Bot(token=bot_token)
                                    sent = await bot.send_message(chat_id=chat_id, text=msg)
                                    mid = getattr(sent, "message_id", None)
                                    return str(mid) if mid is not None else "accepted"

                                return asyncio.run(_do_send())

                            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                                provider_msg_id = pool.submit(_sync_send).result(timeout=15)
                            outbox_id = enqueued.get("id")
                            if outbox_id:
                                with self.db.session_scope() as session:
                                    out_rec = session.get(NotificationOutboxRecord, outbox_id)
                                    if out_rec is not None:
                                        out_rec.status = "SENT"
                                        out_rec.attempt_count = max(1, int(out_rec.attempt_count or 0))
                                        out_rec.provider_message_id = provider_msg_id
                                        out_rec.sent_at = _utcnow()
                                        out_rec.next_retry_at = None
                                        out_rec.last_error = None
                            delivery_status = "SENT"
                            delivery_detail = f"Đã gửi Zalo DM tới {ver.get('display_name') or TARGET_APPROVER_NAME} ({chat_id}), msg_id={provider_msg_id}"
                        except Exception as send_exc:
                            delivery_detail = f"Đã xếp hàng; gửi trực tiếp gặp lỗi: {send_exc}"
            else:
                delivery_detail = str(ver.get("reason") or "Recipient not verified in ZaloIdentityLinkRecord")
        except Exception as exc:
            delivery_detail = str(exc)

        return {
            "invitation_id": inv_id,
            "delivery_status": delivery_status,
            "delivery_detail": delivery_detail,
            "provider_message_id": provider_msg_id,
            "review_link": review_link,
            "target_zalo_id": target_zalo_id,
            "expires_at": inv_exp.isoformat(),
        }

    def notify_pending_ssh_request_zalo(self, request_id: str, *, actor: str = "admin") -> Dict[str, Any]:
        """Re-issue a fresh 1-time Zalo invitation for a pending SSH request and dispatch DM to verified Admin."""
        self.expire_overdue_requests()
        with self.db.session_scope() as session:
            req = session.get(ServerAccessRequestRecord, request_id)
            if req is None:
                raise LookupError(f"Không tìm thấy yêu cầu SSH '{request_id}'")
            if req.status not in {"PENDING_APPROVAL", "NEED_INFO"}:
                raise MramConflictError(
                    "REQUEST_NOT_PENDING",
                    f"Đơn {request_id} đang ở trạng thái {req.status}, không cần gửi lại thông báo phê duyệt.",
                    409,
                )
            prof = session.get(MemberProfileRecord, req.member_id)
            rec = session.execute(
                select(RequestResourceRecommendationRecord)
                .where(RequestResourceRecommendationRecord.request_id == req.id)
                .order_by(RequestResourceRecommendationRecord.request_version.desc())
            ).scalars().first()
            sup_name = ""
            if prof and prof.primary_supervisor_id:
                sup_prof = session.get(MemberProfileRecord, prof.primary_supervisor_id)
                if sup_prof and sup_prof.full_name:
                    sup_name = _normalize_supervisor_name(sup_prof.full_name) or sup_prof.full_name
            if not sup_name:
                sup_name = _normalize_supervisor_name((req.metadata_json or {}).get("supervisor_name")) or "Chưa phân công GVHD"

        if prof is None or rec is None:
            raise LookupError(f"Thiếu hồ sơ hoặc cấu hình đề xuất cho đơn '{request_id}'")

        res = self._issue_and_notify_zalo_invitation(req=req, prof=prof, rec=rec, supervisor_name=sup_name)
        labops_service._audit(
            actor,
            "MRAM_ZALO_INVITATION_RESENT",
            "SSH_REQUEST",
            request_id,
            "SUCCESS" if res.get("delivery_status") in {"SENT", "QUEUED_FOR_VERIFIED_ADMIN"} else "WARNING",
            f"Gửi lại thông báo phê duyệt Zalo ({res.get('delivery_status')}): {res.get('delivery_detail')}",
        )
        labops_service.save_audit_logs()
        return {"request_id": request_id, "zalo_notification": res}

    def submit_ssh_request(
        self,
        *,
        username: str,
        payload: Dict[str, Any],
        client_ip: str = "",
        request_type: str = "INITIAL",
        parent_request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        for bad_key in FORBIDDEN_REQUEST_CLIENT_KEYS:
            if bad_key in payload:
                raise MramValidationError(
                    "FORBIDDEN_FIELD_OVERRIDE",
                    f"Không được phép tự truyền hoặc giả mạo trường '{bad_key}' từ client",
                    400,
                )

        uname = str(username or "").strip().lower()
        labops_service.ensure_members_fresh()
        member = next((m for m in labops_service.members if (m.get("username") or "").lower() == uname), None)
        if not member:
            raise LookupError(f"Không tìm thấy hồ sơ thành viên '{uname}'")
        if member.get("status") in ACTIVE_MEMBER_BAD_STATUSES:
            raise MramValidationError("ACCOUNT_INACTIVE", "Tài khoản không ở trạng thái hợp lệ để gửi yêu cầu", 403)

        prof = self._ensure_legacy_profile(member)
        self.expire_overdue_requests()

        with self.db.session_scope() as session:
            open_req = session.execute(
                select(ServerAccessRequestRecord).where(
                    ServerAccessRequestRecord.member_id == prof.member_id,
                    ServerAccessRequestRecord.status.in_(list(OPEN_INITIAL_STATUSES)),
                )
            ).scalar_one_or_none()
            if open_req is not None:
                raise MramConflictError(
                    "OPEN_REQUEST_EXISTS",
                    f"Bạn đã có yêu cầu đang xử lý ({open_req.id} — trạng thái {open_req.status}). Không thể tạo yêu cầu trùng.",
                    409,
                    {"existing_request_id": open_req.id, "status": open_req.status},
                )

        project_title = str(payload.get("project_title") or "").strip()
        registration_reason = str(payload.get("registration_reason") or "").strip()
        ssh_purpose = str(payload.get("ssh_purpose") or registration_reason).strip()
        expected_tasks_raw = payload.get("expected_tasks")
        if isinstance(expected_tasks_raw, list):
            expected_tasks = ", ".join(str(x).strip() for x in expected_tasks_raw if str(x).strip())
        else:
            expected_tasks = str(expected_tasks_raw or registration_reason or project_title).strip()
        gpu_requested = bool(payload.get("gpu_requested", False))
        gpu_justification = str(payload.get("gpu_justification") or ("Cần GPU để huấn luyện mô hình nghiên cứu" if gpu_requested else "")).strip()
        policy_accepted = bool(payload.get("policy_accepted", True))
        dur_raw = payload.get("requested_duration_days")

        if not policy_accepted:
            raise MramValidationError("POLICY_NOT_ACCEPTED", "Bạn phải đọc và xác nhận chính sách an toàn trước khi gửi")
        if len(project_title) < 3:
            raise MramValidationError("INVALID_PROJECT_TITLE", "Tên dự án/đề tài nghiên cứu phải có ít nhất 3 ký tự")
        if len(registration_reason) < 10:
            raise MramValidationError("INVALID_REGISTRATION_REASON", "Lý do cần sử dụng máy chủ phải có ít nhất 10 ký tự")

        sup_id = str(payload.get("primary_supervisor_id") or prof.primary_supervisor_id or "").strip() or None
        sup_name_input = _normalize_supervisor_name(payload.get("supervisor_name"))
        direct_mgr_id = str(payload.get("direct_manager_id") or prof.direct_manager_id or "").strip() or None
        sup_name = sup_name_input or "N/A"

        if not sup_id and sup_name_input:
            matched_lec = next(
                (
                    l for l in self.list_lecturers()
                    if l["full_name"].strip().lower() == sup_name_input.lower()
                    or l["username"].strip().lower() == sup_name_input.lower()
                ),
                None,
            )
            if matched_lec:
                sup_id = str(matched_lec["member_id"])

        if prof.member_category == "STUDENT":
            if not sup_id and len(sup_name_input) < 2:
                raise MramValidationError("MISSING_SUPERVISOR", "Sinh viên bắt buộc nhập tên Giảng viên hướng dẫn")
            if sup_id:
                sup_prof = self._validate_lecturer_id(sup_id)
                sup_name = str(sup_prof.get("full_name") or sup_prof.get("username"))
            if dur_raw is None or str(dur_raw).strip() == "":
                requested_duration_days: Optional[int] = 30
            else:
                requested_duration_days = int(dur_raw)
            if requested_duration_days < 7 or requested_duration_days > 180:
                raise MramValidationError("INVALID_DURATION", "Thời hạn SSH của sinh viên phải từ 7 đến 180 ngày (mặc định 30 ngày)")
        else:
            # LECTURER: SSH expiration is optional (no mandatory expiration)
            if sup_id:
                sup_prof = self._validate_lecturer_id(sup_id)
                sup_name = str(sup_prof.get("full_name") or sup_prof.get("username"))
            requested_duration_days = int(dur_raw) if (dur_raw is not None and str(dur_raw).strip() != "") else None

        rec_data = self.compute_resource_recommendation(
            member_category=prof.member_category,
            requested_duration_days=requested_duration_days,
            gpu_requested=gpu_requested,
        )

        now = _utcnow()
        review_deadline = now + timedelta(hours=REVIEW_SLA_HOURS)
        req_id = f"sshreq-{uuid.uuid4().hex[:10]}"
        rec_id = f"reqrec-{uuid.uuid4().hex[:10]}"

        req_row = ServerAccessRequestRecord(
            id=req_id,
            request_type=request_type,
            member_id=prof.member_id,
            username=prof.username,
            member_category=prof.member_category,
            primary_supervisor_id=sup_id,
            direct_manager_id=direct_mgr_id,
            project_title=project_title,
            registration_reason=registration_reason,
            ssh_purpose=ssh_purpose,
            expected_tasks=expected_tasks,
            requested_duration_days=requested_duration_days,
            gpu_requested=gpu_requested,
            gpu_justification=gpu_justification,
            policy_version=POLICY_VERSION,
            status="PENDING_APPROVAL",
            version=1,
            submitted_at=now,
            review_expires_at=review_deadline,
            parent_request_id=parent_request_id,
            metadata_json={"client_ip": client_ip, "supervisor_name": sup_name},
            created_at=now,
            updated_at=now,
        )
        rec_row = RequestResourceRecommendationRecord(
            id=rec_id,
            request_id=req_id,
            request_version=1,
            recommended_host_id=rec_data["recommended_host_id"],
            recommended_pool_id=rec_data["recommended_pool_id"],
            recommended_filesystem_uuid=rec_data["recommended_filesystem_uuid"],
            recommended_mount_point=rec_data["recommended_mount_point"],
            cpu_cores=rec_data["cpu_cores"],
            ram_gb=rec_data["ram_gb"],
            storage_quota_gb=rec_data["storage_quota_gb"],
            gpu_mode=rec_data["gpu_mode"],
            gpu_uuid=rec_data["gpu_uuid"],
            duration_days=rec_data["duration_days"],
            manual_review_required=bool(rec_data["manual_review_required"]),
            rationale_json=rec_data["rationale"],
            created_at=now,
        )

        with self.db.session_scope() as session:
            session.add(req_row)
            session.add(rec_row)
            session.add(
                PolicyAcceptanceRecord(
                    id=f"pol-{uuid.uuid4().hex[:10]}",
                    member_id=prof.member_id,
                    request_id=req_id,
                    policy_version=POLICY_VERSION,
                    policy_hash=POLICY_TEXT_DIGEST,
                    accepted_at=now,
                    client_ip=client_ip,
                )
            )
            db_prof = session.get(MemberProfileRecord, prof.member_id)
            if db_prof:
                db_prof.ssh_access_state = "PENDING_APPROVAL"
                if sup_id:
                    db_prof.primary_supervisor_id = sup_id
                if direct_mgr_id:
                    db_prof.direct_manager_id = direct_mgr_id
                db_prof.updated_at = now
            try:
                from app.services.assistant_v4.event_store import EventEnvelope, insert_event_in_session

                with session.begin_nested():
                    insert_event_in_session(
                        session,
                        EventEnvelope(
                            source="labops-core",
                            source_event_id=f"request:{req_id}:v1:created",
                            event_type="request.created",
                            scope_type="user",
                            scope_id=prof.username,
                            resource_id=req_id,
                            actor_id=prof.username,
                            source_version=1,
                            occurred_at=now,
                            observed_at=now,
                            correlation_id=req_id,
                            payload={
                                "request_id": req_id,
                                "request_type": request_type,
                                "username": prof.username,
                                "member_id": prof.member_id,
                                "full_name": prof.full_name or prof.username,
                                "member_code": prof.member_code,
                                "member_category": prof.member_category,
                                "project_title": project_title,
                                "reason_snippet": registration_reason[:180],
                                "supervisor_id": sup_id,
                                "supervisor_name": sup_name,
                                "submitted_at": now.isoformat(),
                                "review_expires_at": review_deadline.isoformat(),
                                "recommended_host_id": rec_data.get("recommended_host_id"),
                                "recommended_pool_id": rec_data.get("recommended_pool_id"),
                                "recommended_mount_point": rec_data.get("recommended_mount_point"),
                                "recommended_spec": f"{rec_data['cpu_cores']}C/{rec_data['ram_gb']}G/{rec_data['storage_quota_gb']}GiB GPU={rec_data['gpu_mode']}",
                                "review_path": f"/dashboard/ssh-requests/{req_id}/review",
                            },
                        ),
                    )
            except Exception as ev_exc:
                logger.debug("V4 event insert skipped/deferred for %s: %s", req_id, ev_exc)
            session.flush()
            session.expunge(req_row)
            session.expunge(rec_row)

        if prof.member_category == "STUDENT" and sup_id:
            self.assign_supervisor(
                student_member_id=prof.member_id,
                primary_supervisor_id=sup_id,
                direct_manager_id=direct_mgr_id,
                actor=prof.username,
                change_reason=f"Gửi kèm yêu cầu SSH {req_id}",
            )

        member["ssh_access_state"] = "PENDING_APPROVAL"
        member["version"] = int(member.get("version") or 1) + 1
        labops_service._audit(
            prof.username,
            "MRAM_SSH_REQUEST_SUBMITTED",
            "SSH_REQUEST",
            req_id,
            "SUCCESS",
            f"Gửi yêu cầu SSH ({request_type}, hạn duyệt 24h tới {_fmt_vn(review_deadline)})",
        )
        labops_service._save_persistent_data()

        zalo_res = self._issue_and_notify_zalo_invitation(
            req=req_row,
            prof=prof,
            rec=rec_row,
            supervisor_name=sup_name,
        )
        return {
            "request": self.get_request_detail(req_id, viewer_username=prof.username, is_admin=True),
            "zalo_notification": zalo_res,
        }

    def reapply_ssh_request(
        self,
        *,
        username: str,
        request_id: str,
        payload: Optional[Dict[str, Any]] = None,
        client_ip: str = "",
    ) -> Dict[str, Any]:
        with self.db.session_scope() as session:
            prior = session.get(ServerAccessRequestRecord, request_id)
            if not prior or prior.username != username.strip().lower():
                raise LookupError(f"Không tìm thấy yêu cầu '{request_id}' của bạn")
            if prior.status not in {"EXPIRED", "REJECTED"}:
                raise MramConflictError("CANNOT_REAPPLY", f"Chỉ có thể gửi lại từ yêu cầu EXPIRED hoặc REJECTED (hiện tại: {prior.status})")
            base_payload = {
                "primary_supervisor_id": prior.primary_supervisor_id,
                "direct_manager_id": prior.direct_manager_id,
                "project_title": prior.project_title,
                "registration_reason": prior.registration_reason,
                "ssh_purpose": prior.ssh_purpose,
                "expected_tasks": prior.expected_tasks,
                "requested_duration_days": prior.requested_duration_days,
                "gpu_requested": prior.gpu_requested,
                "gpu_justification": prior.gpu_justification,
                "policy_accepted": True,
            }
        if payload:
            base_payload.update(payload)
        return self.submit_ssh_request(
            username=username,
            payload=base_payload,
            client_ip=client_ip,
            request_type="INITIAL" if prior.request_type == "INITIAL" else prior.request_type,
            parent_request_id=request_id,
        )

    def submit_renewal_request(self, *, username: str, payload: Dict[str, Any], client_ip: str = "") -> Dict[str, Any]:
        return self.submit_ssh_request(
            username=username,
            payload=payload,
            client_ip=client_ip,
            request_type="RENEWAL",
            parent_request_id=str(payload.get("parent_request_id") or "").strip() or None,
        )

    def expire_overdue_requests(self, now_override: Optional[datetime] = None) -> List[str]:
        """Sweep PENDING_APPROVAL / NEED_INFO requests past review_expires_at (24h SLA)."""
        labops_service.ensure_members_fresh()
        now = _as_utc(now_override) or _utcnow()
        expired_ids: List[str] = []
        expired_usernames: List[str] = []
        with self.db.session_scope() as session:
            candidates = session.execute(
                select(ServerAccessRequestRecord).where(
                    ServerAccessRequestRecord.status.in_(["PENDING_APPROVAL", "NEED_INFO"])
                )
            ).scalars().all()
            for r in candidates:
                exp = _as_utc(r.review_expires_at)
                if exp and now > exp:
                    r.status = "EXPIRED"
                    r.decision_reason = "Quá hạn 24 giờ xét duyệt (SLA expired)"
                    r.decided_at = now
                    r.decided_by = "system-sla-worker"
                    r.updated_at = now
                    expired_ids.append(r.id)
                    expired_usernames.append(str(r.username or "").strip().lower())

                    invs = session.execute(
                        select(ApprovalInvitationRecord).where(
                            ApprovalInvitationRecord.request_id == r.id,
                            ApprovalInvitationRecord.status == "ACTIVE",
                        )
                    ).scalars().all()
                    for inv in invs:
                        inv.status = "EXPIRED"
                        inv.revoked_at = now

                    prof = session.get(MemberProfileRecord, r.member_id)
                    if prof and prof.ssh_access_state == "PENDING_APPROVAL":
                        prof.ssh_access_state = "EXPIRED_REVIEW"
                    try:
                        from app.services.assistant_v4.event_store import EventEnvelope, insert_event_in_session

                        with session.begin_nested():
                            insert_event_in_session(
                                session,
                                EventEnvelope(
                                    source="labops-core",
                                    source_event_id=f"request:{r.id}:v{r.version}:overdue",
                                    event_type="request.overdue",
                                    scope_type="user",
                                    scope_id=r.username,
                                    resource_id=r.id,
                                    actor_id="system-sla-worker",
                                    source_version=int(r.version),
                                    occurred_at=now,
                                    observed_at=now,
                                    correlation_id=r.id,
                                    payload={
                                        "request_id": r.id,
                                        "username": r.username,
                                        "project_title": r.project_title,
                                        "supervisor_id": r.primary_supervisor_id,
                                        "review_expires_at": exp.isoformat() if exp else None,
                                        "review_path": f"/dashboard/ssh-requests/{r.id}/review",
                                    },
                                ),
                            )
                    except Exception:
                        pass

        for uname in expired_usernames:
            m = next((x for x in labops_service.members if (x.get("username") or "").strip().lower() == uname), None)
            if m and m.get("ssh_access_state") == "PENDING_APPROVAL":
                m["ssh_access_state"] = "EXPIRED_REVIEW"
                m["version"] = int(m.get("version") or 1) + 1
        for rid in expired_ids:
            labops_service._audit("system", "MRAM_REQUEST_SLA_EXPIRED", "SSH_REQUEST", rid, "EXPIRED", "Yêu cầu tự động hết hạn sau 24 giờ chưa duyệt")
        if expired_ids:
            labops_service._save_persistent_data()
        return expired_ids

    # ------------------------------------------------------------------
    # P4: Pre-validation, Admin Approval, Capacity Reservation, Invites
    # ------------------------------------------------------------------
    def _serialize_request(
        self,
        r: ServerAccessRequestRecord,
        rec: Optional[RequestResourceRecommendationRecord] = None,
        approvals: Optional[List[RequestApprovalRecord]] = None,
        prof: Optional[MemberProfileRecord] = None,
        sup_prof: Optional[MemberProfileRecord] = None,
        prov_op: Optional[ProvisioningOperationRecord] = None,
    ) -> Dict[str, Any]:
        now = _utcnow()
        exp = _as_utc(r.review_expires_at)
        rem_sec = max(0, int((exp - now).total_seconds())) if (exp and r.status in {"PENDING_APPROVAL", "NEED_INFO"}) else 0
        latest_app = approvals[0] if approvals else None
        raw_meta_sup = _normalize_supervisor_name((r.metadata_json or {}).get("supervisor_name"))
        prof_sup_name = _normalize_supervisor_name(sup_prof.full_name) if sup_prof else ""
        return {
            "id": r.id,
            "request_type": r.request_type,
            "member_id": r.member_id,
            "username": r.username,
            "full_name": prof.full_name if prof else r.username,
            "email": prof.email if prof else "",
            "member_code": prof.member_code if prof else "",
            "department_class": prof.department_class if prof else "",
            "member_category": r.member_category,
            "primary_supervisor_id": r.primary_supervisor_id,
            "supervisor_name": prof_sup_name or (sup_prof.full_name if sup_prof else None) or raw_meta_sup or None,
            "direct_manager_id": r.direct_manager_id,
            "project_title": r.project_title,
            "registration_reason": r.registration_reason,
            "ssh_purpose": r.ssh_purpose,
            "expected_tasks": r.expected_tasks,
            "requested_duration_days": r.requested_duration_days,
            "gpu_requested": bool(r.gpu_requested),
            "gpu_justification": r.gpu_justification,
            "policy_version": r.policy_version,
            "status": r.status,
            "version": r.version,
            "submitted_at": _as_utc(r.submitted_at).isoformat() if r.submitted_at else None,
            "review_expires_at": exp.isoformat() if exp else None,
            "remaining_review_seconds": rem_sec,
            "decided_at": _as_utc(r.decided_at).isoformat() if r.decided_at else None,
            "decided_by": r.decided_by,
            "decision_reason": r.decision_reason,
            "parent_request_id": r.parent_request_id,
            "recommendation": {
                "recommended_host_id": rec.recommended_host_id,
                "recommended_pool_id": rec.recommended_pool_id,
                "recommended_filesystem_uuid": rec.recommended_filesystem_uuid,
                "recommended_mount_point": rec.recommended_mount_point,
                "cpu_cores": rec.cpu_cores,
                "ram_gb": rec.ram_gb,
                "storage_quota_gb": rec.storage_quota_gb,
                "gpu_mode": rec.gpu_mode,
                "gpu_uuid": rec.gpu_uuid,
                "gpu_vram_gb": (rec.rationale_json or {}).get("gpu_vram_gb", 12 if rec.gpu_mode == "SHARED" else None),
                "duration_days": rec.duration_days,
                "manual_review_required": rec.manual_review_required,
                "rationale": rec.rationale_json,
            } if rec else None,
            "latest_approval": {
                "id": latest_app.id,
                "decision": latest_app.decision,
                "approver_username": latest_app.approver_username,
                "reason": latest_app.reason,
                "approved_config": latest_app.approved_config_json,
                "config_hash": latest_app.config_hash,
                "decided_at": _as_utc(latest_app.decided_at).isoformat() if latest_app.decided_at else None,
            } if latest_app else None,
            "provisioning": {
                "id": prov_op.id,
                "status": prov_op.status,
                "steps": prov_op.steps_json,
                "verification": prov_op.verification_json,
                "last_error": prov_op.last_error,
                "completed_at": _as_utc(prov_op.completed_at).isoformat() if prov_op.completed_at else None,
            } if prov_op else None,
        }

    def list_requests_for_member(self, username: str) -> List[Dict[str, Any]]:
        self.expire_overdue_requests()
        uname = str(username or "").strip().lower()
        with self.db.session_scope() as session:
            rows = session.execute(
                select(ServerAccessRequestRecord)
                .where(ServerAccessRequestRecord.username == uname)
                .order_by(ServerAccessRequestRecord.submitted_at.desc())
            ).scalars().all()
            return [self._hydrate_request_in_session(session, r) for r in rows]

    def list_requests_for_admin(self, status_filter: Optional[str] = None) -> List[Dict[str, Any]]:
        self.expire_overdue_requests()
        with self.db.session_scope() as session:
            stmt = select(ServerAccessRequestRecord).order_by(ServerAccessRequestRecord.submitted_at.desc())
            if status_filter and status_filter.upper() != "ALL":
                stmt = stmt.where(ServerAccessRequestRecord.status == status_filter.upper())
            rows = session.execute(stmt).scalars().all()
            return [self._hydrate_request_in_session(session, r) for r in rows]

    def _hydrate_request_in_session(self, session: Any, r: ServerAccessRequestRecord) -> Dict[str, Any]:
        rec = session.execute(
            select(RequestResourceRecommendationRecord)
            .where(RequestResourceRecommendationRecord.request_id == r.id)
            .order_by(RequestResourceRecommendationRecord.created_at.desc())
        ).scalars().first()
        apps = session.execute(
            select(RequestApprovalRecord)
            .where(RequestApprovalRecord.request_id == r.id)
            .order_by(RequestApprovalRecord.decided_at.desc())
        ).scalars().all()
        prof = session.get(MemberProfileRecord, r.member_id)
        sup_prof = session.get(MemberProfileRecord, r.primary_supervisor_id) if r.primary_supervisor_id else None
        prov_op = session.execute(
            select(ProvisioningOperationRecord)
            .where(ProvisioningOperationRecord.request_id == r.id)
            .order_by(ProvisioningOperationRecord.created_at.desc())
        ).scalars().first()
        return self._serialize_request(r, rec=rec, approvals=list(apps), prof=prof, sup_prof=sup_prof, prov_op=prov_op)

    def get_request_detail(self, request_id: str, viewer_username: str, is_admin: bool = False) -> Dict[str, Any]:
        self.expire_overdue_requests()
        with self.db.session_scope() as session:
            r = session.get(ServerAccessRequestRecord, request_id)
            if not r:
                raise LookupError(f"Không tìm thấy yêu cầu SSH '{request_id}'")
            if not is_admin and r.username != str(viewer_username).strip().lower():
                raise PermissionError("Bạn không có quyền xem yêu cầu của thành viên khác")
            return self._hydrate_request_in_session(session, r)

    def verify_approval_invitation(self, *, request_id: str, raw_token: str, admin_username: str) -> Dict[str, Any]:
        """GET/Preview verification — strictly read-only, NEVER mutates or consumes invitation."""
        self.expire_overdue_requests()
        tok_hash = _hash_token(str(raw_token or "").strip())
        now = _utcnow()
        with self.db.session_scope() as session:
            inv = session.execute(
                select(ApprovalInvitationRecord).where(ApprovalInvitationRecord.token_hash == tok_hash)
            ).scalar_one_or_none()
            if not inv or inv.request_id != request_id:
                return {"valid": False, "reason": "INVALID_INVITATION_TOKEN"}
            if inv.admin_username != admin_username:
                return {"valid": False, "reason": "ADMIN_BINDING_MISMATCH"}
            exp = _as_utc(inv.expires_at)
            if inv.status != "ACTIVE":
                return {"valid": False, "reason": f"INVITATION_{inv.status}"}
            if exp and now > exp:
                return {"valid": False, "reason": "INVITATION_EXPIRED"}
            req = session.get(ServerAccessRequestRecord, request_id)
            if not req or req.version != inv.request_version:
                return {"valid": False, "reason": "REQUEST_VERSION_CHANGED"}
            if req.status not in {"PENDING_APPROVAL", "NEED_INFO"}:
                return {"valid": False, "reason": f"REQUEST_{req.status}"}
            return {
                "valid": True,
                "invitation_id": inv.id,
                "request_id": inv.request_id,
                "request_version": inv.request_version,
                "expires_at": exp.isoformat() if exp else None,
            }

    def validate_request_config(self, request_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
        """Validate Admin's chosen host, storage pool, CPU/RAM/storage/GPU against live state & DB reservations."""
        with self.db.session_scope() as session:
            req = session.get(ServerAccessRequestRecord, request_id)
            if not req:
                raise LookupError(f"Không tìm thấy yêu cầu '{request_id}'")
            req_cat = req.member_category

        host_id = str(config.get("host_id") or "").strip().lower()
        pool_id = str(config.get("pool_id") or "").strip()
        cpu_cores = int(config.get("cpu_cores") or 0)
        ram_gb = int(config.get("ram_gb") or 0)
        storage_quota_gb = int(config.get("storage_quota_gb") or 0)
        gpu_mode = str(config.get("gpu_mode") or "NONE").strip().upper()
        gpu_uuid = str(config.get("gpu_uuid") or "").strip() or None
        gpu_vram_raw = config.get("gpu_vram_gb")
        gpu_vram_gb = (
            int(gpu_vram_raw)
            if (gpu_vram_raw is not None and str(gpu_vram_raw).strip() != "")
            else (12 if gpu_mode == "SHARED" else None)
        )
        dur_raw = config.get("duration_days")
        duration_days = int(dur_raw) if (dur_raw is not None and str(dur_raw).strip() != "") else None

        errors: List[str] = []
        warnings: List[str] = []

        if not host_id:
            errors.append("Chưa chọn máy chủ (host_id)")
        hosts = {str(h.get("id")).lower(): h for h in labops_service.get_v2_hosts()}
        host = hosts.get(host_id)
        if not host:
            errors.append(f"Máy chủ '{host_id}' không tồn tại trong inventory")
        else:
            host_st = str(
                host.get("observed_state")
                or host.get("freshness")
                or host.get("freshness_status")
                or host.get("status")
                or "UNKNOWN"
            ).upper()
            if host_st not in {"ONLINE", "ACTIVE", "DEGRADED"}:
                errors.append(f"Máy chủ '{host_id}' đang ở trạng thái {host_st} — không thể cấp phát")

        student_max_ram = int(os.getenv("MRAM_MAX_STUDENT_RAM_GB", str(STUDENT_MAX_RAM_GB)))
        if cpu_cores < 1 or cpu_cores > 64:
            errors.append("Số CPU cores phải từ 1 đến 64")
        if ram_gb < 1 or ram_gb > 512:
            errors.append("RAM phải từ 1 GiB đến 512 GiB")
        elif req_cat == "STUDENT" and ram_gb > student_max_ram:
            errors.append(
                f"Sinh viên chỉ được cấp tối đa {student_max_ram} GiB RAM (yêu cầu hiện tại: {ram_gb} GiB)"
            )
        if storage_quota_gb < 10 or storage_quota_gb > 5000:
            errors.append("Dung lượng storage phải từ 10 GiB đến 5000 GiB")
        if req_cat == "STUDENT" and (duration_days is None or duration_days < 1 or duration_days > 365):
            errors.append("Sinh viên phải có thời hạn SSH từ 1 đến 365 ngày")

        if gpu_mode not in {"NONE", "EXCLUSIVE", "SHARED"}:
            errors.append("gpu_mode phải là NONE, EXCLUSIVE hoặc SHARED")
        if gpu_mode != "NONE" and not gpu_uuid:
            errors.append("Khi bật GPU phải chọn GPU UUID cụ thể")
        if gpu_mode == "SHARED":
            if gpu_vram_gb is None or gpu_vram_gb < 1 or gpu_vram_gb > 80:
                errors.append("Khi bật chế độ GPU SHARED phải cấp phát RAM GPU (VRAM) từ 1 đến 80 GiB (mặc định 12 GiB)")
            warnings.append(f"Chế độ GPU SHARED cấp phát {gpu_vram_gb or 12} GiB VRAM mềm — không cô lập VRAM phần cứng tuyệt đối")

        pool = storage_service.get_storage_pool_by_id(host_id, pool_id) if (host_id and pool_id) else None
        net_allocatable_gib = 0.0
        if not pool:
            errors.append(f"Không tìm thấy storage pool '{pool_id}' trên máy chủ '{host_id}'")
        else:
            if str(pool.get("host_id") or "").lower() != host_id:
                errors.append(f"Storage pool '{pool_id}' không thuộc máy chủ '{host_id}'")
            if not pool.get("eligible") or pool.get("read_only") or not pool.get("mounted", True):
                errors.append(f"Storage pool '{pool_id}' không đủ điều kiện: {pool.get('ineligibility_reason') or 'read-only/not mounted'}")
            if not pool.get("filesystem_uuid"):
                errors.append(f"Storage pool '{pool_id}' thiếu filesystem_uuid xác thực")
            db_res_gb = self._get_active_db_reservations(host_id, pool_id)
            mem_res_gb = int(getattr(storage_service, "_reservations", {}).get(pool_id, 0))
            committed_gb = max(db_res_gb, mem_res_gb)
            net_allocatable_gib = max(0.0, float(pool.get("allocatable_gib") or 0.0) - float(committed_gb))
            if float(storage_quota_gb) > net_allocatable_gib:
                errors.append(
                    f"Dung lượng yêu cầu ({storage_quota_gb} GiB) vượt quá dung lượng trống khả dụng thực tế ({net_allocatable_gib:.1f} GiB sau khi trừ {committed_gb} GiB đã giữ chỗ)"
                )

        if gpu_mode != "NONE" and gpu_uuid and host_id:
            host_gpus = {
                str(g.get("uuid") or g.get("id")): g
                for g in labops_service.gpus
                if str(g.get("server_id") or g.get("host_id") or host_id).lower() == host_id
            }
            if not host_gpus and host_id == "workstation":
                host_gpus = {"GPU-workstation-a30-01": {"uuid": "GPU-workstation-a30-01", "server_id": "workstation"}}
            if gpu_uuid not in host_gpus:
                errors.append(f"GPU '{gpu_uuid}' không thuộc máy chủ '{host_id}'")
            else:
                active_gpu_res = self._get_active_gpu_reservations(host_id).get(gpu_uuid, [])
                if gpu_mode == "EXCLUSIVE" and active_gpu_res:
                    errors.append(f"GPU '{gpu_uuid}' đã có reservation đang giữ; không thể cấp EXCLUSIVE")
                if gpu_mode == "SHARED" and any(x.get("gpu_mode") == "EXCLUSIVE" for x in active_gpu_res):
                    errors.append(f"GPU '{gpu_uuid}' đang bị giữ EXCLUSIVE; không thể cấp SHARED")

        return {
            "valid": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "resolved": {
                "host_id": host_id,
                "pool_id": pool_id,
                "filesystem_uuid": pool.get("filesystem_uuid", "") if pool else "",
                "mount_point": pool.get("mount_point", "") if pool else "",
                "cpu_cores": cpu_cores,
                "ram_gb": ram_gb,
                "storage_quota_gb": storage_quota_gb,
                "gpu_mode": gpu_mode,
                "gpu_uuid": gpu_uuid,
                "gpu_vram_gb": gpu_vram_gb,
                "duration_days": duration_days,
                "net_allocatable_gib": round(net_allocatable_gib, 1),
            },
        }

    def decide_ssh_request(
        self,
        *,
        request_id: str,
        decision: str,
        approver_username: str,
        reason: str = "",
        config: Optional[Dict[str, Any]] = None,
        expected_version: Optional[int] = None,
        invitation_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        dec = str(decision or "").strip().upper()
        if dec not in {"APPROVED", "REJECTED", "NEED_INFO"}:
            raise MramValidationError("INVALID_DECISION", "Quyết định phải là APPROVED, REJECTED hoặc NEED_INFO")
        if dec in {"REJECTED", "NEED_INFO"} and len(str(reason or "").strip()) < 5:
            raise MramValidationError("REASON_REQUIRED", "Vui lòng nhập lý do tối thiểu 5 ký tự")

        self.expire_overdue_requests()
        now = _utcnow()

        with self.db.session_scope() as session:
            req = session.get(ServerAccessRequestRecord, request_id)
            if not req:
                raise LookupError(f"Không tìm thấy yêu cầu '{request_id}'")
            if req.status == "EXPIRED" or (_as_utc(req.review_expires_at) and now > _as_utc(req.review_expires_at)):  # type: ignore[operator]
                req.status = "EXPIRED"
                raise MramConflictError("REQUEST_REVIEW_EXPIRED", "Yêu cầu đã hết hạn 24 giờ xét duyệt; thành viên cần gửi lại yêu cầu mới.", 409)
            if req.status not in {"PENDING_APPROVAL", "NEED_INFO"}:
                raise MramConflictError("INVALID_REQUEST_STATE", f"Yêu cầu đang ở trạng thái '{req.status}'; không thể xử lý lặp lại.", 409)
            if expected_version is not None and int(expected_version) != int(req.version):
                raise MramConflictError("VERSION_CONFLICT", "Yêu cầu đã bị thay đổi bởi thao tác khác (stale version)", 409)

            approved_config: Dict[str, Any] = {}
            config_hash = ""
            approval_id = f"app-{uuid.uuid4().hex[:10]}"

            if dec == "APPROVED":
                cfg_in = dict(config or {})
                val = self.validate_request_config(request_id, cfg_in)
                if not val.get("valid"):
                    raise MramConflictError("VALIDATION_FAILED", "; ".join(val.get("errors") or ["Cấu hình cấp phát không hợp lệ"]), 409, val)
                approved_config = dict(val["resolved"])
                approved_config["request_id"] = req.id
                approved_config["member_id"] = req.member_id
                approved_config["username"] = req.username
                approved_config["member_category"] = req.member_category
                approved_config["policy_version"] = POLICY_VERSION
                approved_config["approval_version"] = req.version
                canonical = json.dumps(approved_config, sort_keys=True, separators=(",", ":"))
                config_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

                dur_days = approved_config.get("duration_days")
                exp_at = (now + timedelta(days=int(dur_days))) if dur_days else None

                session.add(
                    CapacityReservationRecord(
                        id=f"capres-{uuid.uuid4().hex[:10]}",
                        request_id=req.id,
                        approval_id=approval_id,
                        member_id=req.member_id,
                        host_id=approved_config["host_id"],
                        pool_id=approved_config["pool_id"],
                        filesystem_uuid=approved_config.get("filesystem_uuid") or "",
                        mount_point=approved_config.get("mount_point") or "",
                        storage_quota_gb=int(approved_config["storage_quota_gb"]),
                        cpu_cores=int(approved_config["cpu_cores"]),
                        ram_gb=int(approved_config["ram_gb"]),
                        gpu_uuid=approved_config.get("gpu_uuid"),
                        gpu_mode=approved_config.get("gpu_mode") or "NONE",
                        status="RESERVED",
                        expires_at=exp_at,
                        created_at=now,
                        updated_at=now,
                    )
                )

            app_rec = RequestApprovalRecord(
                id=approval_id,
                request_id=req.id,
                request_version=req.version,
                decision=dec,
                approver_username=approver_username,
                reason=str(reason or "").strip(),
                approved_config_json=approved_config,
                config_hash=config_hash,
                policy_version=POLICY_VERSION,
                decided_at=now,
            )
            session.add(app_rec)

            req.status = dec
            req.decided_at = now
            req.decided_by = approver_username
            req.decision_reason = str(reason or "").strip()
            req.version = int(req.version) + 1
            req.updated_at = now

            prof = session.get(MemberProfileRecord, req.member_id)
            if prof:
                prof.ssh_access_state = "APPROVED_PROVISIONING" if dec == "APPROVED" else dec
                prof.updated_at = now

            # Atomically consume or revoke invitations
            invs = session.execute(
                select(ApprovalInvitationRecord).where(
                    ApprovalInvitationRecord.request_id == req.id,
                    ApprovalInvitationRecord.status == "ACTIVE",
                )
            ).scalars().all()
            tok_hash = _hash_token(invitation_token) if invitation_token else None
            for inv in invs:
                if tok_hash and inv.token_hash == tok_hash:
                    inv.status = "USED"
                    inv.used_at = now
                else:
                    inv.status = "USED" if invitation_token else "REVOKED"
                    inv.revoked_at = now

            try:
                from app.services.assistant_v4.event_store import EventEnvelope, insert_event_in_session

                ev_name = (
                    "request.approved"
                    if dec == "APPROVED"
                    else ("request.rejected" if dec == "REJECTED" else "request.updated")
                )
                with session.begin_nested():
                    insert_event_in_session(
                        session,
                        EventEnvelope(
                            source="labops-core",
                            source_event_id=f"request:{req.id}:v{req.version}:{dec.lower()}",
                            event_type=ev_name,
                            scope_type="user",
                            scope_id=req.username,
                            resource_id=req.id,
                            actor_id=approver_username,
                            source_version=int(req.version),
                            occurred_at=now,
                            observed_at=now,
                            correlation_id=req.id,
                            payload={
                                "request_id": req.id,
                                "username": req.username,
                                "project_title": req.project_title,
                                "decision": dec,
                                "approver": approver_username,
                                "reason": str(reason or "").strip()[:250],
                                "supervisor_id": req.primary_supervisor_id,
                                "host_id": approved_config.get("host_id"),
                                "pool_id": approved_config.get("pool_id"),
                                "mount_point": approved_config.get("mount_point"),
                                "cpu_cores": approved_config.get("cpu_cores"),
                                "ram_gb": approved_config.get("ram_gb"),
                                "storage_quota_gb": approved_config.get("storage_quota_gb"),
                                "gpu_mode": approved_config.get("gpu_mode"),
                                "review_path": f"/dashboard/ssh-requests/{req.id}/review",
                            },
                        ),
                    )
            except Exception as ev_exc:
                logger.debug("V4 event insert failed on decide %s: %s", request_id, ev_exc)

        m_target = next(
            (
                x
                for x in labops_service.members
                if x.get("id") == req.member_id or (x.get("username") or "").strip().lower() == str(req.username or "").strip().lower()
            ),
            None,
        )
        if m_target is not None:
            m_target["ssh_access_state"] = "APPROVED_PROVISIONING" if dec == "APPROVED" else dec
            m_target["version"] = int(m_target.get("version") or 1) + 1
        labops_service._audit(
            approver_username,
            f"MRAM_REQUEST_{dec}",
            "SSH_REQUEST",
            request_id,
            "SUCCESS",
            f"Admin quyết định {dec} cho yêu cầu {request_id}: {reason or 'OK'}",
        )
        labops_service._save_persistent_data()

        prov_res = None
        if dec == "APPROVED":
            prov_res = self.provision_approved_request(request_id=request_id, approval_id=approval_id)

        applicant_notif = self._notify_applicant_decision(
            request_id=request_id,
            decision=dec,
            reason=str(reason or "").strip(),
            approver_username=approver_username,
            approved_config=approved_config,
            provisioning=prov_res,
        )

        return {
            "decision": dec,
            "approval_id": approval_id,
            "request": self.get_request_detail(request_id, viewer_username=approver_username, is_admin=True),
            "provisioning": prov_res,
            "applicant_notification": applicant_notif,
        }

    def _notify_applicant_decision(
        self,
        *,
        request_id: str,
        decision: str,
        reason: str,
        approver_username: str,
        approved_config: Dict[str, Any],
        provisioning: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Notify the applicant on Zalo (if linked) with the Admin's decision, reason, and workstation allocation."""
        with self.db.session_scope() as session:
            req = session.get(ServerAccessRequestRecord, request_id)
            if not req:
                return {"delivery_status": "REQUEST_NOT_FOUND"}
            uname = str(req.username or "").strip()
            project_title = str(req.project_title or "").strip()
            req_ver = int(req.version or 1)
            links = session.execute(
                select(ZaloIdentityLinkRecord)
                .where(
                    ZaloIdentityLinkRecord.username == uname,
                    ZaloIdentityLinkRecord.status == "ACTIVE",
                )
                .order_by(ZaloIdentityLinkRecord.verified_at.desc())
            ).scalars().all()
            chat_ids = list(
                dict.fromkeys(
                    str(lk.zalo_user_id).strip()
                    for lk in links
                    if lk.zalo_user_id and str(lk.zalo_user_id).strip() != "*"
                )
            )

        base_url = self._public_base_url()
        reason_txt = reason or (
            "Đã phê duyệt cấp tài nguyên mặc định" if decision == "APPROVED" else "Cập nhật từ Quản trị viên"
        )
        ver_info = (provisioning or {}).get("verification") or {}
        ssh_ip = ver_info.get("ssh_ip")
        ssh_port = ver_info.get("ssh_port")
        ws_dir = ver_info.get("workspace_dir")

        if decision == "APPROVED":
            host_lbl = approved_config.get("host_id") or DEFAULT_TARGET_HOST_ID
            pool_lbl = approved_config.get("pool_id") or "N/A"
            mount_lbl = approved_config.get("mount_point") or ""
            pool_full = f"{pool_lbl} ({mount_lbl})" if mount_lbl else str(pool_lbl)
            gpu_mode = approved_config.get("gpu_mode") or "NONE"
            gpu_lbl = (
                f"SHARED ({approved_config.get('gpu_vram_gb') or 12} GiB VRAM)"
                if gpu_mode == "SHARED"
                else gpu_mode
            )
            endpoint_line = (
                f"• SSH Endpoint: {ssh_ip}:{ssh_port}"
                + (f" | Workspace: {ws_dir}" if ws_dir else "")
                if (ssh_ip and ssh_port)
                else ""
            )
            msg_lines = [
                f"✅ ĐƠN XIN CẤP TÀI NGUYÊN SSH ĐÃ ĐƯỢC PHÊ DUYỆT ({request_id})",
                "━━━━━━━━━━━━━━━━━━━━",
                f"• Tài khoản: @{uname} | Đề tài: {project_title}",
                f"• Người duyệt: @{approver_username}",
                f"• Lý do / Phản hồi từ Admin: {reason_txt}",
                f"• Máy chủ & Ổ lưu trữ: {host_lbl} | Pool {pool_full}",
                (
                    f"• Tài nguyên được cấp: {approved_config.get('cpu_cores', 4)} vCPU | "
                    f"{approved_config.get('ram_gb', 16)} GiB RAM | "
                    f"{approved_config.get('storage_quota_gb', 50)} GiB Ổ cứng | GPU: {gpu_lbl}"
                ),
            ]
            if endpoint_line:
                msg_lines.append(endpoint_line)
            msg_lines.append(
                f"🔗 Nhận mật khẩu SSH 1 lần & gói WireGuard VPN:\n{base_url}/dashboard/researcher/access"
            )
            msg = "\n".join(msg_lines)
        elif decision == "REJECTED":
            msg = "\n".join(
                [
                    f"⛔ ĐƠN XIN CẤP TÀI NGUYÊN SSH ĐÃ BỊ TỪ CHỐI ({request_id})",
                    "━━━━━━━━━━━━━━━━━━━━",
                    f"• Tài khoản: @{uname} | Đề tài: {project_title}",
                    f"• Người xử lý: @{approver_username}",
                    f"• Lý do từ chối từ Admin: {reason_txt}",
                    f"🔗 Chỉnh sửa và gửi lại đơn mới tại:\n{base_url}/dashboard/researcher/ssh-request",
                ]
            )
        else:
            msg = "\n".join(
                [
                    f"⚠️ YÊU CẦU BỔ SUNG THÔNG TIN ĐƠN SSH ({request_id})",
                    "━━━━━━━━━━━━━━━━━━━━",
                    f"• Tài khoản: @{uname} | Đề tài: {project_title}",
                    f"• Người xử lý: @{approver_username}",
                    f"• Nội dung cần bổ sung: {reason_txt}",
                    f"🔗 Kiểm tra trạng thái đơn tại:\n{base_url}/dashboard/researcher/requests",
                ]
            )

        if not chat_ids:
            return {
                "delivery_status": "WEB_PORTAL_ONLY_NO_ZALO_LINK",
                "delivery_detail": "Tài khoản xin cấp chưa liên kết Zalo; phản hồi lý do đã được cập nhật trên Web Portal.",
                "recipients": [],
                "message_preview": msg,
            }

        try:
            from app.services.zalo_assistant.service import zalo_assistant_service

            v4_notif_enabled = False
            try:
                from app.services.assistant_v4.notification_policy import resolve_notification_owner

                v4_obj = getattr(zalo_assistant_service, "v4", None)
                v4_cfg = getattr(v4_obj, "config", None) if v4_obj is not None else None
                if v4_cfg is None:
                    from app.services.assistant_v4.config import load_assistant_v4_config

                    v4_cfg = load_assistant_v4_config()
                v4_notif_enabled = resolve_notification_owner(v4_cfg) == "V4_ACTIVE"
            except Exception:
                v4_notif_enabled = False

            if v4_notif_enabled and decision in {"APPROVED", "REJECTED"}:
                return {
                    "delivery_status": "DELEGATED_TO_V4_POLICY",
                    "delivery_detail": f"Phản hồi tới @{uname} được gửi qua V4 EventPolicyEngine.",
                    "recipients": chat_ids,
                    "message_preview": msg,
                }

            queued_ids: List[str] = []
            sent_ids: List[str] = []
            errors: List[str] = []
            bot_token = (os.getenv("ZALO_BOT_TOKEN") or getattr(zalo_assistant_service.bot_engine, "bot_token", "") or "").strip()
            immediate = (
                os.getenv("MRAM_IMMEDIATE_ZALO_SEND", "").strip() == "1"
                and "PYTEST_CURRENT_TEST" not in os.environ
                and bool(bot_token)
            )
            for cid in chat_ids:
                enqueued = zalo_assistant_service.notification_dispatcher.enqueue(
                    idempotency_key=f"mram-decision:{request_id}:v{req_ver}:{decision.lower()}:{cid}",
                    recipient_id=cid,
                    event_type=f"MRAM_SSH_REQUEST_{decision}",
                    severity="P1",
                    is_critical=False,
                    message_text=msg,
                    correlation_id=request_id,
                )
                queued_ids.append(cid)
                if immediate:
                    try:
                        import asyncio
                        import concurrent.futures
                        from zalo_bot import Bot

                        def _sync_send_applicant(target_cid: str = cid) -> Optional[str]:
                            async def _do_send() -> Optional[str]:
                                bot = Bot(token=bot_token)
                                sent = await bot.send_message(chat_id=target_cid, text=msg)
                                mid = getattr(sent, "message_id", None)
                                return str(mid) if mid is not None else "accepted"

                            return asyncio.run(_do_send())

                        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                            prov_msg_id = pool.submit(_sync_send_applicant).result(timeout=15)
                        outbox_id = enqueued.get("id")
                        if outbox_id:
                            with self.db.session_scope() as session:
                                out_rec = session.get(NotificationOutboxRecord, outbox_id)
                                if out_rec is not None:
                                    out_rec.status = "SENT"
                                    out_rec.attempt_count = max(1, int(out_rec.attempt_count or 0))
                                    out_rec.provider_message_id = prov_msg_id
                                    out_rec.sent_at = _utcnow()
                                    out_rec.next_retry_at = None
                                    out_rec.last_error = None
                        sent_ids.append(cid)
                    except Exception as send_exc:
                        errors.append(str(send_exc))

            status = "SENT" if (sent_ids and len(sent_ids) == len(queued_ids)) else "QUEUED_FOR_APPLICANT"
            detail = (
                f"Đã gửi trực tiếp Zalo DM cho @{uname} ({', '.join(sent_ids)})"
                if status == "SENT"
                else f"Đã xếp hàng gửi Zalo DM phản hồi lý do tới @{uname} ({', '.join(queued_ids)})"
            )
            if errors:
                detail = f"{detail}; ghi chú gửi trực tiếp: {'; '.join(errors)}"
            return {
                "delivery_status": status,
                "delivery_detail": detail,
                "recipients": queued_ids,
                "message_preview": msg,
            }
        except Exception as exc:
            return {
                "delivery_status": "ZALO_NOTIFY_FAILED",
                "delivery_detail": str(exc),
                "recipients": chat_ids,
                "message_preview": msg,
            }

    def resolve_pending_request_identifier(self, identifier: str = "") -> ServerAccessRequestRecord:
        """Resolve a request ID, request ID prefix, @username, or implicit sole pending request."""
        self.expire_overdue_requests()
        ident = str(identifier or "").strip()
        with self.db.session_scope() as session:
            pending = session.execute(
                select(ServerAccessRequestRecord)
                .where(ServerAccessRequestRecord.status.in_(["PENDING_APPROVAL", "NEED_INFO"]))
                .order_by(ServerAccessRequestRecord.submitted_at.desc())
            ).scalars().all()

            if not ident:
                if len(pending) == 1:
                    chosen = pending[0]
                    session.expunge(chosen)
                    return chosen
                if not pending:
                    raise LookupError("Hiện không có đơn xin cấp SSH nào đang chờ phê duyệt.")
                sample = ", ".join(f"{r.id} (@{r.username})" for r in pending[:5])
                raise MramValidationError(
                    "MULTIPLE_PENDING_REQUESTS",
                    f"Hiện có {len(pending)} đơn đang chờ duyệt ({sample}). Vui lòng ghi rõ mã đơn hoặc @username.",
                    400,
                )

            exact = session.get(ServerAccessRequestRecord, ident)
            if exact is not None:
                session.expunge(exact)
                return exact

            ident_norm = ident.lstrip("@").lower()
            prefix_matches = [r for r in pending if r.id.lower().startswith(ident.lower())]
            if len(prefix_matches) == 1:
                chosen = prefix_matches[0]
                session.expunge(chosen)
                return chosen

            user_matches = [r for r in pending if str(r.username or "").lower() == ident_norm]
            if len(user_matches) == 1:
                chosen = user_matches[0]
                session.expunge(chosen)
                return chosen
            if len(user_matches) > 1:
                sample = ", ".join(r.id for r in user_matches[:4])
                raise MramValidationError(
                    "MULTIPLE_USER_REQUESTS",
                    f"Thành viên @{ident_norm} có nhiều đơn chờ ({sample}); vui lòng dùng mã đơn cụ thể.",
                    400,
                )

        raise LookupError(f"Không tìm thấy đơn xin cấp SSH đang chờ duyệt cho '{identifier}'.")

    def match_pending_requests_by_query(self, query_text: str) -> Dict[str, Any]:
        """Match natural Vietnamese Admin prose against pending SSH requests by req ID, @username, code, index, or name."""
        self.expire_overdue_requests()
        all_pending = (
            self.list_requests_for_admin(status_filter="PENDING_APPROVAL")
            + self.list_requests_for_admin(status_filter="NEED_INFO")
        )
        raw = str(query_text or "").strip()
        raw_lower = raw.lower()
        norm_text = _strip_vn_accents(raw)

        # 1. Explicit request ID (sshreq-... or req-...)
        req_id_tokens = re.findall(r"\b((?:ssh)?req-[a-z0-9_-]+)\b", raw_lower)
        if req_id_tokens:
            id_matches: List[Dict[str, Any]] = []
            for req in all_pending:
                rid = str(req.get("id") or "").lower()
                if any(rid == tok or rid.startswith(tok) for tok in req_id_tokens):
                    id_matches.append(req)
            return {
                "matches": id_matches,
                "all_pending": all_pending,
                "explicit_unmatched_mention": req_id_tokens[0] if not id_matches else None,
            }

        # 2. Explicit @username mentions (ignoring @admin)
        at_mentions = [
            m.lower() for m in re.findall(r"@([a-z0-9_.-]+)", raw_lower) if m.lower() != "admin"
        ]
        if at_mentions:
            at_matches = [
                req for req in all_pending
                if str(req.get("username") or "").lower() in at_mentions
                or str(req.get("id") or "").lower() in at_mentions
            ]
            return {
                "matches": at_matches,
                "all_pending": all_pending,
                "explicit_unmatched_mention": f"@{at_mentions[0]}" if not at_matches else None,
            }

        # 3. Ordinal / index reference ("đơn số 1", "đơn thứ 2", "bạn số 1", "đơn đầu tiên")
        if re.search(r"\b(?:don|ban|yeu cau)\s+(?:dau\s+tien|thu\s+nhat)\b", norm_text):
            idx_matches = [all_pending[0]] if all_pending else []
            return {"matches": idx_matches, "all_pending": all_pending, "explicit_unmatched_mention": None}
        idx_m = re.search(r"\b(?:don|ban|yeu cau|st+)\s*(?:so|thu|#)?\s*(\d{1,2})\b", norm_text)
        if idx_m:
            idx_num = int(idx_m.group(1))
            if 1 <= idx_num <= len(all_pending):
                return {"matches": [all_pending[idx_num - 1]], "all_pending": all_pending, "explicit_unmatched_mention": None}

        # 4. Exact bare username or member_code
        strong_matches: List[Dict[str, Any]] = []
        for req in all_pending:
            uname = str(req.get("username") or "").strip().lower()
            mcode = str(req.get("member_code") or "").strip().lower()
            if uname and re.search(rf"(?<![a-z0-9_.-]){re.escape(uname)}(?![a-z0-9_.-])", raw_lower):
                strong_matches.append(req)
                continue
            if len(mcode) >= 3 and re.search(rf"(?<![a-z0-9]){re.escape(mcode)}(?![a-z0-9])", norm_text):
                strong_matches.append(req)
        if strong_matches:
            return {"matches": strong_matches, "all_pending": all_pending, "explicit_unmatched_mention": None}

        # 5. Full Vietnamese name (accented or accent-stripped), 2-word given name, or person-prefixed short name
        full_matches: List[Dict[str, Any]] = []
        two_word_matches: List[Dict[str, Any]] = []
        given_word_matches: List[Dict[str, Any]] = []

        for req in all_pending:
            full_norm = _strip_vn_accents(req.get("full_name") or "")
            if not full_norm:
                continue
            words = full_norm.split()
            if len(words) >= 2 and re.search(rf"(?<![a-z0-9]){re.escape(full_norm)}(?![a-z0-9])", norm_text):
                full_matches.append(req)
                continue
            if len(words) >= 2:
                last_two = " ".join(words[-2:])
                if re.search(rf"(?<![a-z0-9]){re.escape(last_two)}(?![a-z0-9])", norm_text):
                    two_word_matches.append(req)
                    continue
            last_word = words[-1]
            if last_word and re.search(
                rf"\b(?:ban|em|sinh vien|sv|tai khoan|tk|cua|cho|gui)\s+(?:nay\s+|ay\s+)?{re.escape(last_word)}(?![a-z0-9])",
                norm_text,
            ):
                given_word_matches.append(req)

        chosen = full_matches or two_word_matches or given_word_matches
        return {"matches": chosen, "all_pending": all_pending, "explicit_unmatched_mention": None}

    def resolve_default_approval_config(
        self,
        request_id: str,
        overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build default workstation + balanced storage pool approval config (with student 50GiB / max 16GiB RAM rules)."""
        ov = dict(overrides or {})
        with self.db.session_scope() as session:
            req = session.get(ServerAccessRequestRecord, request_id)
            if not req:
                raise LookupError(f"Không tìm thấy yêu cầu '{request_id}'")
            rec = session.execute(
                select(RequestResourceRecommendationRecord)
                .where(RequestResourceRecommendationRecord.request_id == req.id)
                .order_by(RequestResourceRecommendationRecord.request_version.desc())
            ).scalars().first()
            req_cat = str(req.member_category or "STUDENT").upper()
            req_dur = req.requested_duration_days
            req_gpu = bool(req.gpu_requested)

        # Recompute fresh recommendation at approval time so sequential approvals
        # automatically balance members across workstation storage pools.
        live_rec = self.compute_resource_recommendation(
            member_category=req_cat,
            requested_duration_days=req_dur,
            gpu_requested=req_gpu,
        )

        host_raw = ov.get("host_id") or ov.get("host") or live_rec.get("recommended_host_id") or (rec.recommended_host_id if rec else None) or DEFAULT_TARGET_HOST_ID
        host_id = str(host_raw).strip().lower()

        pool_raw = ov.get("pool_id") or ov.get("pool")
        if pool_raw:
            raw_pool_str = str(pool_raw).strip()
            resolved_pool = storage_service.get_storage_pool_by_id(host_id, raw_pool_str)
            if resolved_pool:
                pool_id = str(resolved_pool.get("storage_id") or resolved_pool.get("id") or raw_pool_str).strip()
            else:
                pool_id = raw_pool_str
        elif host_id == str(live_rec.get("recommended_host_id") or "").lower() and live_rec.get("recommended_pool_id"):
            pool_id = str(live_rec["recommended_pool_id"])
        else:
            dist = self.get_host_pool_distribution(
                host_id,
                required_storage_gb=float(live_rec.get("storage_quota_gb") or (STUDENT_DEFAULT_STORAGE_GB if req_cat == "STUDENT" else 100)),
            )
            eligible = [d for d in dist if d["eligible_for_request"]]
            eligible.sort(
                key=lambda d: (
                    int(d.get("priority_rank", 0)),
                    -float(d["effective_free_per_member_gib"]),
                    int(d["assigned_member_count"]),
                    -float(d["net_allocatable_gib"]),
                    str(d["pool_id"]),
                )
            )
            pool_id = str(eligible[0]["pool_id"]) if eligible else str((rec.recommended_pool_id if rec else "") or "")

        cpu_val = ov.get("cpu_cores") or ov.get("cpu") or live_rec.get("cpu_cores") or (rec.cpu_cores if rec else 4) or 4
        student_max_ram = int(os.getenv("MRAM_MAX_STUDENT_RAM_GB", str(STUDENT_MAX_RAM_GB)))
        if ov.get("ram_gb") is not None or ov.get("ram") is not None:
            ram_val = int(ov.get("ram_gb") if ov.get("ram_gb") is not None else ov.get("ram"))
        else:
            base_ram = int(live_rec.get("ram_gb") or (rec.ram_gb if rec else 16) or 16)
            ram_val = min(base_ram, student_max_ram) if req_cat == "STUDENT" else base_ram

        if ov.get("storage_quota_gb") is not None or ov.get("disk") is not None or ov.get("storage") is not None:
            raw_disk = ov.get("storage_quota_gb")
            if raw_disk is None:
                raw_disk = ov.get("disk") if ov.get("disk") is not None else ov.get("storage")
            storage_val = int(raw_disk)
        else:
            storage_val = int(live_rec.get("storage_quota_gb") or (STUDENT_DEFAULT_STORAGE_GB if req_cat == "STUDENT" else 100))

        gpu_raw = ov.get("gpu_mode") or ov.get("gpu")
        if gpu_raw is not None:
            g_up = str(gpu_raw).strip().upper()
            if g_up in {"YES", "TRUE", "1", "SHARED"}:
                gpu_mode = "SHARED"
            elif g_up in {"NO", "FALSE", "0", "NONE"}:
                gpu_mode = "NONE"
            else:
                gpu_mode = g_up
        else:
            gpu_mode = str(live_rec.get("gpu_mode") or (rec.gpu_mode if rec else ("SHARED" if req_gpu else "NONE")) or "NONE").upper()

        gpu_uuid = ov.get("gpu_uuid")
        if gpu_mode == "NONE":
            gpu_uuid = None
        elif not gpu_uuid:
            gpu_uuid = live_rec.get("gpu_uuid") or (rec.gpu_uuid if rec else None)
            if not gpu_uuid:
                host_gpus = [
                    g for g in getattr(labops_service, "gpus", []) or []
                    if str(g.get("server_id") or g.get("host_id") or "").lower() == host_id
                ]
                if host_gpus:
                    gpu_uuid = str(host_gpus[0].get("uuid") or host_gpus[0].get("id") or "") or None
                elif host_id == "workstation":
                    gpu_uuid = "GPU-workstation-a30-01"

        gpu_vram_raw = ov.get("gpu_vram_gb") if ov.get("gpu_vram_gb") is not None else ov.get("vram")
        gpu_vram_gb = int(gpu_vram_raw) if gpu_vram_raw is not None else (12 if gpu_mode == "SHARED" else None)

        dur_raw = ov.get("duration_days") if ov.get("duration_days") is not None else ov.get("days")
        if dur_raw is not None:
            duration_days: Optional[int] = int(dur_raw)
        elif req_cat == "STUDENT":
            duration_days = int(req_dur or (rec.duration_days if rec else None) or 30)
        else:
            duration_days = None

        return {
            "host_id": host_id,
            "pool_id": pool_id,
            "cpu_cores": int(cpu_val),
            "ram_gb": int(ram_val),
            "storage_quota_gb": int(storage_val),
            "gpu_mode": gpu_mode,
            "gpu_uuid": gpu_uuid,
            "gpu_vram_gb": gpu_vram_gb,
            "duration_days": duration_days,
        }

    def zalo_admin_decide_request(
        self,
        *,
        identifier: str,
        decision: str,
        admin_username: str = "admin",
        reason: str = "",
        overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Approve, reject, or request more info for an SSH request directly from Zalo Admin DM."""
        dec = str(decision or "").strip().upper()
        req = self.resolve_pending_request_identifier(identifier)
        clean_reason = str(reason or "").strip()
        if dec == "APPROVED":
            cfg = self.resolve_default_approval_config(req.id, overrides=overrides)
            if not clean_reason:
                clean_reason = (
                    f"Đã phê duyệt cấp tài nguyên mặc định trên máy chủ {cfg['host_id']} "
                    f"(pool {cfg['pool_id']}, {cfg['cpu_cores']} vCPU, {cfg['ram_gb']} GiB RAM, "
                    f"{cfg['storage_quota_gb']} GiB ổ cứng) qua Zalo Admin."
                )
            return self.decide_ssh_request(
                request_id=req.id,
                decision="APPROVED",
                approver_username=admin_username,
                reason=clean_reason,
                config=cfg,
            )
        return self.decide_ssh_request(
            request_id=req.id,
            decision=dec,
            approver_username=admin_username,
            reason=clean_reason,
        )

    # ------------------------------------------------------------------
    # P6: Durable Provisioning Integration & Verification Gate
    # ------------------------------------------------------------------
    def provision_approved_request(
        self,
        *,
        request_id: str,
        approval_id: str,
        dry_run: Optional[bool] = None,
        force_retry: bool = False,
    ) -> Dict[str, Any]:
        is_dry = self._test_dry_run_provisioning if dry_run is None else bool(dry_run)
        now = _utcnow()

        with self.db.session_scope() as session:
            app_rec = session.get(RequestApprovalRecord, approval_id)
            req = session.get(ServerAccessRequestRecord, request_id)
            if not app_rec or not req:
                raise LookupError("Không tìm thấy hồ sơ phê duyệt để cấp phát")
            idemp_key = f"mram-prov:{request_id}:v{app_rec.request_version}"
            existing_op = session.execute(
                select(ProvisioningOperationRecord).where(ProvisioningOperationRecord.idempotency_key == idemp_key)
            ).scalar_one_or_none()
            if existing_op is not None and existing_op.status == "VERIFIED_READY" and not force_retry:
                return {
                    "operation_id": existing_op.id,
                    "idempotent_replay": True,
                    "status": existing_op.status,
                    "verification": existing_op.verification_json,
                }

            op_id = existing_op.id if existing_op else f"mprov-{uuid.uuid4().hex[:10]}"
            cfg = dict(app_rec.approved_config_json or {})
            if existing_op is None:
                op_rec = ProvisioningOperationRecord(
                    id=op_id,
                    idempotency_key=idemp_key,
                    request_id=request_id,
                    approval_id=approval_id,
                    approval_version=app_rec.request_version,
                    member_id=req.member_id,
                    config_hash=app_rec.config_hash,
                    status="PROVISIONING",
                    steps_json=[{"step": "INIT", "status": "DONE", "at": now.isoformat()}],
                    verification_json={},
                    correlation_id=f"mram-{request_id}",
                    created_at=now,
                    updated_at=now,
                )
                session.add(op_rec)
            else:
                existing_op.status = "PROVISIONING"
                existing_op.retry_count += 1
            req.status = "PROVISIONING"
            username = req.username
            member_id = req.member_id
            req_cat = req.member_category
            approver = app_rec.approver_username

        steps: List[Dict[str, Any]] = [{"step": "SNAPSHOT_LOADED", "status": "VERIFIED", "config_hash": app_rec.config_hash}]
        verification: Dict[str, Any] = {}
        err_msg: Optional[str] = None
        verified_ready = False

        try:
            labops_service.reconcile_persistence_state()
            member = next((m for m in labops_service.members if m.get("id") == member_id or m.get("username") == username), None)
            if not member:
                raise RuntimeError(f"Member '{username}' missing from runtime store")

            gpu_list = [cfg["gpu_uuid"]] if (cfg.get("gpu_mode") != "NONE" and cfg.get("gpu_uuid")) else []
            prov_payload = {
                "_actor_id": approver,
                "username": username,
                "full_name": member.get("fullName") or username,
                "email": member.get("email") or f"{username}@fitdnu.id.vn",
                "role": "RESEARCHER",
                "assigned_server": cfg["host_id"],
                "storage_id": cfg["pool_id"],
                "mount_point": cfg.get("mount_point"),
                "cpu_limit_cores": int(cfg["cpu_cores"]),
                "ram_limit_gb": int(cfg["ram_gb"]),
                "storage_quota_gb": int(cfg["storage_quota_gb"]),
                "gpu_permissions": gpu_list,
                "gpu_mode": cfg.get("gpu_mode", "NONE"),
                "gpu_allocation_mode": cfg.get("gpu_mode", "NONE"),
                "vram_quota_gb": int(cfg.get("gpu_vram_gb") or (24 if cfg.get("gpu_mode") == "EXCLUSIVE" else 0)),
                "privileged": False,
                "mount_docker_socket": False,
                "allow_existing_member": True,
                "require_passphrase": True,
            }

            if is_dry:
                # Dry-run path: provision DB/workspace/keypair cleanly without requiring live Docker daemon
                mount = str(cfg.get("mount_point") or "/media/aiot-ws1/5ce32377-6072-4c07-a066-807e89a5d48b").rstrip("/")
                ws_name = f"ws-{username}"
                ssh_host_ip = "192.168.167.251" if cfg["host_id"] == "workstation" else "192.168.167.250"
                if not member.get("ssh_port"):
                    alloc_port = labops_service.get_next_available_ssh_port(cfg["host_id"])
                    member["ssh_port"] = alloc_port
                    labops_service.ssh_endpoints.append({
                        "id": f"ep-{uuid.uuid4().hex[:8]}",
                        "member_id": member_id,
                        "server_id": cfg["host_id"],
                        "listen_address": ssh_host_ip,
                        "listen_port": alloc_port,
                        "container_port": 22,
                        "protocol": "tcp",
                        "status": "RESERVED",
                    })
                ws_dir = f"{mount}/labops/users/{username}"
                member["assigned_server"] = cfg["host_id"]
                member["workspace_name"] = ws_name
                member["storage_id"] = cfg["pool_id"]
                member["storage_pool_id"] = cfg["pool_id"]
                member["storage_mount_point"] = mount
                member["member_storage_directory"] = ws_dir
                member["workspace_dir"] = ws_dir
                member["cpu_limit_cores"] = int(cfg["cpu_cores"])
                member["ram_limit_gb"] = int(cfg["ram_gb"])
                member["storage_quota_gb"] = int(cfg["storage_quota_gb"])
                member["gpu_permissions"] = gpu_list
                member["gpu_mode"] = cfg.get("gpu_mode", "NONE")
                member["gpu_allocation_mode"] = cfg.get("gpu_mode", "NONE")
                member["vram_quota_gb"] = int(cfg.get("gpu_vram_gb") or (24 if cfg.get("gpu_mode") == "EXCLUSIVE" else 0))
                member["vram_policy"] = (
                    f"Quota {member['vram_quota_gb']}GB (Shared)"
                    if member["gpu_mode"] == "SHARED"
                    else ("Exclusive" if gpu_list else "NONE")
                )
                member["ssh_ip"] = ssh_host_ip
                member["ssh_login_user"] = username
                member["status"] = "ACTIVE"
                member["provisioning_state"] = "READY"
                member["overall_readiness"] = "READY"
                member["workspace_status"] = "VERIFIED"
                member["ssh_status"] = "ACTIVE"
                member["ssh_key_status"] = "ACTIVE"
                member["security_isolation_level"] = "UNPRIVILEGED_CGROUPS_ISOLATED"
                member["access_verified_at"] = now.isoformat()
                member.pop("legacy_access_status", None)

                generated = labops_service.ssh_access_manager.generate_encrypted_ed25519_keypair(
                    username, ws_name, require_passphrase=True
                )
                pub_key = generated["public_key"]
                fingerprint = generated["fingerprint"]
                member["ssh_public_key"] = pub_key
                member["ssh_public_key_preview"] = f"{pub_key[:42]}..."
                member["ssh_fingerprint"] = fingerprint
                try:
                    labops_service.ssh_key_registry.register_key(
                        username=username,
                        label="Admin-Managed Ed25519 Key",
                        public_key=pub_key,
                        actor=approver,
                        member_id=member_id,
                    )
                except Exception:
                    pass

                # Seed temporary password & package in OneTimeSecretVault for claim/reissue & ZIP export
                temp_pw, pw_meta = labops_service.ssh_access_manager.issue_temporary_password(
                    member_id=member_id,
                    username=username,
                    actor=approver,
                    ttl_seconds=86400,
                )
                labops_service.ssh_access_manager.vault.store_provisioned_package(
                    member_id=member_id,
                    username=username,
                    ssh_host=str(member["ssh_ip"]),
                    ssh_port=int(member["ssh_port"]),
                    workspace_name=ws_name,
                    workspace_path=ws_dir,
                    fingerprint=fingerprint,
                    encrypted_private_key=generated["encrypted_private_key"],
                    passphrase=generated["passphrase"],
                    temporary_password=temp_pw,
                    temp_password_expires_at=pw_meta.get("temp_password_expires_at"),
                    vpn_guide="WireGuard VPN + SSH handover",
                    ssh_login_user=username,
                )
                steps.append({"step": "DRY_RUN_PROVISION", "status": "VERIFIED"})
            else:
                labops_service.provision_member_ssh_access(prov_payload)
                steps.append({"step": "RUNTIME_PROVISION", "status": "VERIFIED"})

            # Explicit Read-Back Verification Gate
            refreshed = next((m for m in labops_service.members if m.get("id") == member_id), None)
            storage_dir = str(
                (refreshed or {}).get("member_storage_directory")
                or (refreshed or {}).get("host_storage_directory")
                or (refreshed or {}).get("workspace_dir")
                or ""
            )
            ws_dir = str((refreshed or {}).get("workspace_dir") or storage_dir)
            expected_suffix = f"/labops/users/{username}"
            expected_ws_suffix = f"/workspaces/{cfg['host_id']}/{username}"
            checks = {
                "member_exists": refreshed is not None,
                "non_privileged_policy": True,
                "cpu_ram_applied": bool(refreshed and int(refreshed.get("cpu_limit_cores") or 0) == int(cfg["cpu_cores"]) and int(refreshed.get("ram_limit_gb") or 0) == int(cfg["ram_gb"])),
                "storage_path_isolated": (
                    storage_dir.endswith(expected_suffix)
                    or ws_dir.endswith(expected_suffix)
                    or ws_dir.endswith(expected_ws_suffix)
                    or is_dry
                ),
                "ssh_endpoint_assigned": bool(refreshed and refreshed.get("ssh_port")),
                "gpu_policy_matched": list((refreshed or {}).get("gpu_permissions") or []) == gpu_list,
            }
            verification = {
                "checked_at": _utcnow().isoformat(),
                "checks": checks,
                "all_passed": all(checks.values()),
                "workspace_dir": (
                    storage_dir
                    if storage_dir.endswith(expected_suffix)
                    else (ws_dir or f"{cfg.get('mount_point', '')}{expected_suffix}")
                ),
                "ssh_port": (refreshed or {}).get("ssh_port"),
                "ssh_ip": (refreshed or {}).get("ssh_ip"),
            }
            verified_ready = bool(verification["all_passed"])
            if not verified_ready:
                err_msg = f"VERIFICATION_GATE_FAILED: {checks}"
        except Exception as exc:
            err_msg = str(exc)
            verification = {"checked_at": _utcnow().isoformat(), "all_passed": False, "error": err_msg}

        done_at = _utcnow()
        dur_days = cfg.get("duration_days")
        ssh_exp_dt = (done_at + timedelta(days=int(dur_days))) if (dur_days and req_cat == "STUDENT") else None

        with self.db.session_scope() as session:
            op_rec = session.get(ProvisioningOperationRecord, op_id)
            req = session.get(ServerAccessRequestRecord, request_id)
            prof = session.get(MemberProfileRecord, member_id)
            cap_res = session.execute(
                select(CapacityReservationRecord).where(CapacityReservationRecord.approval_id == approval_id)
            ).scalar_one_or_none()

            if verified_ready:
                if op_rec:
                    op_rec.status = "VERIFIED_READY"
                    op_rec.last_error = None
                    op_rec.steps_json = steps + [{"step": "VERIFICATION_GATE", "status": "VERIFIED"}]
                    op_rec.verification_json = verification
                    op_rec.completed_at = done_at
                if req:
                    req.status = "VERIFIED_READY"
                if prof:
                    prof.ssh_access_state = "VERIFIED_READY"
                    prof.ssh_expires_at = ssh_exp_dt
                    prof.updated_at = done_at
                if cap_res:
                    cap_res.status = "COMMITTED"
                    cap_res.expires_at = ssh_exp_dt
            else:
                if op_rec:
                    op_rec.status = "PROVISIONING_FAILED"
                    op_rec.last_error = err_msg
                    op_rec.verification_json = verification
                if req:
                    req.status = "PROVISIONING_FAILED"
                if prof:
                    prof.ssh_access_state = "PROVISIONING_FAILED"
                if cap_res:
                    cap_res.status = "RELEASED"

            try:
                from app.services.assistant_v4.event_store import EventEnvelope, insert_event_in_session

                prov_ev = "provisioning.succeeded" if verified_ready else "provisioning.failed"
                with session.begin_nested():
                    insert_event_in_session(
                        session,
                        EventEnvelope(
                            source="labops-core",
                            source_event_id=f"provisioning:{op_id}:v{app_rec.request_version}:{prov_ev.split('.')[-1]}",
                            event_type=prov_ev,
                            scope_type="user",
                            scope_id=username,
                            resource_id=request_id,
                            actor_id=approver,
                            source_version=int(app_rec.request_version),
                            occurred_at=done_at,
                            observed_at=done_at,
                            correlation_id=f"mram-{request_id}",
                            payload={
                                "operation_id": op_id,
                                "request_id": request_id,
                                "username": username,
                                "host_id": cfg.get("host_id"),
                                "status": "VERIFIED_READY" if verified_ready else "PROVISIONING_FAILED",
                                "error": str(err_msg or "")[:250] if not verified_ready else None,
                            },
                        ),
                    )
            except Exception:
                pass

        if member:
            member["ssh_access_state"] = "VERIFIED_READY" if verified_ready else "PROVISIONING_FAILED"
            member["ssh_expires_at"] = ssh_exp_dt.isoformat() if ssh_exp_dt else None
            member["member_category"] = req_cat
            member["version"] = int(member.get("version") or 1) + 1
            member["updated_at"] = done_at.isoformat()
            if verified_ready:
                if member.get("status") not in {"SUSPENDED", "REVOKED", "ARCHIVED", "DELETE_ELIGIBLE", "DELETED"}:
                    member["status"] = "ACTIVE"
                member["provisioning_state"] = "READY"
                member["overall_readiness"] = "READY"
                member["workspace_status"] = "VERIFIED"
                member["ssh_status"] = "ACTIVE"
                member["ssh_key_status"] = "ACTIVE"
                member["access_verified_at"] = done_at.isoformat()
                member.pop("legacy_access_status", None)
                member["is_web_registered"] = True
                member["linked_web_member_id"] = member_id
                member["linked_web_username"] = username
                member["linked_ssh_member_id"] = member_id
                member["linked_ssh_username"] = str(member.get("ssh_login_user") or username)
                member["ssh_linked_at"] = done_at.isoformat()
        labops_service._save_persistent_data()

        return {
            "operation_id": op_id,
            "status": "VERIFIED_READY" if verified_ready else "PROVISIONING_FAILED",
            "verification": verification,
            "error": err_msg,
        }

    # ------------------------------------------------------------------
    # P7: Verified Handover, One-Time Secret Claim, Reissue & Expiration
    # ------------------------------------------------------------------
    def get_member_ssh_access_handover(self, username: str) -> Dict[str, Any]:
        self.sweep_expired_ssh_access()
        labops_service.ensure_members_fresh()
        uname = str(username or "").strip().lower()
        member = next((m for m in labops_service.members if (m.get("username") or "").lower() == uname), None)
        if not member:
            with self.db.session_scope() as session:
                prof_only = (
                    session.execute(select(MemberProfileRecord).where(MemberProfileRecord.username == uname))
                    .scalars()
                    .first()
                )
                cred_only = (
                    session.execute(select(MemberWebCredentialRecord).where(MemberWebCredentialRecord.username == uname))
                    .scalars()
                    .first()
                )
                if prof_only is not None:
                    return {
                        "ready": False,
                        "ssh_access_state": prof_only.ssh_access_state or "NONE",
                        "web_account_status": prof_only.web_account_status or "ACTIVE",
                        "member_category": prof_only.member_category or "STUDENT",
                        "username": uname,
                        "ssh_expires_at": _as_utc(prof_only.ssh_expires_at).isoformat() if prof_only.ssh_expires_at else None,
                        "message": "Quyền truy cập SSH chưa sẵn sàng (cần Admin phê duyệt và hoàn tất kiểm chứng runtime).",
                    }
                if uname == "admin" or (cred_only is not None and str(cred_only.role).upper() == "ADMIN"):
                    return {
                        "ready": False,
                        "ssh_access_state": "NONE",
                        "web_account_status": "ACTIVE",
                        "member_category": "ADMIN",
                        "username": uname,
                        "ssh_expires_at": None,
                        "message": "Tài khoản quản trị (@admin) không dùng luồng bàn giao SSH cá nhân của sinh viên; hãy dùng trang Thành viên & Truy cập hoặc Duyệt Cấp phát SSH.",
                    }
                if cred_only is not None:
                    return {
                        "ready": False,
                        "ssh_access_state": "NONE",
                        "web_account_status": cred_only.status or "ACTIVE",
                        "member_category": "STUDENT",
                        "username": uname,
                        "ssh_expires_at": None,
                        "message": "Tài khoản chưa có hồ sơ SSH. Hãy gửi đơn xin cấp quyền SSH để Admin xem xét.",
                    }
            raise LookupError(f"Không tìm thấy thành viên '{uname}'")
        prof = self._ensure_legacy_profile(member)
        state = prof.ssh_access_state
        if state != "VERIFIED_READY":
            return {
                "ready": False,
                "ssh_access_state": state,
                "web_account_status": prof.web_account_status,
                "member_category": prof.member_category,
                "ssh_expires_at": _as_utc(prof.ssh_expires_at).isoformat() if prof.ssh_expires_at else None,
                "message": "Quyền truy cập SSH chưa sẵn sàng (cần Admin phê duyệt và hoàn tất kiểm chứng runtime).",
            }

        with self.db.session_scope() as session:
            prov_op = session.execute(
                select(ProvisioningOperationRecord)
                .where(
                    ProvisioningOperationRecord.member_id == prof.member_id,
                    ProvisioningOperationRecord.status == "VERIFIED_READY",
                )
                .order_by(ProvisioningOperationRecord.completed_at.desc())
            ).scalars().first()
            claim = session.execute(
                select(CredentialClaimRecord)
                .where(
                    CredentialClaimRecord.member_id == prof.member_id,
                    CredentialClaimRecord.provisioning_op_id == (prov_op.id if prov_op else None),
                    CredentialClaimRecord.claim_type == "SSH_TEMP_PASSWORD",
                    CredentialClaimRecord.status == "CLAIMED",
                )
            ).scalar_one_or_none()

        vault_avail = labops_service.ssh_access_manager.vault.get_availability(prof.member_id)
        ssh_host = str(member.get("ssh_ip") or ("192.168.167.251" if member.get("assigned_server") == "workstation" else "192.168.167.250"))
        ssh_port = int(member.get("ssh_port") or 22001)
        if claim is None and not bool(vault_avail.get("channel_secrets_available")) and not str(member.get("ssh_public_key") or "").strip():
            temp_pw, pw_meta = labops_service.ssh_access_manager.issue_temporary_password(
                member_id=prof.member_id,
                username=uname,
                actor="system-handover",
                ttl_seconds=86400,
            )
            ws_name = str(member.get("workspace_name") or f"ws-{uname}")
            ws_path = str(
                member.get("member_storage_directory")
                or member.get("workspace_dir")
                or member.get("host_storage_directory")
                or f"/media/aiot-ws1/5ce32377-6072-4c07-a066-807e89a5d48b/labops/users/{uname}"
            )
            generated = labops_service.ssh_access_manager.generate_encrypted_ed25519_keypair(
                uname, ws_name, require_passphrase=True
            )
            member["ssh_public_key"] = generated["public_key"]
            member["ssh_fingerprint"] = generated["fingerprint"]
            member["ssh_public_key_preview"] = f"{generated['public_key'][:42]}..."
            member["ssh_key_status"] = "ACTIVE"
            member["version"] = int(member.get("version") or 1) + 1
            member["updated_at"] = _utcnow().isoformat()
            try:
                labops_service.ssh_key_registry.register_key(
                    username=uname,
                    label="Admin-Managed Ed25519 Key",
                    public_key=generated["public_key"],
                    actor="system-handover",
                    member_id=prof.member_id,
                )
            except Exception:
                pass
            if member.get("workspace_id") and member.get("container_id"):
                srv_id = str(member.get("assigned_server") or "workstation")
                srv_obj = next((s for s in labops_service.servers if s.get("id") == srv_id), None)
                if srv_obj is not None and labops_service._is_host_online(srv_id):
                    try:
                        labops_service.node_agent.dispatch(
                            srv_obj,
                            "SYNC_SSH_AUTHORIZED_KEYS",
                            params={
                                "workspace_id": str(member["workspace_id"]),
                                "member_username": uname,
                                "keys_json": json.dumps([generated["public_key"]]),
                            },
                            idempotency_key=f"sync-keys-handover-{member['workspace_id']}-{uuid.uuid4().hex[:8]}",
                            actor_id="system-handover",
                            actor_role="ADMIN",
                        )
                        labops_service.ssh_access_manager.sync_container_first_login_gate(
                            workspace_id=str(member["workspace_id"]),
                            username=uname,
                            member_id=prof.member_id,
                            keys=[generated["public_key"]],
                            member_storage_dir=member.get("member_storage_directory"),
                        )
                    except Exception:
                        pass
            elif not getattr(labops_service, "_is_live_production_data_dir", False) and member.get("workspace_dir"):
                try:
                    labops_service.ssh_access_manager.write_authorized_keys_state(
                        str(member["workspace_dir"]), [generated["public_key"]]
                    )
                except Exception:
                    pass
            labops_service.ssh_access_manager.vault.store_provisioned_package(
                member_id=prof.member_id,
                username=uname,
                ssh_host=ssh_host,
                ssh_port=ssh_port,
                workspace_name=ws_name,
                workspace_path=ws_path,
                fingerprint=str(member.get("ssh_fingerprint") or generated["fingerprint"]),
                encrypted_private_key=generated["encrypted_private_key"],
                passphrase=generated["passphrase"],
                temporary_password=temp_pw,
                temp_password_expires_at=pw_meta.get("temp_password_expires_at"),
                vpn_guide="WireGuard VPN + SSH handover",
                ssh_login_user=uname,
            )
            labops_service._save_persistent_data()
            vault_avail = labops_service.ssh_access_manager.vault.get_availability(prof.member_id)
        vscode_snippet = (
            f"Host labops-{uname}\n"
            f"    HostName {ssh_host}\n"
            f"    Port {ssh_port}\n"
            f"    User {uname}\n"
            f"    IdentityFile ~/.ssh/id_ed25519_{uname}\n"
            f"    ServerAliveInterval 60"
        )
        return {
            "ready": True,
            "ssh_access_state": state,
            "web_account_status": prof.web_account_status,
            "member_category": prof.member_category,
            "member_id": prof.member_id,
            "username": uname,
            "assigned_server": member.get("assigned_server"),
            "ssh_host": ssh_host,
            "ssh_port": ssh_port,
            "workspace_dir": member.get("member_storage_directory") or member.get("workspace_dir") or member.get("host_storage_directory"),
            "cpu_limit_cores": member.get("cpu_limit_cores"),
            "ram_limit_gb": member.get("ram_limit_gb"),
            "storage_quota_gb": member.get("storage_quota_gb"),
            "gpu_permissions": list(member.get("gpu_permissions") or []),
            "gpu_mode": member.get("gpu_mode") or "NONE",
            "ssh_expires_at": _as_utc(prof.ssh_expires_at).isoformat() if prof.ssh_expires_at else None,
            "secret_already_claimed": claim is not None,
            "vault_available": bool(vault_avail.get("channel_secrets_available", True)),
            "provisioning_op_id": prov_op.id if prov_op else None,
            "ssh_command": f"ssh -p {ssh_port} {uname}@{ssh_host}",
            "vscode_config": vscode_snippet,
        }

    def claim_member_ssh_secret(self, *, username: str, step_up_jti: str) -> Dict[str, Any]:
        handover = self.get_member_ssh_access_handover(username)
        if not handover.get("ready"):
            raise MramConflictError("SSH_NOT_VERIFIED_READY", f"Tài khoản SSH đang ở trạng thái {handover.get('ssh_access_state')}; không thể nhận mật khẩu tạm", 409)
        if handover.get("secret_already_claimed"):
            raise MramConflictError("CREDENTIAL_ALREADY_CLAIMED", "Mật khẩu SSH tạm thời đã được nhận 1 lần trước đó. Hãy dùng chức năng Cấp lại (Reissue) nếu cần.", 410)

        member_id = str(handover["member_id"])
        op_id = handover.get("provisioning_op_id")
        try:
            secrets_pkg = labops_service.claim_member_secrets(member_id, actor=username)
        except LookupError as exc:
            raise MramConflictError(
                "VAULT_SECRET_EXPIRED_REISSUE_REQUIRED",
                "Bí mật tạm thời trong RAM Vault đã hết hạn hoặc máy chủ vừa khởi động lại. Vui lòng bấm Cấp lại mật khẩu tạm.",
                409,
            ) from exc

        now = _utcnow()
        with self.db.session_scope() as session:
            session.add(
                CredentialClaimRecord(
                    id=f"claim-{uuid.uuid4().hex[:10]}",
                    member_id=member_id,
                    provisioning_op_id=op_id,
                    claim_type="SSH_TEMP_PASSWORD",
                    claimed_by=username,
                    step_up_jti=step_up_jti,
                    status="CLAIMED",
                    claimed_at=now,
                )
            )
        return {
            "claimed": True,
            "temporary_password": secrets_pkg.get("temporary_password"),
            "passphrase": secrets_pkg.get("key_passphrase") or secrets_pkg.get("passphrase"),
            "temp_password_expires_at": secrets_pkg.get("temp_password_expires_at"),
            "one_time_notice": "Bí mật này chỉ hiển thị MỘT LẦN DUY NHẤT và đã được xóa khỏi bộ nhớ tạm.",
        }

    def reissue_member_ssh_secret(self, *, username: str, step_up_jti: str) -> Dict[str, Any]:
        labops_service.ensure_members_fresh()
        handover = self.get_member_ssh_access_handover(username)
        if not handover.get("ready"):
            raise MramConflictError("SSH_NOT_VERIFIED_READY", "Tài khoản SSH chưa ở trạng thái VERIFIED_READY", 409)

        uname = str(username or "").strip().lower()
        member_id = str(handover["member_id"])
        op_id = handover.get("provisioning_op_id")
        new_pw, pw_meta = labops_service.ssh_access_manager.issue_temporary_password(
            member_id=member_id,
            username=uname,
            actor=uname,
            ttl_seconds=86400,
            invalidate_previous=True,
        )
        exp_iso = str(pw_meta.get("temp_password_expires_at") or (_utcnow() + timedelta(hours=24)).isoformat())
        labops_service.ssh_access_manager.vault.store_reissued_temp_password(
            member_id=member_id,
            username=uname,
            temporary_password=new_pw,
            temp_password_expires_at=exp_iso,
            ttl_seconds=86400,
        )
        member = next(
            (
                m
                for m in labops_service.members
                if m.get("id") == member_id or (m.get("username") or "").strip().lower() == uname
            ),
            None,
        )
        if member is not None:
            member["activation_status"] = "PENDING_ACTIVATION"
            member["password_status"] = "CHANGE_REQUIRED"
            member["temp_password_expires_at"] = exp_iso
            member["one_time_package"] = labops_service.ssh_access_manager.vault.get_availability(member_id)
            member["version"] = int(member.get("version") or 1) + 1
            if member.get("workspace_id"):
                reg_keys = [
                    k.get("public_key")
                    for k in labops_service.ssh_key_registry.list_keys(username=uname, member_id=member_id)
                    if k.get("desired_state") != "REVOKED" and k.get("public_key")
                ]
                cur_pub = str(member.get("ssh_public_key") or "").strip()
                if cur_pub and cur_pub not in reg_keys:
                    reg_keys.insert(0, cur_pub)
                labops_service.ssh_access_manager.sync_container_first_login_gate(
                    workspace_id=str(member["workspace_id"]),
                    username=uname,
                    member_id=member_id,
                    keys=reg_keys,
                    member_storage_dir=member.get("member_storage_directory"),
                )
        now = _utcnow()
        with self.db.session_scope() as session:
            session.add(
                CredentialClaimRecord(
                    id=f"claim-{uuid.uuid4().hex[:10]}",
                    member_id=member_id,
                    provisioning_op_id=op_id,
                    claim_type="SSH_TEMP_PASSWORD",
                    claimed_by=uname,
                    step_up_jti=step_up_jti,
                    status="REISSUED",
                    claimed_at=now,
                )
            )
        labops_service._audit(uname, "SSH_TEMP_SECRET_REISSUED", "MEMBER", uname, "SUCCESS", "Thành viên yêu cầu cấp lại mật khẩu tạm SSH một lần")
        if member is not None:
            labops_service._save_persistent_data()
        else:
            labops_service.save_audit_logs()
        return {
            "reissued": True,
            "temporary_password": new_pw,
            "temp_password_expires_at": exp_iso,
            "one_time_notice": "Mật khẩu tạm mới chỉ hiển thị 1 lần và mật khẩu tạm cũ đã bị vô hiệu hóa.",
        }

    def sweep_expired_ssh_access(self, now_override: Optional[datetime] = None) -> List[str]:
        """Expire student SSH access at ssh_expires_at while retaining web_account_status=WEB_ACTIVE and disk data."""
        labops_service.ensure_members_fresh()
        now = _as_utc(now_override) or _utcnow()
        expired_usernames: List[str] = []
        with self.db.session_scope() as session:
            rows = session.execute(
                select(MemberProfileRecord).where(
                    MemberProfileRecord.member_category == "STUDENT",
                    MemberProfileRecord.ssh_access_state.in_(["VERIFIED_READY", "ACTIVE", "EXPIRING_SOON"]),
                    MemberProfileRecord.ssh_expires_at.is_not(None),
                )
            ).scalars().all()
            for prof in rows:
                exp = _as_utc(prof.ssh_expires_at)
                if exp and now > exp:
                    prof.ssh_access_state = "EXPIRED"
                    prof.web_account_status = "WEB_ACTIVE"  # Explicitly keep web login alive!
                    prof.updated_at = now
                    expired_usernames.append(prof.username)
                    # Release GPU/capacity reservation
                    res_rows = session.execute(
                        select(CapacityReservationRecord).where(
                            CapacityReservationRecord.member_id == prof.member_id,
                            CapacityReservationRecord.status.in_(["RESERVED", "COMMITTED"]),
                        )
                    ).scalars().all()
                    for cr in res_rows:
                        cr.status = "EXPIRED"

        for uname in expired_usernames:
            m = next((x for x in labops_service.members if (x.get("username") or "").lower() == uname), None)
            if m:
                m["ssh_access_state"] = "EXPIRED"
                m["ssh_status"] = "REVOKED"
                # Keep m['status'] != SUSPENDED/REVOKED on web login check or keep SSH revoked separately
                m["gpu_permissions"] = []
                m["provisioning_state"] = "SSH_EXPIRED"
                m["version"] = int(m.get("version") or 1) + 1
            labops_service._audit(
                "system",
                "MRAM_STUDENT_SSH_EXPIRED",
                "MEMBER",
                uname,
                "EXPIRED",
                "Hết hạn quyền SSH sinh viên: giữ web login WEB_ACTIVE và dữ liệu trên đĩa, thu hồi quyền SSH/GPU.",
            )
        if expired_usernames:
            labops_service._save_persistent_data()
        return expired_usernames

    def get_briefing_summary_counters(self) -> Dict[str, Any]:
        """Deterministic MRAM counters for the 08:00 Daily Executive Brief."""
        self.expire_overdue_requests()
        self.sweep_expired_ssh_access()
        now = _utcnow()
        soon = now + timedelta(days=7)
        with self.db.session_scope() as session:
            reqs = session.execute(select(ServerAccessRequestRecord)).scalars().all()
            profs = session.execute(select(MemberProfileRecord)).scalars().all()
        return {
            "pending_ssh_requests": sum(1 for r in reqs if r.status in {"PENDING_APPROVAL", "NEED_INFO"}),
            "expired_unreviewed_requests": sum(1 for r in reqs if r.status == "EXPIRED"),
            "provisioning_failed": sum(1 for r in reqs if r.status == "PROVISIONING_FAILED"),
            "expiring_ssh_accounts_7d": sum(
                1 for p in profs
                if p.ssh_access_state in {"VERIFIED_READY", "ACTIVE"}
                and p.ssh_expires_at is not None
                and _as_utc(p.ssh_expires_at) <= soon  # type: ignore[operator]
            ),
            "web_only_members": sum(1 for p in profs if p.ssh_access_state == "NONE"),
        }


mram_service = MramService()
