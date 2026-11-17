import os
import importlib.util
import unittest
from unittest.mock import patch

if importlib.util.find_spec("fastapi") is None or importlib.util.find_spec("pydantic") is None:
    raise unittest.SkipTest("BLOCKED_BY_INFRASTRUCTURE: fastapi/pydantic are not installed in the isolated test environment")

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api.dependencies import require_labops_admin, require_user
from app.core.security import Principal, issue_token


class TestAccessApiAuthorization(unittest.TestCase):
    def setUp(self):
        self.credential = lambda token: HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    def test_access_api_fails_closed_when_session_secret_is_not_configured(self):
        with patch.dict(os.environ, {"LABOPS_SESSION_SECRET": ""}, clear=True):
            with self.assertRaises(HTTPException) as error:
                require_labops_admin(None)
        self.assertEqual(error.exception.status_code, 503)

    def test_static_admin_machine_token_does_not_impersonate_admin(self):
        with patch.dict(os.environ, {
            "LABOPS_SESSION_SECRET": "isolated-session-secret",
            "LABOPS_ADMIN_API_TOKEN": "expected-secret",
        }, clear=True):
            with self.assertRaises(HTTPException) as missing:
                require_labops_admin(None)
            with self.assertRaises(HTTPException) as invalid:
                require_labops_admin(self.credential("expected-secret"))
        self.assertEqual(missing.exception.status_code, 401)
        self.assertEqual(invalid.exception.status_code, 401)

    def test_signed_admin_session_supplies_actor_identity(self):
        with patch.dict(os.environ, {"LABOPS_SESSION_SECRET": "isolated-session-secret"}, clear=True):
            token = issue_token("verified-admin", "ADMIN")
            actor = require_labops_admin(self.credential(token))
        self.assertEqual(actor, "verified-admin")

    def test_signed_researcher_session_cannot_access_admin_api(self):
        with patch.dict(os.environ, {"LABOPS_SESSION_SECRET": "isolated-session-secret"}, clear=True):
            token = issue_token("alice", "RESEARCHER")
            with self.assertRaises(HTTPException) as denied:
                require_labops_admin(self.credential(token))
        self.assertEqual(denied.exception.status_code, 403)

    def test_active_member_can_use_researcher_api(self):
        principal = Principal(username="user_abc123", role="RESEARCHER", auth_subject="user_abc123")
        with patch("app.services.labops_service.service") as service:
            service.members = [{
                "id": "member-alice", "username": "alice", "clerk_user_id": "user_abc123",
                "status": "ACTIVE", "lifecycle_state": "ACTIVE",
            }]
            authorized = require_user(principal)
        self.assertEqual(authorized.username, "alice")
        self.assertEqual(authorized.member_id, "member-alice")

    def test_suspended_pending_or_unregistered_member_is_denied(self):
        principal = Principal(username="user_abc123", role="RESEARCHER", auth_subject="user_abc123")
        for member in (
            {"username": "alice", "clerk_user_id": "user_abc123", "status": "SUSPENDED", "lifecycle_state": "SUSPENDED"},
            {"username": "alice", "clerk_user_id": "user_abc123", "status": "PENDING_PROVISIONING", "lifecycle_state": "ACTIVE"},
        ):
            with self.subTest(member=member), patch("app.services.labops_service.service") as service:
                service.members = [member]
                with self.assertRaises(HTTPException) as denied:
                    require_user(principal)
                self.assertEqual(denied.exception.status_code, 403)
        with patch("app.services.labops_service.service") as service:
            service.members = []
            with self.assertRaises(HTTPException) as denied:
                require_user(principal)
            self.assertEqual(denied.exception.status_code, 403)

    def test_researcher_without_stable_identity_binding_is_denied(self):
        principal = Principal(username="alice", role="RESEARCHER")
        with self.assertRaises(HTTPException) as denied:
            require_user(principal)
        self.assertEqual(denied.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
