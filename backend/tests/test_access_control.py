import os
import sys
import importlib.util as _ilu
import unittest as _ut

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

if _ilu.find_spec("fastapi") is None or _ilu.find_spec("pydantic") is None:
    raise _ut.SkipTest("BLOCKED_BY_INFRASTRUCTURE: fastapi/pydantic chưa cài trong môi trường test")
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api.v1.endpoints import admin_members, ssh_endpoints, users
from app.api.dependencies import require_labops_admin
from app.core.security import issue_token


class TestAccessApiAuthorization(unittest.TestCase):
    def setUp(self):
        self.credential = lambda token: HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    def test_access_api_fails_closed_when_session_secret_is_not_configured(self):
        with patch.dict(os.environ, {"LABOPS_SESSION_SECRET": ""}, clear=False):
            with self.assertRaises(HTTPException) as error:
                require_labops_admin(None)
        self.assertEqual(error.exception.status_code, 503)

    def test_access_api_rejects_missing_invalid_and_researcher_tokens(self):
        secret = "unit-access-control-secret-0123456789"
        admin_tok = issue_token("admin-real", "ADMIN", secret=secret)
        researcher_tok = issue_token("alice", "RESEARCHER", secret=secret)
        with patch.dict(os.environ, {"LABOPS_SESSION_SECRET": secret}, clear=False):
            with self.assertRaises(HTTPException) as missing:
                require_labops_admin(None)
            with self.assertRaises(HTTPException) as invalid:
                require_labops_admin(self.credential("wrong.token"))
            with self.assertRaises(HTTPException) as non_admin:
                require_labops_admin(self.credential(researcher_tok))
            actor = require_labops_admin(self.credential(admin_tok))

        self.assertEqual(missing.exception.status_code, 401)
        self.assertEqual(invalid.exception.status_code, 401)
        self.assertEqual(non_admin.exception.status_code, 403)
        self.assertEqual(actor, "admin-real")

    def test_user_and_ssh_management_routers_are_guarded(self):
        self.assertTrue(users.router.dependencies)
        self.assertTrue(admin_members.router.dependencies)
        self.assertTrue(ssh_endpoints.router.dependencies)


if __name__ == "__main__":
    unittest.main()
