"""Phase 5 & 6 tests: Evidence-grounded RCA, runbooks, Knowledge Base, Skills, SSRF & Connectors."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

os.environ.setdefault("LABOPS_ENV", "development")

from app.db.session import DatabaseManager  # noqa: E402
from app.services.assistant_v4.config import ExternalConnectorsConfig  # noqa: E402
from app.services.assistant_v4.investigation_service import InvestigationService  # noqa: E402
from app.services.assistant_v4.secure_tools import SecureToolExecutor  # noqa: E402
from app.services.assistant_v4.skills_and_connectors import (  # noqa: E402
    ConnectorsService,
    KnowledgeBaseService,
    SSRFBlockedError,
    SafeOutboundGuard,
    SkillRegistry,
)
from app.services.zalo_assistant.identity_rbac import ZaloPrincipal  # noqa: E402
from app.services.zalo_assistant.incident_engine import UnifiedIncidentEngine  # noqa: E402
from app.services.zalo_assistant.telemetry_collector import UnifiedTelemetryCollector  # noqa: E402


def test_phase5_rca_verified_vs_insufficient_evidence_and_no_power_guess(tmp_path: Path):
    db = DatabaseManager(str(tmp_path))
    now_utc = datetime.now(timezone.utc)
    core = SimpleNamespace(
        local_host_id="workstation",
        servers=[
            {"id": "workstation", "hostname": "aiot-ws1", "status": "ONLINE", "last_heartbeat_at": now_utc.isoformat()},
            {"id": "track", "hostname": "aiotlab-rack", "status": "OFFLINE", "last_heartbeat_at": (now_utc - timedelta(hours=24)).isoformat()},
        ],
        gpus=[{"id": "gpu0", "uuid": "GPU-1", "server_id": "workstation", "name": "A30", "utilization_gpu": 98, "temp_celsius": 90.0}],
        jobs=[{"id": "job-1", "owner": "vinhnq", "name": "train.py", "status": "RUNNING", "host_id": "workstation"}],
        workspace_controller=SimpleNamespace(list_workspaces=lambda: []),
        get_gpu_allocations_by_device=lambda: [],
    )
    tel = UnifiedTelemetryCollector(str(tmp_path), db_manager=db, core_service=core)
    inc_eng = UnifiedIncidentEngine(str(tmp_path), db_manager=db)
    tools = SecureToolExecutor(str(tmp_path), db_manager=db, telemetry_collector=tel, incident_engine=inc_eng)
    rca = InvestigationService(db, tool_executor=tools)

    # Case 1: Rack offline WITHOUT IPMI/PDU proof -> verified_root_cause MUST be None (never guesses power loss)
    inc_rack, _ = inc_eng.upsert_incident(
        event_type="NODE_HEARTBEAT_LOST",
        severity="P0",
        source="telemetry",
        resource_id="track",
        host_id="track",
        fingerprint="rack-offline",
        title="Rack offline",
    )
    rep1 = rca.investigate_incident(inc_rack["incident_id"])
    assert rep1["verified_root_cause"] is None
    assert rep1["runbook_id"] == "rb-node-unreachable-v1"
    assert len(rep1["missing_information"]) >= 1

    # Duplicate investigation within cooldown returns deduplicated report
    rep1_dup = rca.investigate_incident(inc_rack["incident_id"])
    assert rep1_dup.get("deduplicated") is True

    # Case 2: GPU overheat with verified online high util + running job -> verified_root_cause populated with evidence refs
    inc_gpu, _ = inc_eng.upsert_incident(
        event_type="GPU_THERMAL_HIGH",
        severity="P1",
        source="gpu",
        resource_id="workstation:GPU-1",
        host_id="workstation",
        fingerprint="gpu-hot",
        title="GPU temp 90C",
    )
    rep2 = rca.investigate_incident(inc_gpu["incident_id"])
    assert rep2["verified_root_cause"] is not None
    assert "90" in rep2["verified_root_cause"]
    assert len(rep2["evidence_refs"]) >= 2

    # Admin review feedback stores cleanly without changing rules
    fb = rca.submit_feedback(rep2["run_id"], reviewer="admin", feedback="Verified workload thermal spike.")
    assert fb["reviewed_by"] == "admin"


def test_c01_c02_kb_ssrf_connectors_and_student_isolation(tmp_path: Path):
    db = DatabaseManager(str(tmp_path))
    kb = KnowledgeBaseService(db)
    kb.bootstrap_defaults()

    admin = ZaloPrincipal("z-adm", "admin", "m-adm", "ADMIN", "Admin", True, True)
    student = ZaloPrincipal("z-sv", "sv_hoa", "m-sv", "STUDENT", "Hoa", True, True)

    # Admin sees admin + shared KB; Student NEVER sees admin-only KB
    res_adm = kb.search("rack offline", admin)
    res_sv = kb.search("rack offline", student)
    assert any(d["doc_id"] == "kb-admin-incident-runbook-v1" for d in res_adm)
    assert not any(d["doc_id"] == "kb-admin-incident-runbook-v1" for d in res_sv)

    # Skill & Connector RBAC isolation (C02)
    skills = SkillRegistry()
    with pytest.raises(PermissionError):
        skills.authorize_skill("personal_email", student)
    conns = ConnectorsService(db)
    with pytest.raises(PermissionError):
        conns.query_personal_connector("personal_email", student)
    assert conns.query_personal_connector("personal_email", admin)["status"] == "NOT_CONFIGURED"

    # SSRF Defense (C01)
    with pytest.raises(SSRFBlockedError):
        SafeOutboundGuard.validate_url("http://arxiv.org/abs/123", allowed_domains=["arxiv.org"])
    with pytest.raises(SSRFBlockedError):
        SafeOutboundGuard.validate_url("https://169.254.169.254/latest/meta-data", allowed_domains=["169.254.169.254"])
    with pytest.raises(SSRFBlockedError):
        SafeOutboundGuard.validate_url(
            "https://arxiv.org/abs/2401.0001",
            allowed_domains=["arxiv.org"],
            resolver_fn=lambda h: [(2, 1, 6, "", ("127.0.0.1", 443))],
        )
    assert SafeOutboundGuard.validate_url(
        "https://arxiv.org/abs/2401.0001",
        allowed_domains=["arxiv.org"],
        resolver_fn=lambda h: [(2, 1, 6, "", ("128.84.158.119", 443))],
    ) == "https://arxiv.org/abs/2401.0001"

    # GitHub webhook signature & delivery idempotency
    gh_conns = ConnectorsService(db, cfg=ExternalConnectorsConfig(github=True, allowed_github_repos=["Hieuslecong/labops_control_center"]))
    raw_body = json.dumps({"repository": {"full_name": "Hieuslecong/labops_control_center"}, "action": "opened"}).encode()
    sig = "sha256=" + hmac.new(b"gh-secret", raw_body, hashlib.sha256).hexdigest()

    with pytest.raises(PermissionError):
        gh_conns.ingest_github_webhook(delivery_id="del-1", event_name="pull_request", signature_header="sha256=bad", webhook_secret="gh-secret", raw_body=raw_body)

    w1 = gh_conns.ingest_github_webhook(delivery_id="del-1", event_name="pull_request", signature_header=sig, webhook_secret="gh-secret", raw_body=raw_body)
    w2 = gh_conns.ingest_github_webhook(delivery_id="del-1", event_name="pull_request", signature_header=sig, webhook_secret="gh-secret", raw_body=raw_body)
    assert w1["accepted"] is True and w1["duplicate"] is False
    assert w2["duplicate"] is True
