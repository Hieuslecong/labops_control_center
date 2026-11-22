"""
Zalo Bot Event-Driven & Reconciliation Repository Security Alert Monitor (v2)
- Creates/updates unified Incidents for post-clone SECURITY_FINDINGS and SCAN_FAILED events.
- Sends alerts STRICTLY to verified ADMIN Zalo recipients (never RESEARCHER/STUDENT).
- Tracks delivery state independently per (alert_key, recipient_id) so partial failures retry only failed recipients.
- Strips secret/token/masked values and avoids localhost links in remote alerts.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from app.core.config import settings
from app.db.atomic_json import atomic_write_json, safe_load_json
from app.services.zalo_assistant.identity_rbac import ZaloIdentityRBACService
from app.services.zalo_assistant.incident_engine import UnifiedIncidentEngine

logger = logging.getLogger("zalo_repo_alerts")

_IN_PROGRESS_STATES = {
    "REQUESTED",
    "SUBMITTED",
    "VALIDATING",
    "RESOLVING_COMMIT",
    "CHECKING_CACHE",
    "FETCHING",
    "SCANNING",
    "POLICY_EVALUATION",
    "PUBLISHING",
    "DELIVERING",
}

_FRACTIONAL_RE = re.compile(r"\.(\d+)(?=(Z|[+-]\d{2}:\d{2})$)")


def _parse_iso_ts(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str) or not raw.strip():
        return None
    cleaned = _FRACTIONAL_RE.sub(lambda m: "." + m.group(1)[:6].ljust(6, "0"), raw.strip())
    if cleaned.endswith("Z"):
        cleaned = cleaned[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _is_timeout_error(exc: Exception) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    return "timeout" in name or "timed out" in msg or "timeout" in msg


def _public_security_url() -> str:
    base = (
        os.getenv("LABOPS_PUBLIC_DASHBOARD_URL")
        or os.getenv("NEXT_PUBLIC_APP_URL")
        or "https://aiotserver.fitdnu.id.vn"
    ).strip().rstrip("/")
    if "localhost" in base.lower() or "127.0.0.1" in base:
        return "https://aiotserver.fitdnu.id.vn/dashboard/security"
    return f"{base}/dashboard/security"


def _has_security_concerns(req: Dict[str, Any]) -> bool:
    report = req.get("report")
    if not isinstance(report, dict):
        return False
    vulns = report.get("vulnerabilities") or {}
    coverage = report.get("coverage") or {}
    submodules = str(coverage.get("submodules") or "NOT_PRESENT")
    lfs = str(coverage.get("lfs") or "NOT_PRESENT")
    overall = str(coverage.get("overall_coverage") or "POLICY_COMPLETE")
    return (
        int(report.get("secrets_count") or 0) > 0
        or int(vulns.get("critical") or 0) > 0
        or int(vulns.get("high") or 0) > 0
        or int(report.get("misconfig_high_count") or 0) > 0
        or submodules != "NOT_PRESENT"
        or lfs != "NOT_PRESENT"
        or overall != "POLICY_COMPLETE"
    )


def _fetch_agent_requests_sync() -> List[Dict[str, Any]]:
    raw_secret = os.environ.get("LABOPS_AGENT_SECRET")
    if raw_secret is not None:
        agent_token = raw_secret.strip()
    else:
        agent_token = (
            os.getenv("LABOPS_NODE_AGENT_TOKEN")
            or getattr(settings, "LABOPS_AGENT_SECRET", "")
            or getattr(settings, "LABOPS_NODE_AGENT_TOKEN", "")
            or ""
        ).strip()
    if not agent_token:
        return []

    local_base = (
        os.getenv("LABOPS_LOCAL_AGENT_URL")
        or getattr(settings, "LABOPS_LOCAL_AGENT_URL", "")
        or "http://127.0.0.1:9100"
    ).strip().rstrip("/")
    url_path = "/api/v1/agent/repositoryguard/requests"
    url = f"{local_base}{url_path}"

    headers: Dict[str, str] = {
        "X-LabOps-Agent-Secret": agent_token,
        "Authorization": f"Bearer {agent_token}",
        "X-LabOps-Verified-Actor": "admin",
        "X-LabOps-Verified-Role": "ADMIN",
    }
    broker_secret = (
        os.getenv("LABOPS_BROKER_VERIFY_SECRET")
        or os.getenv("LABOPS_REPO_BROKER_SECRET")
        or ""
    ).strip()
    if broker_secret:
        ts = str(int(time.time()))
        signing_payload = f"{ts}:admin:ADMIN:GET:{url_path}"
        sig = hmac.new(broker_secret.encode("utf-8"), signing_payload.encode("utf-8"), hashlib.sha256).hexdigest()
        headers["X-LabOps-Verified-Timestamp"] = ts
        headers["X-LabOps-Verified-Signature"] = sig

    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=5.0) as resp:
        raw = resp.read().decode("utf-8")
        data = json.loads(raw) if raw else []
        return data if isinstance(data, list) else []


class RepoSecurityAlertMonitor:
    def __init__(
        self,
        data_dir: str,
        quota_ledger: Any,
        bot_engine: Any,
        fetch_requests_fn: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        poll_interval_seconds: float = 30.0,
        bootstrap_lookback_hours: float = 24.0,
        identity_rbac: Optional[ZaloIdentityRBACService] = None,
        incident_engine: Optional[UnifiedIncidentEngine] = None,
    ):
        self.data_dir = os.path.abspath(data_dir)
        self.quota_ledger = quota_ledger
        self.bot_engine = bot_engine
        self.fetch_requests_fn = fetch_requests_fn or _fetch_agent_requests_sync
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.bootstrap_lookback_hours = float(bootstrap_lookback_hours)
        self.identity_rbac = identity_rbac or ZaloIdentityRBACService(self.data_dir)
        self.incident_engine = incident_engine or UnifiedIncidentEngine(self.data_dir)
        self.state_path = os.path.join(self.data_dir, "zalo_repo_alerts_state.json")
        self._last_poll_monotonic: float = 0.0

    def _new_state(self) -> Dict[str, Any]:
        return {"sent_alerts": {}, "last_checked_at": None, "pending_events": []}

    def _load_state(self) -> Dict[str, Any]:
        loaded = safe_load_json(self.state_path, self._new_state)
        if not isinstance(loaded, dict):
            return self._new_state()
        loaded.setdefault("sent_alerts", {})
        loaded.setdefault("pending_events", [])
        return loaded

    def _save_state(self, state: Dict[str, Any]) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        sent = state.get("sent_alerts", {})
        if len(sent) > 1000:
            items = sorted(sent.items(), key=lambda kv: str((kv[1] or {}).get("ts", "")))
            state["sent_alerts"] = dict(items[-1000:])
        atomic_write_json(self.state_path, state, mode=0o600, use_lock=False)

    def push_event(self, event_req: Dict[str, Any]) -> None:
        """Event-driven queue push invoked immediately when Repository Guard finishes/updates a scan."""
        if not isinstance(event_req, dict) or not event_req.get("id"):
            return
        state = self._load_state()
        queue = list(state.get("pending_events") or [])
        queue.append(event_req)
        state["pending_events"] = queue[-100:]
        self._save_state(state)

    def get_admin_recipients(self) -> List[str]:
        explicit = [
            uid.strip()
            for uid in os.getenv("ZALO_ALERT_ADMIN_IDS", "").split(",")
            if uid.strip() and uid.strip() != "*"
        ]
        if hasattr(self.bot_engine, "reload_persisted_config"):
            self.bot_engine.reload_persisted_config()
        fallback = list(getattr(self.bot_engine, "authorized_users", []) or [])
        return self.identity_rbac.get_verified_admin_recipients(
            fallback_authorized_users=fallback,
            explicit_admin_ids=explicit or None,
            alert_category="repo_alerts",
        )

    @staticmethod
    def classify_alert(req: Dict[str, Any]) -> Optional[str]:
        if not isinstance(req, dict):
            return None
        state = str(req.get("state") or "").upper()
        if state in _IN_PROGRESS_STATES:
            return None
        if req.get("decision"):
            return None

        review_status = str(req.get("review_status") or "").upper()
        if review_status == "SCAN_FAILED" or state == "FAILED":
            return "SCAN_FAILED"
        if review_status == "REVIEW_REQUIRED" or state == "REVIEW_REQUIRED":
            return "SECURITY_FINDINGS"
        if state in {"AUTO_APPROVED", "DELIVERED"} and _has_security_concerns(req):
            return "SECURITY_FINDINGS"
        return None

    @staticmethod
    def build_alert_key(req: Dict[str, Any], alert_kind: str) -> str:
        req_id = str(req.get("id") or "unknown")
        commit_part = str(req.get("commit_sha") or req.get("requested_ref") or "head")[:16]
        return f"{req_id}:{commit_part}:{alert_kind}"

    def _record_incident_for_alert(self, req: Dict[str, Any], alert_kind: str) -> Optional[Dict[str, Any]]:
        owner = str(req.get("owner") or "").strip()
        repo_name = str(req.get("repo_name") or "").strip()
        repo_label = f"{owner}/{repo_name}" if owner and repo_name else str(req.get("canonical_repo") or req.get("repo_url") or "unknown")
        commit_sha = str(req.get("commit_sha") or req.get("requested_ref") or "head")
        submitter = str(req.get("submitted_by") or "unknown")

        if alert_kind == "SCAN_FAILED":
            inc, _ = self.incident_engine.upsert_incident(
                event_type="REPO_SCAN_FAILED",
                severity="P1",
                confidence="CONFIRMED",
                source="repository_guard",
                resource_id=repo_label,
                host_id="workstation",
                owner_id=submitter,
                fingerprint=f"repo-scan-failed:{repo_label}:{commit_sha[:16]}",
                title=f"Quét hậu kiểm repo {repo_label} (@{submitter}) thất bại",
                evidence={
                    "request_id": req.get("id"),
                    "workspace": req.get("target_workspace"),
                    "commit_sha": commit_sha,
                    "error": str(req.get("error") or "")[:300],
                },
                recommended_action="Kiểm tra nguyên nhân lỗi scan trên Trung tâm An ninh và bấm Quét lại.",
            )
            return inc

        report = req.get("report") or {}
        vulns = report.get("vulnerabilities") or {}
        secrets = int(report.get("secrets_count") or 0)
        crit = int(vulns.get("critical") or 0)
        high = int(vulns.get("high") or 0)
        misconfig = int(report.get("misconfig_high_count") or 0)
        sev = "P0" if secrets > 0 or crit > 0 else "P1"

        sanitized_findings = [
            {
                "id": str(f.get("id") or ""),
                "type": str(f.get("type") or ""),
                "severity": str(f.get("severity") or ""),
                "target_file": str(f.get("target_file") or ""),
            }
            for f in (report.get("findings") or [])[:10]
            if isinstance(f, dict)
        ]

        inc, _ = self.incident_engine.upsert_incident(
            event_type="REPO_SECURITY_FINDINGS",
            severity=sev,
            confidence="CONFIRMED",
            source="repository_guard",
            resource_id=repo_label,
            host_id="workstation",
            owner_id=submitter,
            fingerprint=f"repo-findings:{repo_label}:{commit_sha[:16]}",
            title=f"Repo {repo_label} (@{submitter}) cần kiểm duyệt sau clone ({secrets} Secret, {crit} Crit, {high} High)",
            evidence={
                "request_id": req.get("id"),
                "workspace": req.get("target_workspace"),
                "commit_sha": commit_sha,
                "secrets_count": secrets,
                "critical": crit,
                "high": high,
                "misconfig_high_count": misconfig,
                "findings": sanitized_findings,
            },
            recommended_action="Kiểm tra chi tiết findings trên Trung tâm An ninh (Secret hoặc dependency CVE không mặc định là malware) và Duyệt/Từ chối hậu kiểm.",
        )
        return inc

    @staticmethod
    def format_alert_message(req: Dict[str, Any], alert_kind: str, incident_id: Optional[str] = None) -> str:
        owner = str(req.get("owner") or "").strip()
        repo_name = str(req.get("repo_name") or "").strip()
        repo_label = f"{owner}/{repo_name}" if owner and repo_name else str(req.get("canonical_repo") or req.get("repo_url") or "unknown")
        submitter = str(req.get("submitted_by") or "unknown")
        workspace = str(req.get("target_workspace") or f"ws-{submitter}")
        commit_short = str(req.get("commit_sha") or req.get("requested_ref") or "HEAD")[:8]
        security_url = _public_security_url()
        inc_line = f"\n• Sự cố: {incident_id}" if incident_id else ""

        clone_status = str(req.get("clone_status") or "").upper()
        if not clone_status:
            clone_status = "CLONED" if req.get("source") == "workspace_clone" or str(req.get("state") or "").upper() in {"AUTO_APPROVED", "DELIVERED"} else "FAILED"
        clone_label = "Thành công" if clone_status == "CLONED" else clone_status

        if alert_kind == "SCAN_FAILED":
            err_text = str(req.get("error") or "Lỗi không xác định khi quét hậu kiểm").strip()
            if len(err_text) > 220:
                err_text = err_text[:217] + "..."
            return (
                "🚨 [CẢNH BÁO KIỂM DUYỆT REPO - LỖI QUÉT HẬU KIỂM]"
                f"{inc_line}\n"
                f"• Thành viên: @{submitter} ({workspace})\n"
                f"• Kho mã: {repo_label} ({commit_short})\n"
                f"• Trạng thái Clone: {clone_label}\n"
                "• Kiểm duyệt sau Clone: Lỗi quét hậu kiểm (chưa xác nhận an toàn)\n"
                f"• Nguyên nhân: {err_text}\n"
                f"👉 Xử lý & Quét lại: {security_url}"
            )

        report = req.get("report") or {}
        vulns = report.get("vulnerabilities") or {}
        coverage = report.get("coverage") or {}
        secrets = int(report.get("secrets_count") or 0)
        critical = int(vulns.get("critical") or 0)
        high = int(vulns.get("high") or 0)
        misconfig = int(report.get("misconfig_high_count") or 0)
        subm = str(coverage.get("submodules") or "NOT_PRESENT")
        lfs = str(coverage.get("lfs") or "NOT_PRESENT")

        lines = [
            "⚠️ [CẢNH BÁO KIỂM DUYỆT REPO SAU CLONE]" + (f" ({incident_id})" if incident_id else ""),
            f"• Thành viên: @{submitter} ({workspace})",
            f"• Kho mã: {repo_label} ({commit_short})",
            f"• Trạng thái Clone: {clone_label}",
            "• Kiểm duyệt sau Clone: Cần Admin kiểm duyệt",
            f"• Tổng hợp: {secrets} Secret | {critical} Critical CVE | {high} High CVE | {misconfig} Misconfig",
        ]
        if subm != "NOT_PRESENT" or lfs != "NOT_PRESENT":
            lines.append(f"• Bổ sung: submodules={subm}, lfs={lfs}")

        findings = report.get("findings") or []
        for item in findings[:3]:
            if not isinstance(item, dict):
                continue
            f_type = str(item.get("type") or "RISK")
            f_sev = str(item.get("severity") or "")
            f_id = str(item.get("id") or "")
            f_file = str(item.get("target_file") or "")
            # NEVER include masked_value, tokens, or raw secret payloads
            lines.append(f"  - [{f_type}/{f_sev}] {f_id} ({f_file})")

        lines.append(f"👉 Security Center: {security_url}")
        return "\n".join(lines)

    async def check_and_notify(
        self,
        bot: Any,
        *,
        force: bool = False,
        now_monotonic: Optional[float] = None,
        direct_events: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        curr_mono = time.monotonic() if now_monotonic is None else now_monotonic
        if not force and not direct_events and self._last_poll_monotonic > 0 and (curr_mono - self._last_poll_monotonic) < self.poll_interval_seconds:
            return {"skipped": True, "reason": "THROTTLED"}
        self._last_poll_monotonic = curr_mono

        recipients = self.get_admin_recipients()
        if not recipients or bot is None:
            return {"skipped": True, "reason": "NO_VERIFIED_ADMIN_RECIPIENTS_OR_BOT"}

        is_first_bootstrap = not os.path.exists(self.state_path)
        state = self._load_state()
        sent_alerts = state.setdefault("sent_alerts", {})
        queued_events = list(state.get("pending_events") or [])
        state["pending_events"] = []

        polled_requests: List[Dict[str, Any]] = []
        if direct_events is not None:
            polled_requests = list(direct_events)
        else:
            try:
                polled_requests = self.fetch_requests_fn() or []
            except Exception as fetch_err:
                logger.warning("Lỗi đọc requests từ Go Agent: %s", fetch_err)
                polled_requests = []

        # Merge event-driven queue + polled reconciliation (deduplicated by id, prefer latest)
        merged_by_id: Dict[str, Dict[str, Any]] = {}
        for req in polled_requests + queued_events:
            if isinstance(req, dict) and req.get("id"):
                merged_by_id[str(req["id"])] = req
        requests = list(merged_by_id.values())

        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.bootstrap_lookback_hours)
        sent_count = 0
        incidents_created = 0

        for req in requests:
            alert_kind = self.classify_alert(req)
            if not alert_kind:
                continue
            alert_key = self.build_alert_key(req, alert_kind)
            entry = sent_alerts.get(alert_key)

            if is_first_bootstrap and entry is None and direct_events is None:
                ts = _parse_iso_ts(req.get("updated_at") or req.get("submitted_at"))
                if ts is not None and ts < cutoff:
                    sent_alerts[alert_key] = {
                        "status": "BOOTSTRAP_SKIPPED",
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "recipients": {},
                    }
                    continue

            # Ensure backward compatibility if old state stored plain status without per-recipient map
            if isinstance(entry, dict) and entry.get("status") in {"BOOTSTRAP_SKIPPED"}:
                continue

            per_recip: Dict[str, str] = {}
            if isinstance(entry, dict):
                per_recip = dict(entry.get("recipients") or {})
                if not per_recip and entry.get("status") == "SENT":
                    # Legacy completed alert -> already sent to previous recipients
                    continue

            inc = self._record_incident_for_alert(req, alert_kind)
            inc_id = inc.get("incident_id") if inc else None
            if inc:
                incidents_created += 1

            message = self.format_alert_message(req, alert_kind, incident_id=inc_id)

            for recipient in recipients:
                prev_r_status = per_recip.get(recipient)
                if prev_r_status in {"SENT", "DELIVERY_UNKNOWN"}:
                    continue

                idemp = f"repo-alert:{alert_key}:{recipient}"
                try:
                    _, outbox_id, _ = self.quota_ledger.reserve_outbox(
                        recipient_id=recipient,
                        message_text=message,
                        is_critical_alert=True,
                        idempotency_key=idemp,
                        metadata={
                            "kind": "REPO_POST_CLONE_ALERT",
                            "request_id": req.get("id"),
                            "incident_id": inc_id,
                            "alert_kind": alert_kind,
                        },
                    )
                except ValueError:
                    per_recip[recipient] = "SENT"
                    continue
                except Exception as q_err:
                    logger.warning("Không thể reserve quota cảnh báo repo %s tới %s: %s", alert_key, recipient, q_err)
                    per_recip[recipient] = "FAILED_RETRYABLE"
                    continue

                if not outbox_id:
                    continue
                if hasattr(self.quota_ledger, "is_already_dispatched") and self.quota_ledger.is_already_dispatched(outbox_id):
                    per_recip[recipient] = "SENT"
                    continue

                try:
                    sent_res = await bot.send_message(chat_id=recipient, text=message)
                    prov_id = getattr(sent_res, "message_id", None)
                    self.quota_ledger.confirm_sent(
                        outbox_id,
                        provider_message_id=str(prov_id) if prov_id is not None else None,
                    )
                    per_recip[recipient] = "SENT"
                    sent_count += 1
                    logger.info("🚨 Đã gửi cảnh báo %s (%s) tới Admin Zalo %s", alert_key, inc_id or "-", recipient)
                except Exception as send_err:
                    if _is_timeout_error(send_err) and hasattr(self.quota_ledger, "mark_delivery_unknown"):
                        self.quota_ledger.mark_delivery_unknown(outbox_id, str(send_err))
                        per_recip[recipient] = "DELIVERY_UNKNOWN"
                    else:
                        try:
                            self.quota_ledger.mark_delivery_failed(outbox_id, str(send_err))
                        except Exception:
                            pass
                        per_recip[recipient] = "FAILED_RETRYABLE"
                    logger.error("Lỗi gửi cảnh báo repo %s tới %s: %s", alert_key, recipient, send_err)

            all_done = bool(recipients) and all(per_recip.get(r) in {"SENT", "DELIVERY_UNKNOWN"} for r in recipients)
            sent_alerts[alert_key] = {
                "status": "SENT" if all_done else "PARTIAL_OR_RETRYING",
                "alert_kind": alert_kind,
                "incident_id": inc_id,
                "ts": datetime.now(timezone.utc).isoformat(),
                "recipients": per_recip,
            }

        state["last_checked_at"] = datetime.now(timezone.utc).isoformat()
        self._save_state(state)
        return {"checked": len(requests), "sent": sent_count, "incidents_upserted": incidents_created}
