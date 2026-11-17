import json
import os
import unittest
from unittest.mock import patch

from app.core.security import (
    AuthError,
    principal_from_authorization,
    require_node_agent_for_host,
)


class NodeAgentBindingTests(unittest.TestCase):
    def test_per_node_token_is_bound_to_configured_node(self):
        tokens = json.dumps({"track": "track-secret", "workstation": "workstation-secret"})
        with patch.dict(os.environ, {"LABOPS_NODE_AGENT_TOKENS": tokens}, clear=True):
            principal = principal_from_authorization("Bearer track-secret")
        self.assertEqual(principal.node_id, "track")
        require_node_agent_for_host(principal, "track")

    def test_node_token_cannot_spoof_another_host_path(self):
        with patch.dict(os.environ, {"LABOPS_NODE_AGENT_TOKENS": '{"track":"track-secret"}'}, clear=True):
            principal = principal_from_authorization("Bearer track-secret")
        with self.assertRaises(AuthError) as raised:
            require_node_agent_for_host(principal, "workstation")
        self.assertEqual(raised.exception.status, 403)

    def test_legacy_unbound_machine_token_cannot_publish_heartbeat(self):
        with patch.dict(os.environ, {"LABOPS_NODE_AGENT_TOKEN": "legacy-secret"}, clear=True):
            with self.assertRaises(AuthError):
                principal_from_authorization("Bearer legacy-secret")

    def test_machine_principal_without_node_binding_is_rejected(self):
        from app.core.security import require_node_agent

        unbound = type("UnboundAgent", (), {
            "kind": "node_agent", "node_id": None,
        })()
        with self.assertRaises(AuthError):
            require_node_agent(unbound)

    def test_malformed_node_token_map_does_not_authorize_machine(self):
        with patch.dict(os.environ, {"LABOPS_NODE_AGENT_TOKENS": "not-json"}, clear=True):
            with self.assertRaises(AuthError):
                principal_from_authorization("Bearer track-secret")


if __name__ == "__main__":
    unittest.main()
