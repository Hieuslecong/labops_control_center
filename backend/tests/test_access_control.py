import importlib.util as _ilu
import unittest as _ut
if _ilu.find_spec("fastapi") is None or _ilu.find_spec("pydantic") is None:
    raise _ut.SkipTest("BLOCKED_BY_INFRASTRUCTURE: fastapi/pydantic chưa cài trong môi trường test")
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.api.v1.endpoints import admin_members, ssh_endpoints, users
from app.api.dependencies import require_labops_admin
from app.core.config import settings


class TestAccessApiAuthorization(unittest.TestCase):
    def setUp(self):
        self.credential = lambda token: HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    def test_access_api_fails_closed_when_admin_token_is_not_configured(self):
        with patch.object(settings, "LABOPS_ADMIN_API_TOKEN", ""):
            with self.assertRaises(HTTPException) as error:
                require_labops_admin(None, None)
        self.assertEqual(error.exception.status_code, 503)

    def test_access_api_rejects_missing_and_invalid_admin_token(self):
        with patch.object(settings, "LABOPS_ADMIN_API_TOKEN", "expected-secret"):
            with self.assertRaises(HTTPException) as missing:
                require_labops_admin(None, None)
            with self.assertRaises(HTTPException) as invalid:
                require_labops_admin(self.credential("wrong"), None)
            actor = require_labops_admin(self.credential("expected-secret"), "clerk-user-id")
        self.assertEqual(missing.exception.status_code, 401)
        self.assertEqual(invalid.exception.status_code, 401)
        self.assertEqual(actor, "clerk-user-id")

    def test_user_and_ssh_management_routers_are_guarded(self):
        self.assertTrue(users.router.dependencies)
        self.assertTrue(admin_members.router.dependencies)
        self.assertTrue(ssh_endpoints.router.dependencies)


if __name__ == "__main__":
    unittest.main()
