import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.api.v1.endpoints.repositories import _resolve_owned_workspace_id
from app.core.security import Principal
from app.services.labops_service import service


class RepositoryWorkspaceBindingTests(unittest.TestCase):
    def setUp(self):
        self.actor = Principal(username="alice", role="RESEARCHER", kind="user")
        self.verified = {
            "id": "ws-0123456789",
            "name": "ws-alice",
            "owner": "alice",
            "host_id": "track",
            "runtime_type": "DOCKER",
            "lifecycle_state": "ACTIVE",
            "observed_state": "ONLINE",
            "runtime_verification": "VERIFIED",
            "container_id": "a" * 64,
        }

    def test_only_runtime_verified_owned_workspace_is_selected(self):
        pending = dict(self.verified)
        pending.update({"lifecycle_state": "PENDING_RETRY", "observed_state": "NOT_VERIFIED"})
        with patch.object(service.workspace_controller, "list_workspaces", return_value=[pending, self.verified]):
            self.assertEqual(_resolve_owned_workspace_id(self.actor, None), self.verified["id"])

    def test_explicit_pending_workspace_is_not_eligible(self):
        pending = dict(self.verified)
        pending.update({"lifecycle_state": "PENDING_RETRY", "observed_state": "NOT_VERIFIED"})
        with patch.object(service.workspace_controller, "list_workspaces", return_value=[pending]):
            with self.assertRaises(HTTPException) as raised:
                _resolve_owned_workspace_id(self.actor, pending["id"])
        self.assertEqual(raised.exception.status_code, 409)

    def test_unowned_workspace_is_denied(self):
        other = dict(self.verified, owner="bob")
        with patch.object(service.workspace_controller, "list_workspaces", return_value=[other]):
            with self.assertRaises(HTTPException) as raised:
                _resolve_owned_workspace_id(self.actor, other["id"])
        self.assertEqual(raised.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
