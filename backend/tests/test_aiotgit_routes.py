import asyncio
import unittest
from unittest.mock import patch
from starlette.requests import Request
from fastapi import HTTPException

from app.api.v1.endpoints import repositories
from app.core.config import settings
from app.services.aiotgit_tokens import issue_workspace_token, verify_workspace_token


SECRET = "0123456789abcdef0123456789abcdef"
WORKSPACE = "ws-0123456789"


class AioGitRouteTests(unittest.TestCase):
    def setUp(self):
        self.workspace = {
            "id": WORKSPACE,
            "owner": "alice",
            "host_id": "track",
            "runtime_type": "DOCKER",
            "lifecycle_state": "ACTIVE",
            "observed_state": "ONLINE",
            "runtime_verification": "VERIFIED",
            "container_id": "a" * 64,
        }

    def token(self, scopes):
        return issue_workspace_token(
            username="alice",
            role="RESEARCHER",
            member_id="member-alice",
            workspace_id=WORKSPACE,
            node_id="track",
            scopes=scopes,
            secret=SECRET,
        )

    def request(self, token):
        request = Request({
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/repositories/cli/requests",
            "raw_path": b"/repositories/cli/requests",
            "query_string": b"",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
        })
        request.state.aiotgit_token = token
        return request

    def test_submit_forwards_verified_scoped_actor_not_client_identity(self):
        token = self.token(["repo:request:submit"])
        with patch.object(settings, "LABOPS_AIOTGIT_SIGNING_KEY", SECRET), patch.object(
            repositories, "_authorize_aiotgit_workspace", return_value=self.workspace
        ), patch.object(repositories, "_agent_call", return_value={"id": "req-0123456789ab", "state": "SUBMITTED"}) as agent:
            claims = verify_workspace_token(token, SECRET)
            response = asyncio.run(repositories.aiotgit_submit_request(
                repositories.AioGitCloneRequest(
                    repo_url="https://github.com/example/project.git",
                    ref="main",
                    idempotency_key="0123456789abcdef0123456789abcdef",
                ),
                self.request(token),
                claims,
            ))

        self.assertEqual(response["id"], "req-0123456789ab")
        call = agent.call_args
        self.assertEqual(call.args[0], "requests")
        self.assertEqual(call.kwargs["actor_token"], token)
        self.assertEqual(call.kwargs["data"]["target_workspace"], WORKSPACE)
        self.assertNotIn("actor_id", call.kwargs["data"])
        self.assertNotIn("is_admin", call.kwargs["data"])

    def test_missing_scope_is_denied_before_agent_dispatch(self):
        token = self.token(["repo:request:list"])
        with patch.object(settings, "LABOPS_AIOTGIT_SIGNING_KEY", SECRET), patch.object(
            repositories, "_authorize_aiotgit_workspace", return_value=self.workspace
        ), patch.object(repositories, "_agent_call") as agent:
            claims = verify_workspace_token(token, SECRET)
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(repositories.aiotgit_submit_request(
                    repositories.AioGitCloneRequest(
                        repo_url="https://github.com/example/project.git",
                        ref="main",
                        idempotency_key="0123456789abcdef0123456789abcdef",
                    ),
                    self.request(token),
                    claims,
                ))

        self.assertEqual(raised.exception.status_code, 403)
        agent.assert_not_called()


if __name__ == "__main__":
    unittest.main()
