"""
LabOps Personal AI Agent V4 — Reviewed SkillRegistry, Markdown Knowledge Base,
SSRF Outbound Guard, and Opt-in Admin-Isolated External Connectors.
All external connectors default to disabled (`NOT_CONFIGURED`) and personal connectors
are strictly restricted to verified ADMIN principals.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import socket
import urllib.parse
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.db.models import V4ConnectorStateRecord, V4KnowledgeDocumentRecord, V4WebhookDeliveryRecord
from app.db.session import DatabaseManager
from app.services.assistant_v4.admin_action_catalog import ADMIN_ACTION_CATALOG
from app.services.assistant_v4.config import ExternalConnectorsConfig
from app.services.assistant_v4.event_store import EventEnvelope, EventStoreService, redact_text
from app.services.zalo_assistant.identity_rbac import ZaloPrincipal


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return _utcnow()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class SkillManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: int = 1
    purpose: str
    allowed_roles: List[str]
    tool_allowlist: List[str]
    data_classification: str  # internal_infra | general_public | admin_personal
    outbound_domains: List[str] = []
    max_tool_calls: int = 8
    risk_tier: str = "R0"
    enabled: bool = True


class SkillRegistry:
    """Reviewed immutable manifest registry — no autonomous prompt/skill self-install."""

    def __init__(self, connector_cfg: Optional[ExternalConnectorsConfig] = None):
        cfg = connector_cfg or ExternalConnectorsConfig()
        self._skills: Dict[str, SkillManifest] = {
            "labops": SkillManifest(
                name="labops",
                version=2,
                purpose="Giám sát hạ tầng, GPU, CPU/RAM/Disk, jobs, workspaces, sự cố, đơn SSH và toàn bộ thao tác quản trị Web Admin đồng bộ sang Zalo qua xác nhận hiểu đúng & OTP DM 1-1",
                allowed_roles=["ADMIN", "RESEARCHER", "STUDENT"],
                tool_allowlist=[
                    "get_node_status",
                    "get_gpu_metrics",
                    "get_service_health",
                    "get_storage_usage",
                    "get_metric_history",
                    "compare_resource_metrics",
                    "list_incident_history",
                    "list_my_jobs",
                    "get_job",
                    "get_workspace",
                    "get_container_stats",
                    "get_gpu_allocations",
                    "get_member_info",
                    "list_pending_requests",
                    "get_request",
                    "get_group_usage",
                    "get_audit_trail",
                    "get_vpn_status",
                    "search_redacted_logs",
                    "get_repository_scan",
                    "inspect_member_detail",
                    "list_member_ssh_keys",
                    "list_member_vpn_devices",
                    "list_repository_requests",
                    "list_security_scans",
                    "list_jobs_and_processes",
                    "clarify_admin_action",
                    "propose_ack",
                    "propose_resolve",
                    "propose_reject",
                    "propose_approval",
                ] + [f"propose_{aid}" for aid in sorted(ADMIN_ACTION_CATALOG.keys())],
                data_classification="internal_infra",
                enabled=True,
            ),
            "investigation": SkillManifest(
                name="investigation",
                version=1,
                purpose="Điều tra nguyên nhân sự cố có kiểm chứng bằng chứng read-only",
                allowed_roles=["ADMIN", "RESEARCHER"],
                tool_allowlist=["get_node_status", "get_gpu_metrics", "get_metric_history", "list_my_jobs", "get_container_stats", "get_storage_usage", "search_runbooks", "get_runbook_version"],
                data_classification="internal_infra",
                enabled=True,
            ),
            "knowledge": SkillManifest(
                name="knowledge",
                version=1,
                purpose="Tra cứu FAQ SSH/Docker/Conda và runbook đã được duyệt",
                allowed_roles=["ADMIN", "RESEARCHER", "STUDENT"],
                tool_allowlist=["search_runbooks", "get_runbook_version", "search_faq"],
                data_classification="internal_infra",
                enabled=True,
            ),
            "tasks": SkillManifest(
                name="tasks",
                version=1,
                purpose="Quản lý nhắc việc và điều kiện theo dõi watch rule có xác nhận",
                allowed_roles=["ADMIN", "RESEARCHER", "STUDENT"],
                tool_allowlist=["list_my_tasks", "propose_watch_rule", "propose_reminder", "propose_cancel_task"],
                data_classification="internal_infra",
                risk_tier="R1",
                enabled=True,
            ),
            "programming": SkillManifest(
                name="programming",
                version=1,
                purpose="Hỗ trợ lập trình, PyTorch, CUDA và debugging từ văn bản người dùng gửi",
                allowed_roles=["ADMIN", "RESEARCHER", "STUDENT"],
                tool_allowlist=[],
                data_classification="general_public",
                enabled=True,
            ),
            "general": SkillManifest(
                name="general",
                version=1,
                purpose="Hội thoại tự nhiên, tóm tắt, dịch thuật không chạm dữ liệu lab nội bộ",
                allowed_roles=["ADMIN", "RESEARCHER", "STUDENT"],
                tool_allowlist=[],
                data_classification="general_public",
                enabled=True,
            ),
            "research": SkillManifest(
                name="research",
                version=1,
                purpose="Tìm bài báo công khai với nguồn trích dẫn (opt-in)",
                allowed_roles=["ADMIN"],
                tool_allowlist=[],
                data_classification="general_public",
                outbound_domains=list(cfg.allowed_web_domains),
                enabled=bool(cfg.web_search),
            ),
            "github": SkillManifest(
                name="github",
                version=1,
                purpose="Nhận GitHub webhook và đọc repo trong phạm vi cho phép (opt-in)",
                allowed_roles=["ADMIN"],
                tool_allowlist=["get_repository_scan"],
                data_classification="internal_infra",
                outbound_domains=["api.github.com"],
                enabled=bool(cfg.github),
            ),
            "personal_email": SkillManifest(
                name="personal_email",
                version=1,
                purpose="Đọc và tóm tắt email quan trọng của riêng Admin (chỉ đọc, opt-in)",
                allowed_roles=["ADMIN"],
                tool_allowlist=[],
                data_classification="admin_personal",
                enabled=bool(cfg.email),
            ),
            "personal_calendar": SkillManifest(
                name="personal_calendar",
                version=1,
                purpose="Nhắc lịch cá nhân của riêng Admin (chỉ đọc, opt-in)",
                allowed_roles=["ADMIN"],
                tool_allowlist=[],
                data_classification="admin_personal",
                enabled=bool(cfg.calendar),
            ),
            "personal_docs": SkillManifest(
                name="personal_docs",
                version=1,
                purpose="Tra cứu tài liệu cá nhân của riêng Admin (chỉ đọc, opt-in)",
                allowed_roles=["ADMIN"],
                tool_allowlist=[],
                data_classification="admin_personal",
                enabled=bool(cfg.documents),
            ),
        }

    def authorize_skill(self, skill_name: str, principal: ZaloPrincipal) -> SkillManifest:
        if skill_name not in self._skills:
            raise LookupError(f"Unknown skill '{skill_name}'")
        sk = self._skills[skill_name]
        if not principal.is_authorized or principal.role.upper() not in set(sk.allowed_roles):
            raise PermissionError(f"Role '{principal.role}' is not authorized for skill '{skill_name}'")
        if not sk.enabled:
            raise RuntimeError(f"CONNECTOR_NOT_CONFIGURED: Skill '{skill_name}' hiện đang tắt (opt-in disabled).")
        return sk

    def list_manifests(self, principal: ZaloPrincipal) -> List[Dict[str, Any]]:
        out = []
        for sk in self._skills.values():
            if principal.is_admin or principal.role.upper() in set(sk.allowed_roles):
                out.append(sk.model_dump())
        return out


DEFAULT_KNOWLEDGE_DOCS = [
    {
        "doc_id": "kb-ssh-docker-guide-v1",
        "title": "Hướng dẫn kết nối WireGuard VPN & SSH vào Workspace Lab AIoT",
        "scope": "shared",
        "tags": ["ssh", "vpn", "wireguard", "docker", "vscode"],
        "content": (
            "1. Bật WireGuard tunnel trước khi kết nối SSH.\n"
            "2. Sinh viên/Researcher dùng cổng SSH riêng được cấp phát (ssh -p <port> <user>@192.168.167.251).\n"
            "3. Không mount docker.sock hoặc chạy sudo trong workspace.\n"
            "4. Dùng `gitaiot clone <url>` để gửi yêu cầu quét Repository Guard trước khi nạp mã nguồn mới."
        ),
    },
    {
        "doc_id": "kb-cuda-oom-runbook-v1",
        "title": "Xử lý lỗi CUDA Out of Memory và tối ưu VRAM A30 24GB",
        "scope": "shared",
        "tags": ["cuda", "oom", "vram", "pytorch", "gpu"],
        "content": (
            "1. Kiểm tra VRAM còn trống bằng lệnh `/gpu` hoặc `/status`.\n"
            "2. Giảm `batch_size`, bật gradient accumulation hoặc mixed precision (`torch.amp`).\n"
            "3. Tránh để tiến trình Python cũ giữ VRAM sau khi script dừng; kiểm tra `/myjobs`."
        ),
    },
    {
        "doc_id": "kb-admin-incident-runbook-v1",
        "title": "Quy trình Admin xử lý node OFFLINE và Repository Guard cảnh báo",
        "scope": "admin",
        "tags": ["admin", "rack", "repository_guard", "incident"],
        "content": (
            "1. Khi Rack offline: xác minh heartbeat, kiểm tra kết nối mạng nội bộ và trạng thái labops-agent.\n"
            "2. Khi Repository Guard cảnh báo finding/failed: kiểm tra Trung tâm An ninh /dashboard/security trước khi Approve hoặc Rescan."
        ),
    },
]


class KnowledgeBaseService:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    def bootstrap_defaults(self) -> int:
        now = _utcnow()
        inserted = 0
        with self.db.session_scope() as session:
            for item in DEFAULT_KNOWLEDGE_DOCS:
                if session.get(V4KnowledgeDocumentRecord, item["doc_id"]) is None:
                    h = hashlib.sha256(item["content"].encode("utf-8")).hexdigest()
                    session.add(
                        V4KnowledgeDocumentRecord(
                            doc_id=item["doc_id"],
                            title=item["title"],
                            owner_id="admin",
                            scope=item["scope"],
                            source="reviewed_baseline",
                            version=1,
                            reviewed_by="admin",
                            status="ACTIVE",
                            content_markdown=item["content"],
                            content_hash=h,
                            tags_json=item["tags"],
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    inserted += 1
        return inserted

    def search(self, query: str, principal: ZaloPrincipal, *, limit: int = 5) -> List[Dict[str, Any]]:
        terms = [t.strip().lower() for t in str(query or "").split() if t.strip()]
        with self.db.session_scope() as session:
            docs = session.execute(select(V4KnowledgeDocumentRecord).where(V4KnowledgeDocumentRecord.status == "ACTIVE")).scalars().all()
            scored = []
            for d in docs:
                if d.scope == "admin" and not principal.is_admin:
                    continue
                if d.scope not in {"shared", "admin"} and d.owner_id != principal.username and not principal.is_admin:
                    continue
                hay = f"{d.title}\n{' '.join(d.tags_json or [])}\n{d.content_markdown}".lower()
                score = sum(2 if t in d.title.lower() else 1 for t in terms if t in hay)
                if score > 0 or not terms:
                    scored.append((score, d))
            scored.sort(key=lambda x: (x[0], x[1].version), reverse=True)
            return [
                {
                    "doc_id": d.doc_id,
                    "title": d.title,
                    "scope": d.scope,
                    "version": d.version,
                    "reviewed_by": d.reviewed_by,
                    "snippet": d.content_markdown[:500],
                }
                for _, d in scored[:limit]
            ]


class SSRFBlockedError(PermissionError):
    pass


class SafeOutboundGuard:
    """Validates external URLs against scheme, domain allowlist, DNS resolution, private/mapped IPs, and redirect safety."""

    _BLOCKED_NETS = (
        ipaddress.ip_network("100.64.0.0/10"),   # RFC 6598 CGNAT
        ipaddress.ip_network("198.18.0.0/15"),   # Benchmark testing
        ipaddress.ip_network("64:ff9b::/96"),    # NAT64 well-known
        ipaddress.ip_network("64:ff9b:1::/48"),  # NAT64 local-use
        ipaddress.ip_network("fc00::/7"),        # IPv6 Unique Local
        ipaddress.ip_network("fe80::/10"),       # IPv6 Link-Local
    )

    @classmethod
    def _is_forbidden_ip(cls, ip_str: str) -> bool:
        cleaned = str(ip_str or "").strip()
        if not cleaned or "%" in cleaned:
            return True
        try:
            ip = ipaddress.ip_address(cleaned)
            if isinstance(ip, ipaddress.IPv6Address):
                if ip.ipv4_mapped is not None and cls._is_forbidden_ip(str(ip.ipv4_mapped)):
                    return True
                if ip.sixtofour is not None and cls._is_forbidden_ip(str(ip.sixtofour)):
                    return True
                if ip.teredo is not None:
                    if cls._is_forbidden_ip(str(ip.teredo[0])) or cls._is_forbidden_ip(str(ip.teredo[1])):
                        return True
            if any(ip in net for net in cls._BLOCKED_NETS if ip.version == net.version):
                return True
            return (
                (not ip.is_global)
                or ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_reserved
                or ip.is_unspecified
            )
        except ValueError:
            return True

    @classmethod
    def validate_url(cls, raw_url: str, *, allowed_domains: List[str], resolver_fn=None) -> str:
        parsed = urllib.parse.urlparse(str(raw_url or "").strip())
        if parsed.scheme.lower() != "https":
            raise SSRFBlockedError("Chỉ cho phép HTTPS outbound.")
        host = (parsed.hostname or "").lower().rstrip(".")
        if not host:
            raise SSRFBlockedError("Thiếu hostname.")
        if parsed.port not in (None, 443):
            raise SSRFBlockedError("Chỉ cho phép cổng HTTPS 443.")
        if parsed.username or parsed.password:
            raise SSRFBlockedError("Cấm nhúng thông tin đăng nhập trong URL.")

        allowed_norm = {d.lower().lstrip(".") for d in allowed_domains if d.strip()}
        if not any(host == d or host.endswith(f".{d}") for d in allowed_norm):
            raise SSRFBlockedError(f"Domain '{host}' không nằm trong outbound allowlist.")

        # Direct IP literal check
        try:
            ipaddress.ip_address(host.split("%", 1)[0])
            raise SSRFBlockedError("Không cho phép truy cập trực tiếp qua IP literal.")
        except ValueError:
            pass

        # DNS resolution check (guards DNS rebinding to internal RFC1918/IPv6-mapped/CGNAT/NAT64)
        try:
            infos = resolver_fn(host) if resolver_fn else socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            if not infos:
                raise SSRFBlockedError("DNS resolution trả về rỗng.")
            for info in infos:
                addr = info[4][0] if isinstance(info, (list, tuple)) and len(info) >= 5 else str(info)
                if cls._is_forbidden_ip(addr):
                    raise SSRFBlockedError(f"Blocked internal/private resolved IP: {addr}")
        except SSRFBlockedError:
            raise
        except Exception as exc:
            raise SSRFBlockedError(f"DNS verification failed: {exc}") from exc

        return parsed.geturl()

    @classmethod
    def fetch_safe_url(
        cls,
        raw_url: str,
        *,
        allowed_domains: List[str],
        max_redirects: int = 3,
        timeout: float = 10.0,
        max_bytes: int = 65536,
        resolver_fn=None,
        client_factory=None,
    ) -> Dict[str, Any]:
        """Fetch an HTTPS URL with follow_redirects=False and validate every 3xx hop against SSRF."""
        import httpx

        bounded_timeout = max(0.5, min(float(timeout), 30.0))
        current_url = cls.validate_url(raw_url, allowed_domains=allowed_domains, resolver_fn=resolver_fn)
        redirects_followed = 0
        factory = client_factory or (lambda: httpx.Client(follow_redirects=False, timeout=bounded_timeout))

        with factory() as client:
            while True:
                resp = client.get(current_url)
                status = int(getattr(resp, "status_code", 0))
                if status in {301, 302, 303, 307, 308}:
                    location = (resp.headers.get("location") or resp.headers.get("Location") or "").strip()
                    if not location:
                        raise SSRFBlockedError("Redirect response missing Location header.")
                    if redirects_followed >= max_redirects:
                        raise SSRFBlockedError(f"Exceeded maximum redirects ({max_redirects}).")
                    next_url = urllib.parse.urljoin(current_url, location)
                    current_url = cls.validate_url(next_url, allowed_domains=allowed_domains, resolver_fn=resolver_fn)
                    redirects_followed += 1
                    continue
                body = getattr(resp, "text", "") or ""
                return {
                    "status_code": status,
                    "final_url": current_url,
                    "redirects": redirects_followed,
                    "timeout_s": bounded_timeout,
                    "body_redacted": redact_text(body[:max_bytes]),
                }


class ConnectorsService:
    """Manages opt-in Admin-isolated connectors and verified GitHub webhooks."""

    CONNECTOR_TYPES = ("github", "web_search", "personal_email", "personal_calendar", "personal_docs")

    def __init__(self, db_manager: DatabaseManager, *, cfg: Optional[ExternalConnectorsConfig] = None):
        self.db = db_manager
        self.cfg = cfg or ExternalConnectorsConfig()
        self.timeout_s = max(1.0, min(float(getattr(self.cfg, "request_timeout_s", 10.0)), 60.0))
        self.events = EventStoreService(db_manager)

    def get_connector_statuses(self, principal: ZaloPrincipal) -> List[Dict[str, Any]]:
        if not principal.is_admin:
            raise PermissionError("Chỉ Admin được phép xem trạng thái kết nối mở rộng.")
        enabled_map = {
            "github": self.cfg.github,
            "web_search": self.cfg.web_search,
            "personal_email": self.cfg.email,
            "personal_calendar": self.cfg.calendar,
            "personal_docs": self.cfg.documents,
        }
        with self.db.session_scope() as session:
            rows = {r.connector_id: r for r in session.execute(select(V4ConnectorStateRecord)).scalars().all()}
            out = []
            for ctype in self.CONNECTOR_TYPES:
                r = rows.get(ctype)
                is_en = enabled_map.get(ctype, False)
                out.append(
                    {
                        "connector_id": ctype,
                        "enabled": is_en,
                        "status": (r.status if (r and is_en) else "NOT_CONFIGURED"),
                        "owner_scope": "ADMIN_ONLY" if ctype.startswith("personal_") else "ADMIN_LAB",
                        "request_timeout_s": self.timeout_s,
                        "last_success_at": _as_utc(r.last_success_at).isoformat() if (r and r.last_success_at) else None,
                        "last_error": redact_text(r.last_error or "") if (r and r.last_error) else None,
                    }
                )
            return out

    def query_personal_connector(self, connector_type: str, principal: ZaloPrincipal) -> Dict[str, Any]:
        if not principal.is_admin:
            raise PermissionError("FORBIDDEN: Kết nối cá nhân (email/lịch/tài liệu) chỉ dành riêng cho Admin.")
        flag_map = {
            "personal_email": self.cfg.email,
            "personal_calendar": self.cfg.calendar,
            "personal_docs": self.cfg.documents,
            "web_search": self.cfg.web_search,
            "github": self.cfg.github,
        }
        if not flag_map.get(connector_type, False):
            return {"status": "NOT_CONFIGURED", "connector": connector_type, "message": f"Connector '{connector_type}' chưa được kích hoạt hoặc chưa cấu hình xác thực."}
        return {"status": "NOT_CONFIGURED", "connector": connector_type, "message": "Adapter đang ở chế độ đọc an toàn và chưa có token kết nối bên ngoài."}

    def ingest_github_webhook(
        self,
        *,
        delivery_id: str,
        event_name: str,
        signature_header: str,
        webhook_secret: str,
        raw_body: bytes,
    ) -> Dict[str, Any]:
        if not self.cfg.github:
            raise RuntimeError("CONNECTOR_DISABLED: GitHub connector đang tắt.")
        if not delivery_id or not webhook_secret:
            raise PermissionError("MISSING_DELIVERY_OR_SECRET")

        expected_sig = "sha256=" + hmac.new(webhook_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected_sig, str(signature_header or "").strip()):
            raise PermissionError("INVALID_GITHUB_SIGNATURE")

        payload = json.loads(raw_body.decode("utf-8"))
        repo_full = str((payload.get("repository") or {}).get("full_name") or "")
        allowed = {r.lower() for r in self.cfg.allowed_github_repos if r.strip()}
        if allowed and repo_full.lower() not in allowed:
            raise PermissionError(f"Repository '{repo_full}' không nằm trong allowlist.")

        ev_map = {
            "workflow_run": "github.action_failed",
            "pull_request": "github.review_requested",
            "issues": "github.issue_assigned",
        }
        mapped_ev = ev_map.get(event_name)
        now = _utcnow()
        with self.db.session_scope() as session:
            dup = session.execute(
                select(V4WebhookDeliveryRecord).where(
                    V4WebhookDeliveryRecord.connector_id == "github",
                    V4WebhookDeliveryRecord.delivery_id == delivery_id,
                )
            ).scalar_one_or_none()
            if dup is not None:
                return {"accepted": True, "duplicate": True, "delivery_id": delivery_id}

            session.add(
                V4WebhookDeliveryRecord(
                    id=f"wh4-{uuid.uuid4().hex[:12]}",
                    connector_id="github",
                    delivery_id=delivery_id,
                    event_type=event_name,
                    signature_verified=True,
                    status="ACCEPTED",
                    received_at=now,
                )
            )
            if mapped_ev:
                from app.services.assistant_v4.event_store import insert_event_in_session

                insert_event_in_session(
                    session,
                    EventEnvelope(
                        source="github",
                        source_event_id=f"gh:{delivery_id}",
                        event_type=mapped_ev,
                        scope_type="admin",
                        scope_id="admin",
                        resource_id=repo_full or delivery_id,
                        occurred_at=now,
                        observed_at=now,
                        payload={"repository": repo_full, "github_event": event_name, "action": payload.get("action")},
                    ),
                )
        return {"accepted": True, "duplicate": False, "delivery_id": delivery_id, "event": mapped_ev}
