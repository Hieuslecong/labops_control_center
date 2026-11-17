from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.services.aiotgit_tokens import issue_workspace_token, verify_workspace_token


class AioGitTokenTests(unittest.TestCase):
    def setUp(self) -> None:
        self.secret = "0123456789abcdef0123456789abcdef"
        self.now = datetime(2026, 11, 17, 0, 0, tzinfo=timezone.utc)

    def issue(self, **overrides: object) -> str:
        claims = {
            "username": "alice",
            "role": "RESEARCHER",
            "member_id": "member-alice",
            "workspace_id": "ws-0123456789",
            "node_id": "track",
            "scopes": ["repo:request:submit", "repo:request:list", "repo:request:read", "repo:bundle:read"],
            "secret": self.secret,
            "now": self.now,
        }
        claims.update(overrides)
        return issue_workspace_token(**claims)  # type: ignore[arg-type]

    def test_issued_token_is_bound_to_user_workspace_node_and_scopes(self) -> None:
        token = self.issue()
        claims = verify_workspace_token(token, self.secret, now=self.now)
        self.assertEqual(claims.subject, "alice")
        self.assertEqual(claims.workspace_id, "ws-0123456789")
        self.assertEqual(claims.node_id, "track")
        self.assertIn("repo:bundle:read", claims.scopes)

    def test_tampered_or_expired_token_is_rejected(self) -> None:
        token = self.issue()
        prefix, signature = token.split(".", 1)
        with self.assertRaises(ValueError):
            verify_workspace_token(prefix + "x." + signature, self.secret, now=self.now)

        expired = self.issue(now=datetime(2026, 11, 16, 23, 0, tzinfo=timezone.utc), ttl=60)
        with self.assertRaises(ValueError):
            verify_workspace_token(expired, self.secret, now=self.now)

    def test_researcher_cannot_issue_admin_or_unbounded_token(self) -> None:
        with self.assertRaises(ValueError):
            self.issue(role="SUPERADMIN")
        with self.assertRaises(ValueError):
            self.issue(ttl=901)
        with self.assertRaises(ValueError):
            self.issue(workspace_id="../../other")


if __name__ == "__main__":
    unittest.main()
