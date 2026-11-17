import ast
import unittest
from pathlib import Path

from app.core.security import AuthError, Principal, authorize_fallback_request
from app.services.resource_access import find_visible_workspace, visible_storage_allocations


BACKEND = Path(__file__).resolve().parents[1]


def dependency_names(node):
    return {
        item.args[0].id
        for item in ast.walk(node)
        if isinstance(item, ast.Call)
        and isinstance(item.func, ast.Name)
        and item.func.id == "Depends"
        and item.args
        and isinstance(item.args[0], ast.Name)
    }


class ApiAuthorizationCoverageTests(unittest.TestCase):
    def test_fallback_server_restricts_host_process_details_to_admins(self):
        researcher = Principal(username="alice", role="RESEARCHER")
        with self.assertRaises(AuthError) as raised:
            authorize_fallback_request("GET", "/api/v1/servers/track/processes", researcher)
        self.assertEqual(raised.exception.status, 403)

    def test_fallback_server_does_not_expose_monitoring_health_publicly(self):
        researcher = Principal(username="alice", role="RESEARCHER")
        with self.assertRaises(AuthError) as raised:
            authorize_fallback_request("GET", "/api/v1/monitoring/health", researcher)
        self.assertEqual(raised.exception.status, 403)

    def test_fallback_server_rejects_machine_identity_on_user_routes(self):
        agent = Principal(username="node-agent:track", role="RESEARCHER", kind="node_agent", node_id="track")
        with self.assertRaises(AuthError) as raised:
            authorize_fallback_request("GET", "/api/v1/servers", agent)
        self.assertEqual(raised.exception.status, 403)

    def test_fallback_server_allows_researcher_server_inventory(self):
        researcher = Principal(username="user_abc123", role="RESEARCHER", auth_subject="user_abc123")
        resolved = authorize_fallback_request("GET", "/api/v1/servers", researcher, [{
            "id": "member-alice", "username": "alice", "clerk_user_id": "user_abc123",
            "status": "ACTIVE", "lifecycle_state": "ACTIVE",
        }])
        self.assertEqual(resolved.username, "alice")
        self.assertEqual(resolved.member_id, "member-alice")

    def test_fallback_server_rejects_suspended_member(self):
        researcher = Principal(username="user_abc123", role="RESEARCHER", auth_subject="user_abc123")
        with self.assertRaises(AuthError) as raised:
            authorize_fallback_request("GET", "/api/v1/servers", researcher, [{
                "id": "member-alice", "username": "alice", "clerk_user_id": "user_abc123",
                "status": "SUSPENDED", "lifecycle_state": "SUSPENDED",
            }])
        self.assertEqual(raised.exception.status, 403)

    def test_sensitive_endpoint_modules_require_a_principal(self):
        for filename in (
            "dashboard.py", "monitoring.py", "audit.py", "events.py",
            "storage_pools.py", "servers.py", "workspaces.py",
        ):
            with self.subTest(filename=filename):
                module = ast.parse((BACKEND / "app/api/v1/endpoints" / filename).read_text(encoding="utf-8"))
                router_deps = set()
                for node in module.body:
                    if isinstance(node, ast.Assign) and any(
                        isinstance(target, ast.Name) and target.id == "router" for target in node.targets
                    ):
                        router_deps.update(dependency_names(node.value))
                routes = [
                    node for node in module.body
                    if isinstance(node, ast.AsyncFunctionDef)
                    and any(
                        isinstance(dec, ast.Call)
                        and isinstance(dec.func, ast.Attribute)
                        and isinstance(dec.func.value, ast.Name)
                        and dec.func.value.id == "router"
                        for dec in node.decorator_list
                    )
                ]
                self.assertTrue(routes, "expected at least one route")
                for route in routes:
                    defaults = ast.Module(body=list(route.args.defaults) + [d for d in route.args.kw_defaults if d], type_ignores=[])
                    names = router_deps | dependency_names(defaults)
                    self.assertTrue(names & {"require_user", "require_admin", "require_labops_admin", "require_agent"}, route.name)

    def test_storage_allocations_are_scoped_to_member_identity(self):
        allocations = [
            {"id": "a", "member_id": "m-alice", "amount_gb": 10},
            {"id": "b", "member_id": "m-bob", "amount_gb": 20},
        ]
        members = [{"id": "m-alice", "username": "alice"}, {"id": "m-bob", "username": "bob"}]
        alice = Principal(username="alice", role="RESEARCHER")
        self.assertEqual([item["id"] for item in visible_storage_allocations(allocations, members, alice)], ["a"])

    def test_workspace_lookup_rejects_cross_user_and_ambiguous_ids(self):
        workspaces = [
            {"id": "ws-a", "name": "alice-workspace", "owner": "alice"},
            {"id": "ws-b", "name": "bob-workspace", "owner": "bob"},
        ]
        alice = Principal(username="alice", role="RESEARCHER")
        self.assertEqual(find_visible_workspace(workspaces, "ws-a", alice), workspaces[0])
        self.assertIsNone(find_visible_workspace(workspaces, "ws-b", alice))
        workspaces.append({"id": "ws-a", "name": "duplicate", "owner": "alice"})
        self.assertIsNone(find_visible_workspace(workspaces, "ws-a", alice))

    def test_frontend_role_authority_is_server_managed_and_defaults_to_researcher(self):
        repo_root = BACKEND.parent
        bff = (repo_root / "src/app/api/labops/_lib.ts").read_text(encoding="utf-8")
        session = (repo_root / "src/app/api/labops/session/route.ts").read_text(encoding="utf-8")
        hook = (repo_root / "src/hooks/use-auth-session.ts").read_text(encoding="utf-8")
        self.assertNotIn("sessionClaims?.metadata", bff)
        self.assertNotIn("sessionClaims?.metadata", session)
        self.assertIn("session?.role === 'ADMIN' ? 'ADMIN' : 'RESEARCHER'", hook)
        self.assertNotIn("return 'ADMIN'; // Default", hook)

    def test_researcher_ssh_instructions_require_verified_runtime_endpoint(self):
        portal = (BACKEND.parent / "src/features/researcher/components/researcher-portal-view.tsx").read_text(encoding="utf-8")
        self.assertNotIn("192.168.167.250", portal)
        self.assertNotIn("vpn_bind_ip ||", portal)
        self.assertIn("ssh_readiness === 'VERIFIED'", portal)
        self.assertIn("ssh_host", portal)

    def test_frontend_uses_distinct_bounded_web_and_backend_session_claims(self):
        repo_root = BACKEND.parent
        bff = (repo_root / "src/app/api/labops/_lib.ts").read_text(encoding="utf-8")
        login = (repo_root / "src/app/api/auth/login/route.ts").read_text(encoding="utf-8")
        verifier = (repo_root / "src/lib/auth-guard.ts").read_text(encoding="utf-8")
        self.assertIn("iss: 'labops-bff'", bff)
        self.assertIn("aud: 'labops-api'", bff)
        self.assertIn("auth_subject: authSubject || null", bff)
        self.assertIn("username = userId", bff)
        self.assertIn("authSubject = userId", bff)
        self.assertIn("iss: 'labops-local'", login)
        self.assertIn("aud: 'labops-web'", login)
        self.assertIn("data.iss !== 'labops-local'", verifier)
        self.assertIn("data.aud !== 'labops-web'", verifier)

    def test_database_configuration_has_no_built_in_credentials(self):
        config = (BACKEND / "app/core/config.py").read_text(encoding="utf-8")
        self.assertNotIn("labops_secret", config)
        self.assertIn('DATABASE_URL: str = os.getenv("DATABASE_URL", "")', config)


if __name__ == "__main__":
    unittest.main()
