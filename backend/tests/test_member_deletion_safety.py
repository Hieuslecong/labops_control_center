"""Isolated lifecycle tests: execute only the pure service methods, no adapters."""
import ast
from datetime import datetime, timezone
import logging
from pathlib import Path
import unittest
import uuid
from typing import Any, Dict, Optional
from app.services.vpn_ownership import find_owned_member_device


def service_method(name):
    source = Path(__file__).resolve().parents[1] / 'app/services/labops_service.py'
    tree = ast.parse(source.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'LabOpsDataService')
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == name)
    method.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    namespace = {
        'Dict': Dict, 'Any': Any, 'Optional': Optional, 'uuid': uuid,
        'datetime': datetime, 'timezone': timezone, 'logger': logging.getLogger(__name__),
        'validate_linux_username': lambda _username: None,
        'find_owned_member_device': find_owned_member_device,
    }
    exec(compile(module, str(source), 'exec'), namespace)
    return namespace[name]


class WorkspaceRegistry:
    def __init__(self, records):
        self.records = records

    def list_workspaces(self, owner=None, legacy_endpoints=None):
        return [record for record in self.records if owner is None or record.get('owner') == owner]


class FakeService:
    def __init__(self, workspaces=None):
        self.members = [{
            'id': 'member-1', 'username': 'alice', 'status': 'ACTIVE',
            'ssh_status': 'VERIFIED', 'teleport_status': 'INVITED',
            'wireguard': {'status': 'ACTIVE', 'devices': [{'status': 'ACTIVE'}]},
        }]
        self.audit_logs = []
        self.workspace_controller = WorkspaceRegistry(workspaces or [])
        self.jobs_snapshot = []
        self.persist_calls = 0
        self.runtime_calls = 0

    def _save_persistent_data(self):
        self.persist_calls += 1


class MemberLifecycleSafetyTest(unittest.TestCase):
    def test_unverified_workspace_provisioning_fails_before_side_effects(self):
        with self.assertRaisesRegex(RuntimeError, "trusted runtime provisioning"):
            service_method('provision_member_ssh_access')(object(), {})

    def test_ssh_key_rotation_fails_before_revoking_existing_keys(self):
        with self.assertRaisesRegex(RuntimeError, "trusted SSH runtime lifecycle"):
            service_method('rotate_member_ssh_keypair')(object(), 'alice', actor='admin')

    def test_bulk_vpn_revoke_does_not_claim_provider_failure_as_revoked(self):
        class FailingVPNManager:
            def revoke_device(self, *_args):
                return False

        service = FakeService()
        service.vpn_manager = FailingVPNManager()
        member = {
            'id': 'member-1', 'username': 'alice',
            'wireguard': {'status': 'ACTIVE', 'devices': [{
                'device_id': 'dev-1', 'external_id': 'provider-1', 'provider': 'wg-easy',
                'status': 'ACTIVE', 'enabled': True,
            }]},
        }
        service.members[0] = member
        result = service_method('_revoke_member_wireguard')(service, member)
        self.assertEqual(result['status'], 'PARTIAL_FAILURE')
        self.assertEqual(result['devices'][0]['status'], 'ACTIVE')
        self.assertTrue(result['devices'][0]['enabled'])

    def test_suspend_preserves_data_and_marks_external_revocation_pending(self):
        service = FakeService()
        result = service_method('suspend_member')(service, 'member-1', actor='admin-test')
        self.assertEqual(result['status'], 'SUSPENDED_PENDING_EXTERNAL_REVOCATION')
        self.assertEqual(service.members[0]['status'], 'SUSPENDED')
        self.assertEqual(service.members[0]['lifecycle_state'], 'SUSPENDED')
        self.assertEqual(service.members[0]['external_access_revocation']['vpn'], 'PENDING_EXTERNAL_REVOCATION')
        self.assertEqual(service.members[0]['external_access_revocation']['ssh'], 'PENDING_EXTERNAL_REVOCATION')
        self.assertEqual(service.members[0]['wireguard']['devices'][0]['status'], 'ACTIVE')
        self.assertEqual(service.persist_calls, 1)
        self.assertEqual(service.runtime_calls, 0)

    def test_dry_run_inventories_owned_resources_and_never_deletes(self):
        service = FakeService([
            {'id': 'ws-1', 'name': 'workspace-a', 'owner': 'alice', 'lifecycle_state': 'PENDING_RETRY'},
        ])
        result = service_method('delete_member_permanently')(
            service, 'member-1', actor='admin-test', remove_files=True, dry_run=True,
        )
        self.assertFalse(result['success'])
        self.assertTrue(result['dry_run'])
        self.assertEqual(result['status'], 'DELETE_BLOCKED')
        self.assertEqual(result['inventory']['workspaces'][0]['owner'], 'alice')
        self.assertEqual(result['inventory']['user_data'], 'PRESERVED')
        self.assertEqual(result['inventory']['shared_datasets'], 'PRESERVED')
        self.assertTrue(any('backup' in blocker for blocker in result['blockers']))
        self.assertTrue(any('runtime job inventory' in blocker for blocker in result['blockers']))
        self.assertEqual(service.persist_calls, 0)
        self.assertEqual(service.runtime_calls, 0)

    def test_permanent_delete_requires_separate_confirmation_and_verified_integrations(self):
        service = FakeService()
        delete_method = service_method('delete_member_permanently')
        with self.assertRaisesRegex(ValueError, 'CONFIRMATION_REQUIRED'):
            delete_method(service, 'member-1', dry_run=False)
        with self.assertRaisesRegex(RuntimeError, 'Permanent deletion remains unavailable'):
            delete_method(service, 'member-1', dry_run=False, confirmation='DELETE alice', backup_verification_id='fake-receipt')
        self.assertEqual(len(service.members), 1)
        self.assertEqual(service.persist_calls, 0)
        self.assertEqual(service.runtime_calls, 0)


if __name__ == '__main__':
    unittest.main()
