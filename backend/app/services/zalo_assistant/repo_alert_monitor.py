"""
Zalo Bot Post-Clone Repository Security Alert Monitor
Periodically inspects RepositoryGuard requests from Go Node Agent and sends concise
alerts to Admin Zalo recipients whenever a cloned repository requires security review
or encounters a post-clone scan failure.
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
from typing import Any, Awaitable, Callable, Dict, List, Optional

from app.core.config import settings
from app.db.atomic_json import atomic_write_json, safe_load_json

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
    ):
        self.data_dir = data_dir
        self.quota_ledger = quota_ledger
        self.bot_engine = bot_engine
        self.fetch_requests_fn = fetch_requests_fn or _fetch_agent_requests_sync
        self.poll_interval_seconds = float(poll_interval_seconds)
        self.bootstrap_lookback_hours = float(bootstrap_lookback_hours)
        self.state_path = os.path.join(self.data_dir, "zalo_repo_alerts_state.json")
        self._last_poll_monotonic: float = 0.0

    def _new_state(self) -> Dict[str, Any]:
        return {"sent_alerts": {}, "last_checked_at": None}

    def _load_state(self) -> Dict[str, Any]:
        loaded = safe_load_json(self.state_path, self._new_state)
        if not isinstance(loaded, dict):
            return self._new_state()
        loaded.setdefault("sent_alerts", {})
        return loaded

    def _save_state(self, state: Dict[str, Any]) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        # Keep bounded history so file size stays tiny
        sent = state.get("sent_alerts", {})
        if len(sent) > 1000:
            items = sorted(sent.items(), key=lambda kv: str((kv[1] or {}).get("ts", "")))
            state["sent_alerts"] = dict(items[-1000:])
        atomic_write_json(self.state_path, state, mode=0o600, use_lock=False)

    def get_admin_recipients(self) -> List[str]:
        explicit = [
            uid.strip()
            for uid in os.getenv("ZALO_ALERT_ADMIN_IDS", "").split(",")
            if uid.strip() and uid.strip() != "*"
        ]
        if explicit:
            return list(dict.fromkeys(explicit))
        if hasattr(self.bot_engine, "reload_persisted_config"):
            self.bot_engine.reload_persisted_config()
        users = [
            str(uid).strip()
            for uid in (getattr(self.bot_engine, "authorized_users", []) or [])
            if str(uid).strip() and str(uid).strip() != "*"
        ]
        return list(dict.fromkeys(users))

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

    @staticmethod
    def format_alert_message(req: Dict[str, Any], alert_kind: str) -> str:
        owner = str(req.get("owner") or "").strip()
        repo_name = str(req.get("repo_name") or "").strip()
        repo_label = f"{owner}/{repo_name}" if owner and repo_name else str(req.get("canonical_repo") or req.get("repo_url") or "unknown")
        submitter = str(req.get("submitted_by") or "unknown")
        workspace = str(req.get("target_workspace") or f"ws-{submitter}")
        commit_short = str(req.get("commit_sha") or req.get("requested_ref") or "HEAD")[:8]

        clone_status = str(req.get("clone_status") or "").upper()
        if not clone_status:
            clone_status = "CLONED" if req.get("source") == "workspace_clone" or str(req.get("state") or "").upper() in {"AUTO_APPROVED", "DELIVERED"} else "FAILED"
        clone_label = "Thành công" if clone_status == "CLONED" else clone_status

        if alert_kind == "SCAN_FAILED":
            err_text = str(req.get("error") or "Lỗi không xác định khi quét hậu kiểm").strip()
            if len(err_text) > 220:
                err_text = err_text[:217] + "..."
            return (
                "🚨 [CẢNH BÁO KIỂM DUYỆT REPO - LỖI QUÉT HẬU KIỂM]\n"
                f"• Thành viên: @{submitter} ({workspace})\n"
                f"• Kho mã: {repo_label} ({commit_short})\n"
                f"• Trạng thái Clone: {clone_label}\n"
                "• Kiểm duyệt sau Clone: Lỗi quét hậu kiểm\n"
                f"• Chi tiết: {err_text}\n"
                "👉 Vui lòng mở Trung tâm An ninh để kiểm tra hoặc bấm Quét lại."
            )

        report = req.get("report") or {}
        vulns = report.get("vulnerabilities") or {}
        secrets = int(report.get("secrets_count") or 0)
        critical = int(vulns.get("critical") or 0)
        high = int(vulns.get("high") or 0)
        misconfig = int(report.get("misconfig_high_count") or 0)

        lines = [
            "⚠️ [CẢNH BÁO KIỂM DUYỆT REPO SAU CLONE]",
            f"• Thành viên: @{submitter} ({workspace})",
            f"• Kho mã: {repo_label} ({commit_short})",
            f"• Trạng thái Clone: {clone_label}",
            "• Kiểm duyệt sau Clone: Cần Admin kiểm duyệt",
            f"• Phát hiện: {secrets} Secret | {critical} Critical | {high} High | {misconfig} Misconfig",
        ]

        findings = report.get("findings") or []
        for item in findings[:3]:
            if not isinstance(item, dict):
                continue
            f_type = str(item.get("type") or "RISK")
            f_sev = str(item.get("severity") or "")
            f_id = str(item.get("id") or "")
            f_file = str(item.get("target_file") or "")
            # Never emit masked_value or secret values
            lines.append(f"  - [{f_type}/{f_sev}] {f_id} ({f_file})")

        lines.append("👉 Mở Dashboard > Trung tâm An ninh để xem chi tiết & Duyệt/Từ chối.")
        return "\n".join(lines)

    async def check_and_notify(
        self,
        bot: Any,
        *,
        force: bool = False,
        now_monotonic: Optional[float] = None,
    ) -> Dict[str, Any]:
        curr_mono = time.monotonic() if now_monotonic is None else now_monotonic
        if not force and self._last_poll_monotonic > 0 and (curr_mono - self._last_poll_monotonic) < self.poll_interval_seconds:
            return {"skipped": True, "reason": "THROTTLED"}
        self._last_poll_monotonic = curr_mono

        recipients = self.get_admin_recipients()
        if not recipients or bot is None:
            return {"skipped": True, "reason": "NO_RECIPIENTS_OR_BOT"}

        requests = self.fetch_requests_fn()
        if not isinstance(requests, list):
            return {"checked": 0, "sent": 0}

        is_first_bootstrap = not os.path.exists(self.state_path)
        state = self._load_state()
        sent_alerts = state.setdefault("sent_alerts", {})
        cutoff = datetime.now(timezone.utc) - timedelta(hours=self.bootstrap_lookback_hours)

        sent_count = 0
        for req in requests:
            alert_kind = self.classify_alert(req)
            if not alert_kind:
                continue
            alert_key = self.build_alert_key(req, alert_kind)
            if alert_key in sent_alerts:
                continue

            if is_first_bootstrap:
                ts = _parse_iso_ts(req.get("updated_at") or req.get("submitted_at"))
                if ts is not None and ts < cutoff:
                    sent_alerts[alert_key] = {
                        "status": "BOOTSTRAP_SKIPPED",
                        "ts": datetime.now(timezone.utc).isoformat(),
                    }
                    continue

            message = self.format_alert_message(req, alert_kind)
            delivered_any = False
            for recipient in recipients:
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
                            "alert_kind": alert_kind,
                        },
                    )
                except ValueError:
                    # Idempotency hit with previous content
                    delivered_any = True
                    continue
                except Exception as q_err:
                    logger.warning("Không thể reserve quota cảnh báo repo %s tới %s: %s", alert_key, recipient, q_err)
                    continue

                if not outbox_id:
                    continue

                try:
                    sent_res = await bot.send_message(chat_id=recipient, text=message)
                    prov_id = getattr(sent_res, "message_id", None)
                    self.quota_ledger.confirm_sent(
                        outbox_id,
                        provider_message_id=str(prov_id) if prov_id is not None else None,
                    )
                    delivered_any = True
                    sent_count += 1
                    logger.info("🚨 Đã gửi cảnh báo kiểm duyệt repo %s tới Admin Zalo %s", alert_key, recipient)
                except Exception as send_err:
                    try:
                        self.quota_ledger.mark_delivery_failed(outbox_id, str(send_err))
                    except Exception:
                        pass
                    logger.error("Lỗi gửi cảnh báo repo %s tới %s: %s", alert_key, recipient, send_err)

            if delivered_any:
                sent_alerts[alert_key] = {
                    "status": "SENT",
                    "alert_kind": alert_kind,
                    "ts": datetime.now(timezone.utc).isoformat(),
                }

        state["last_checked_at"] = datetime.now(timezone.utc).isoformat()
        self._save_state(state)
        return {"checked": len(requests), "sent": sent_count}
