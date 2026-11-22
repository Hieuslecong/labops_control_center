import asyncio
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

os.environ.setdefault("LABOPS_ENV", "development")

from app.main import app  # noqa: E402
from app.services.zalo_assistant.ai_gateway_client import AIGatewayClient  # noqa: E402
from app.services.zalo_assistant.bot_core import ZaloBotEngine  # noqa: E402
from app.services.zalo_assistant.quota_ledger import QuotaLedger  # noqa: E402
from app.services.zalo_assistant.report_generator import ReportGenerator  # noqa: E402
from app.services.zalo_assistant.repo_alert_monitor import RepoSecurityAlertMonitor  # noqa: E402


class _FakeBot:
    def __init__(self, fail_for=None):
        self.sent = []
        self.fail_for = set(fail_for or [])
        self._seq = 0

    async def send_message(self, *, chat_id: str, text: str):
        if chat_id in self.fail_for:
            raise RuntimeError("Zalo transport error")
        self._seq += 1
        self.sent.append({"chat_id": chat_id, "text": text})
        return type("_Sent", (), {"message_id": f"prov-{self._seq}"})()


def _make_monitor(tmp_path: Path, requests_list, authorized=None):
    data_dir = str(tmp_path)
    ledger = QuotaLedger(data_dir)
    engine = ZaloBotEngine(
        quota_ledger=ledger,
        ai_client=AIGatewayClient(),
        report_generator=ReportGenerator(data_dir),
        config_path=os.path.join(data_dir, "zalo_config.json"),
    )
    engine.authorized_users = list(authorized if authorized is not None else ["admin-zalo-1"])
    engine.save_persisted_config()
    monitor = RepoSecurityAlertMonitor(
        data_dir=data_dir,
        quota_ledger=ledger,
        bot_engine=engine,
        fetch_requests_fn=lambda: requests_list,
        poll_interval_seconds=30.0,
        bootstrap_lookback_hours=24.0,
    )
    return monitor, ledger


def test_repo_alert_monitor_notifies_findings_and_scan_failed_without_secrets(tmp_path: Path):
    now_iso = datetime.now(timezone.utc).isoformat()
    old_iso = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()

    requests = [
        # Clean workspace clone -> must NOT alert
        {
            "id": "req-clean",
            "owner": "TrangLe1912",
            "repo_name": "clean-repo",
            "commit_sha": "a" * 40,
            "submitted_by": "tranglt",
            "target_workspace": "ws-tranglt",
            "source": "workspace_clone",
            "clone_status": "CLONED",
            "review_status": "CLEAN",
            "state": "AUTO_APPROVED",
            "submitted_at": now_iso,
            "updated_at": now_iso,
            "report": {
                "secrets_count": 0,
                "vulnerabilities": {"critical": 0, "high": 0, "medium": 0, "low": 0},
                "misconfig_high_count": 0,
                "coverage": {"overall_coverage": "POLICY_COMPLETE"},
            },
        },
        # Post-clone findings in Permissive mode -> MUST alert with masked/no secret value
        {
            "id": "req-risk",
            "owner": "TrangLe1912",
            "repo_name": "risk-constrained-endpointing",
            "commit_sha": "401ad754616072d26d92ef3548398bef2e47fbb5",
            "submitted_by": "tranglt",
            "target_workspace": "ws-tranglt",
            "source": "workspace_clone",
            "clone_status": "CLONED",
            "review_status": "REVIEW_REQUIRED",
            "state": "AUTO_APPROVED",
            "submitted_at": now_iso,
            "updated_at": now_iso,
            "report": {
                "secrets_count": 1,
                "vulnerabilities": {"critical": 0, "high": 2, "medium": 0, "low": 0},
                "misconfig_high_count": 0,
                "coverage": {"overall_coverage": "POLICY_COMPLETE"},
                "findings": [
                    {
                        "id": "generic-api-key",
                        "type": "SECRET",
                        "severity": "HIGH",
                        "title": "API Key",
                        "target_file": "configs/secret.env:3",
                        "masked_value": "SUPER_SECRET_RAW_LEAK_CHECK",
                    }
                ],
            },
        },
        # Post-clone scan failed -> MUST alert Admin
        {
            "id": "req-scanfail",
            "owner": "TrangLe1912",
            "repo_name": "broken-scan",
            "commit_sha": "b" * 40,
            "submitted_by": "tranglt",
            "target_workspace": "ws-tranglt",
            "source": "workspace_clone",
            "clone_status": "CLONED",
            "review_status": "SCAN_FAILED",
            "state": "FAILED",
            "error": "trivy fs failed",
            "submitted_at": now_iso,
            "updated_at": now_iso,
        },
        # Already decided -> must NOT alert
        {
            "id": "req-decided",
            "owner": "org",
            "repo_name": "decided-repo",
            "commit_sha": "c" * 40,
            "submitted_by": "tranglt",
            "state": "MANUALLY_APPROVED",
            "review_status": "APPROVED",
            "decision": {"verdict": "MANUALLY_APPROVED", "reviewer": "admin", "reason": "ok"},
            "submitted_at": now_iso,
            "updated_at": now_iso,
        },
        # Older than 24h on first bootstrap -> skipped on baseline
        {
            "id": "req-ancient",
            "owner": "org",
            "repo_name": "old-failed",
            "commit_sha": "d" * 40,
            "submitted_by": "admin",
            "state": "FAILED",
            "error": "old error",
            "submitted_at": old_iso,
            "updated_at": old_iso,
        },
    ]

    monitor, ledger = _make_monitor(tmp_path, requests)
    bot = _FakeBot()

    res1 = asyncio.run(monitor.check_and_notify(bot, force=True))
    assert res1["sent"] == 2
    assert len(bot.sent) == 2

    combined = "\n---\n".join(m["text"] for m in bot.sent)
    assert "risk-constrained-endpointing" in combined
    assert "broken-scan" in combined
    assert "clean-repo" not in combined
    assert "decided-repo" not in combined
    assert "old-failed" not in combined
    # Critical security check: never include raw/masked secret value in Zalo notification
    assert "SUPER_SECRET_RAW_LEAK_CHECK" not in combined
    assert "configs/secret.env:3" in combined

    # Quota was deducted from critical reserve (200)
    q = ledger.get_status()
    assert q["critical_sent"] == 2
    assert q["standard_sent"] == 0

    # Second run must be idempotent (0 duplicates)
    res2 = asyncio.run(monitor.check_and_notify(bot, force=True))
    assert res2["sent"] == 0
    assert len(bot.sent) == 2


def test_repo_alert_monitor_refunds_quota_on_send_failure(tmp_path: Path):
    now_iso = datetime.now(timezone.utc).isoformat()
    requests = [
        {
            "id": "req-fail-send",
            "owner": "TrangLe1912",
            "repo_name": "risk-constrained-endpointing",
            "commit_sha": "e" * 40,
            "submitted_by": "tranglt",
            "source": "workspace_clone",
            "clone_status": "CLONED",
            "review_status": "REVIEW_REQUIRED",
            "state": "AUTO_APPROVED",
            "submitted_at": now_iso,
            "updated_at": now_iso,
            "report": {
                "secrets_count": 1,
                "vulnerabilities": {"critical": 0, "high": 0, "medium": 0, "low": 0},
                "misconfig_high_count": 0,
                "coverage": {"overall_coverage": "POLICY_COMPLETE"},
            },
        }
    ]

    monitor, ledger = _make_monitor(tmp_path, requests, authorized=["broken-chat"])
    failing_bot = _FakeBot(fail_for={"broken-chat"})
    res = asyncio.run(monitor.check_and_notify(failing_bot, force=True))
    assert res["sent"] == 0
    # Quota must be refunded when send fails
    assert ledger.get_status()["critical_sent"] == 0


def test_admin_rescan_endpoint_forwards_to_agent(monkeypatch):
    from app.api.v1.endpoints import repositories as repo_ep
    from app.core.security import issue_token

    calls = []

    def fake_agent_call(path, method="GET", data=None, *, principal=None, timeout=10.0):
        calls.append((method, path, data))
        if method == "GET" and path == "requests/req-123":
            return {
                "id": "req-123",
                "submitted_by": "tranglt",
                "canonical_repo": "https://github.com/TrangLe1912/risk-constrained-endpointing",
                "source": "workspace_clone",
                "local_repo_path": "risk-constrained-endpointing",
            }
        if method == "POST" and path == "requests/req-123/rescan":
            return {"id": "req-123", "state": "REQUESTED", "clone_status": "CLONED", "review_status": "SCANNING"}
        raise AssertionError(f"Unexpected call: {method} {path}")

    monkeypatch.setattr(repo_ep, "_agent_call", fake_agent_call)
    monkeypatch.setattr(repo_ep.service, "_audit", lambda *a, **kw: None)
    monkeypatch.setattr(repo_ep.service, "_save_persistent_data", lambda *a, **kw: None)
    if hasattr(repo_ep.service, "save_audit_logs"):
        monkeypatch.setattr(repo_ep.service, "save_audit_logs", lambda *a, **kw: None)

    tok = issue_token("admin", "ADMIN")
    client = TestClient(app)
    resp = client.post(
        "/api/v1/repositories/requests/req-123/rescan",
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["clone_status"] == "CLONED"
    assert body["review_status"] == "SCANNING"
    assert any(c[0] == "POST" and c[1] == "requests/req-123/rescan" for c in calls)
