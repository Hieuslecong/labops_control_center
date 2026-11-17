import os
import json
import hmac
import hashlib
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core import security
from app.core.security import (AuthError, issue_token, principal_from_authorization,
                               require_node_agent, require_owner_or_admin, require_role, verify_token)

SECRET = "unit-secret"


def sign_payload(payload):
    encoded = json.dumps(payload, separators=(",", ":")).encode()
    signature = hmac.new(SECRET.encode(), encoded, hashlib.sha256).digest()
    return f"{security._b64e(encoded)}.{security._b64e(signature)}"


class TestV2Security(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {
            "LABOPS_SESSION_SECRET": SECRET,
            "LABOPS_NODE_AGENT_TOKENS": '{"track":"agent-token-xyz"}',
        }, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

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
        now = int(time.time())
        tok = sign_payload({
            "sub": "alice", "role": "RESEARCHER", "member_id": None,
            "iat": now - 10, "exp": now - 5, "iss": "labops-bff", "aud": "labops-api",
        })
        with self.assertRaises(AuthError):
            verify_token(tok)

    def test_token_requires_bff_issuer_and_api_audience(self):
        now = int(time.time())
        token = sign_payload({
            "sub": "alice", "role": "RESEARCHER", "member_id": None,
            "iat": now, "exp": now + 60, "iss": "labops-local", "aud": "labops-web",
        })
        with self.assertRaises(AuthError):
            verify_token(token)

    def test_token_lifetime_is_bounded_and_issued_at_is_required(self):
        now = int(time.time())
        token = sign_payload({
            "sub": "alice", "role": "RESEARCHER", "member_id": None,
            "iat": now, "exp": now + 901, "iss": "labops-bff", "aud": "labops-api",
        })
        with self.assertRaises(AuthError):
            verify_token(token)

        missing_iat = {
            "sub": "alice", "role": "RESEARCHER", "member_id": None,
            "exp": now + 60, "iss": "labops-bff", "aud": "labops-api",
        }
        token = sign_payload(missing_iat)
        with self.assertRaises(AuthError):
            verify_token(token)

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

    def test_api_token_carries_and_validates_stable_external_subject(self):
        principal = verify_token(issue_token(
            "user_abc123", "RESEARCHER", auth_subject="user_abc123",
        ))
        self.assertEqual(principal.auth_subject, "user_abc123")
        with self.assertRaises(AuthError):
            issue_token("alice", "RESEARCHER", auth_subject="../alice")

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

