from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import repositories
from app.core.repository_credentials import issue_repository_token, verify_repository_token
from app.core.security import AuthError, _decode_and_verify_raw, _secret, _sign_payload, get_revocation_store, issue_token


@pytest.fixture
def broker(monkeypatch):
    node = repositories.settings.LOCAL_HOST_ID
    member = {"id": "m-1", "username": "alice", "status": "ACTIVE", "workspace_id": "ws-1"}
    record = {
        "id": "ws-1", "owner": "alice", "host_id": node, "runtime_type": "DOCKER",
        "desired_state": "ACTIVE", "container_mount_path": "/workspace",
    }
    service = SimpleNamespace(
        members=[member],
        workspace_controller=SimpleNamespace(list_workspaces=lambda **_: [record]),
        _audit=lambda *_: None,
        _save_persistent_data=lambda: None,
    )
    calls = []

    def call(path, method="GET", data=None, **kwargs):
        calls.append((path, method, data))
        if path == "requests/other":
            return {"submitted_by": "alice", "target_workspace": "managed:ws-2"}
        if path == "requests/wrong-member":
            return {"submitted_by": "bob", "target_workspace": "managed:ws-1"}
        if path == "requests/own":
            return {"submitted_by": "alice", "target_workspace": "managed:ws-1"}
        return {"success": True}

    monkeypatch.setattr(repositories, "service", service)
    monkeypatch.setattr(repositories, "_agent_call", call)
    app = FastAPI()
    app.include_router(repositories.router)
    return TestClient(app), node, calls, member


def headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_scope_expiration_audience_and_revocation(broker):
    _, node, _, _ = broker
    credential = issue_repository_token("alice", "m-1", "ws-1", node)
    token = credential["token"]
    claims = _decode_and_verify_raw(token)
    assert verify_repository_token(token, "repository:deliver").workspace_id == "ws-1"
    for changes, expected in [
        ({"scope": ["repository:status"]}, 403),
        ({"aud": "session"}, 401),
        ({"iat": 1, "exp": 2}, 401),
    ]:
        with pytest.raises(AuthError) as err:
            verify_repository_token(_sign_payload(claims | changes, _secret()), "repository:deliver")
        assert err.value.status == expected
    get_revocation_store().revoke_jti(credential["jti"], credential["expires_at"])
    with pytest.raises(AuthError) as err:
        verify_repository_token(token, "repository:deliver")
    assert err.value.status == 401


def test_workspace_bounds_and_admin_routes(broker):
    client, node, calls, member = broker
    auth = headers(issue_repository_token("alice", "m-1", "ws-1", node)["token"])
    for request in ("other", "wrong-member"):
        assert client.get(f"/requests/{request}", headers=auth).status_code == 403
        assert client.post("/deliver", headers=auth, json={"request_id": request, "target_directory": "/workspace/x"}).status_code == 403
    assert client.post("/requests/own/decision", headers=auth, json={"decision": "APPROVED", "reason": "x"}).status_code in (401, 403)
    assert client.post("/credentials/ws-1", headers=auth, json={"member_id": "m-1"}).status_code in (401, 403)
    assert client.post("/requests", headers=auth, json={"repo_url": "https://github.com/test/demo", "target_workspace": "ws-2"}).status_code == 403
    forged = headers(issue_repository_token("alice", "m-2", "ws-1", node)["token"])
    assert client.get("/requests/own", headers=forged).status_code == 403

    for destination in ("/etc/x", "/workspaces/other/x", "/workspace/../outside"):
        assert client.post("/deliver", headers=auth, json={"request_id": "own", "target_directory": destination}).status_code == 400
    assert client.post("/deliver", headers=auth, json={"request_id": "own", "target_directory": "/workspace/projects/demo"}).status_code == 200
    assert calls[-1] == ("deliver", "POST", {
        "request_id": "own", "target_directory": "projects/demo", "deliver_as_user": "alice",
        "workspace_id": "ws-1", "member_id": "m-1", "node_id": node,
    })

    session = headers(issue_token("alice", "RESEARCHER", "m-1"))
    assert client.post("/deliver", headers=session, json={"request_id": "own", "target_directory": "/workspace/projects/demo"}).status_code == 403
    assert client.post("/requests", headers=auth, json={"repo_url": "https://github.com/test/demo"}).status_code == 202
    assert calls[-1][2]["target_workspace"] == "managed:ws-1"
    member["status"] = "SUSPENDED"
    assert client.get("/requests/own", headers=auth).status_code == 403


def test_operator_reissues_repository_credential(broker):
    client, node, _, _ = broker
    old = issue_repository_token("alice", "m-1", "ws-1", node)
    admin = headers(issue_token("admin", "ADMIN"))
    response = client.post("/credentials/ws-1", headers=admin, json={"member_id": "m-1", "previous_jti": old["jti"]})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert client.get("/requests/own", headers=headers(response.json()["token"])).status_code == 200
    assert client.get("/requests/own", headers=headers(old["token"])).status_code == 401


def test_managed_provision_targets_only_the_local_agent(monkeypatch):
    import io
    import json

    from app.adapters import repository_agent_http
    from app.adapters.node_agent_adapter import NodeAgentClient

    monkeypatch.setenv("LABOPS_GATEWAY_HTTP_URL", "https://gateway.invalid")
    monkeypatch.setenv("LABOPS_GATEWAY_CONTROL_SECRET", "gateway-fixture")
    monkeypatch.setenv("LABOPS_NODE_AGENT_TOKEN", "agent-fixture")
    monkeypatch.setenv("LABOPS_LOCAL_AGENT_URL", "http://127.0.0.1:9100")

    calls = []

    def local_only(req, *, timeout):
        calls.append(req.full_url)
        assert json.loads(json.loads(req.data)["parameters"]["spec_json"])["repository_token"] == "credential-fixture"
        return io.BytesIO(b'{"success":true}')

    monkeypatch.setattr(repository_agent_http, "open_repository_agent", local_only)
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: pytest.fail("unexpected HTTP transport"))
    client = NodeAgentClient(local_host_id="workstation")
    params = {"spec_json": json.dumps({"repository_token": "credential-fixture"})}
    assert client.dispatch({"id": "workstation"}, "PROVISION_DOCKER_WORKSPACE", params=params)["success"]
    assert calls == ["http://127.0.0.1:9100/api/v1/agent/execute"]


def test_agent_url_secret_and_401_diagnostic_mapping(monkeypatch):
    import io
    import json
    import urllib.error
    from fastapi import HTTPException
    from app.core.security import Principal

    monkeypatch.delenv("LABOPS_AGENT_URL", raising=False)
    monkeypatch.setenv("LABOPS_LOCAL_AGENT_URL", "http://127.0.0.1:9100/")
    urls = repositories._get_agent_urls()
    assert urls[0] == "http://127.0.0.1:9100/api/v1/agent/repositoryguard"
    assert "http://127.0.0.1:9150/api/v1/agent/repositoryguard" in urls
    assert len(urls) == len(set(urls))

    monkeypatch.setenv("LABOPS_AGENT_URL", "http://127.0.0.1:9222/custom/")
    assert repositories._get_agent_urls() == ["http://127.0.0.1:9222/custom"]
    monkeypatch.delenv("LABOPS_AGENT_URL", raising=False)

    # Fallback to LABOPS_NODE_AGENT_TOKEN when LABOPS_AGENT_SECRET is unset
    monkeypatch.delenv("LABOPS_AGENT_SECRET", raising=False)
    monkeypatch.setenv("LABOPS_NODE_AGENT_TOKEN", "node-fallback-token")
    monkeypatch.setattr(repositories.settings, "LABOPS_AGENT_SECRET", "")
    monkeypatch.setattr(repositories.settings, "LABOPS_NODE_AGENT_TOKEN", "")
    captured = {}

    class _OkResp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"ok": true}'

    def _ok_urlopen(req, timeout=10.0):
        captured.update({k.lower(): v for k, v in req.header_items()})
        return _OkResp()

    monkeypatch.setattr("urllib.request.urlopen", _ok_urlopen)
    assert repositories._agent_call("requests", method="GET") == {"ok": True}
    assert captured["x-labops-agent-secret"] == "node-fallback-token"
    assert captured["authorization"] == "Bearer node-fallback-token"

    # Explicitly empty LABOPS_AGENT_SECRET still fails closed with 503
    monkeypatch.setenv("LABOPS_AGENT_SECRET", "")
    with pytest.raises(HTTPException) as exc_503:
        repositories._agent_call("requests", method="GET")
    assert exc_503.value.status_code == 503

    # Map upstream Go Agent 401 token/broker failure to actionable 502
    monkeypatch.setenv("LABOPS_AGENT_SECRET", "wrong-secret")

    def _agent_401(req, timeout=10.0):
        raise urllib.error.HTTPError(
            req.full_url,
            401,
            "Unauthorized",
            hdrs=None,
            fp=io.BytesIO(json.dumps({"error": "Unauthorized: invalid or missing agent token"}).encode("utf-8")),
        )

    monkeypatch.setattr("urllib.request.urlopen", _agent_401)
    with pytest.raises(HTTPException) as exc_502:
        repositories._agent_call("requests", method="GET")
    assert exc_502.value.status_code == 502
    assert "LABOPS_AGENT_SECRET" in exc_502.value.detail
    assert "/etc/labops/agent.env" in exc_502.value.detail

    # Broker headers support both LABOPS_BROKER_VERIFY_SECRET and LABOPS_REPO_BROKER_SECRET
    principal = Principal(username="alice", role="RESEARCHER", kind="user")
    monkeypatch.delenv("LABOPS_BROKER_VERIFY_SECRET", raising=False)
    monkeypatch.setenv("LABOPS_REPO_BROKER_SECRET", "repo-broker-fallback")
    signed_fallback = repositories._sign_broker_headers("GET", "requests", principal)
    assert len(signed_fallback.get("X-LabOps-Verified-Signature", "")) == 64

    monkeypatch.setenv("LABOPS_BROKER_VERIFY_SECRET", "primary-broker-secret")
    signed_primary = repositories._sign_broker_headers("GET", "requests", principal)
    assert signed_primary["X-LabOps-Verified-Signature"] != signed_fallback["X-LabOps-Verified-Signature"]
