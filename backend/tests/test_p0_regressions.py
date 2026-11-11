import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.vpn_ssh_service import SshKeyRegistry
from app.services.job_access import visible_jobs
from app.core.security import Principal


class TestP0Regressions(unittest.TestCase):
    def test_revocation_is_not_claimed_until_runtime_verifies_it(self):
        with tempfile.TemporaryDirectory() as directory:
            registry = SshKeyRegistry(directory)
            key = registry.register_key(username='alice', label='key', actor='alice', public_key='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl')
            result = registry.revoke_key(key['id'], actor='alice', is_admin=False)
            self.assertEqual(result['desired_state'], 'REVOKED')
            self.assertEqual(result['observed_state'], 'PENDING_REVOKE')
            self.assertIsNone(result['revoked_at'])

    def test_jobs_scoped_to_principal(self):
        jobs = [{'username': 'alice'}, {'username': 'bob'}, {'username': None}]
        self.assertEqual(visible_jobs(jobs, Principal('alice', 'RESEARCHER')), [jobs[0]])
        self.assertEqual(visible_jobs(jobs, Principal('admin', 'ADMIN')), jobs)
        self.assertEqual(visible_jobs(jobs, Principal('alice', 'RESEARCHER', kind='node_agent')), [])
