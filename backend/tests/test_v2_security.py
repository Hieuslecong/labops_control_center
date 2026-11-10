import os
import sys
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core import security
from app.core.security import (AuthError, issue_token, principal_from_authorization,
                               require_node_agent, require_owner_or_admin, require_role, verify_token)

SECRET = "unit-secret"


class TestV2Security(unittest.TestCase):
    def setUp(self):
        os.environ["LABOPS_SESSION_SECRET"] = SECRET
        os.environ["LABOPS_NODE_AGENT_TOKEN"] = "agent-token-xyz"

    def test_missing_header_401(self):
        with self.assertRaises(AuthError) as c:
            principal_from_authorization(None)
        self.assertEqual(c.exception.status, 401)

    def test_tampered_role_rejected(self):
        tok = issue_token("alice", "RESEARCHER")
        p64, sig = tok.split(".")
        forged = security._b64e(security._b64d(p64).replace(b"RESEARCHER", b"ADMIN")) + "." + sig
        with self.assertRaises(AuthError):
            verify_token(forged)

    def test_expired_token_rejected(self):
        tok = issue_token("alice", "RESEARCHER", ttl=-5)
        with self.assertRaises(AuthError):
            verify_token(tok)

    def test_no_secret_fails_closed(self):
        os.environ["LABOPS_SESSION_SECRET"] = ""
        with self.assertRaises(AuthError) as c:
            verify_token("a.b")
        self.assertEqual(c.exception.status, 503)

    def test_researcher_cannot_admin(self):
        p = verify_token(issue_token("alice", "RESEARCHER"))
        with self.assertRaises(AuthError) as c:
            require_role(p, "ADMIN")
        self.assertEqual(c.exception.status, 403)

    def test_ownership(self):
        p = verify_token(issue_token("alice", "RESEARCHER"))
        require_owner_or_admin(p, "alice")
        with self.assertRaises(AuthError):
            require_owner_or_admin(p, "bob")

    def test_agent_token_is_not_user(self):
        p = principal_from_authorization("Bearer agent-token-xyz")
        require_node_agent(p)
        with self.assertRaises(AuthError):
            require_role(p, "ADMIN")
        with self.assertRaises(AuthError):
            require_owner_or_admin(p, "node-agent")

    def test_user_token_is_not_agent(self):
        p = verify_token(issue_token("root", "ADMIN"))
        with self.assertRaises(AuthError):
            require_node_agent(p)


if __name__ == "__main__":
    unittest.main()

