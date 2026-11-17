import asyncio
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException

from app.api.v1.endpoints.internal_workspaces import (
    RuntimeObservation,
    get_workspace_by_container,
    record_workspace_runtime,
)
from app.core.security import Principal
from app.services.labops_service import service
from app.services.workspace_controller import WorkspaceController


class InternalWorkspaceRegistryAPITests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="labops-workspace-registry-api-")
        self.registry = WorkspaceController(self.temp_dir.name)
        self.workspace = self.registry.request_workspace(
            owner="alice",
            host_id="track",
            ssh_public_key="ssh-ed25519 QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFB fixture",
            cpu_cores=4,
            ram_mb=8192,
            pids_limit=512,
            storage_quota_gb=20,
            idempotency_key="workspace-api-test-1",
        )
        self.container_id = "c" * 64
        self.payload = {
            "container_id": self.container_id,
            "container_name": self.workspace["container_name"],
            "labels": {
                "labops.managed": "true",
                "labops.workspace_id": self.workspace["id"],
                "labops.owner_id": "alice",
                "labops.node_id": "track",
            },
            "state": "running",
            "privileged": False,
            "docker_socket_mounted": False,
            "cpu_cores": 4,
            "ram_bytes": 8192 * 1024 * 1024,
            "pids_limit": 512,
        }
        self.principal = Principal(
            username="node-agent:track", role="RESEARCHER", kind="node_agent", node_id="track"
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_agent_observation_registers_and_reads_only_its_node_record(self):
        with patch.object(service, "workspace_controller", self.registry):
            record = asyncio.run(
                record_workspace_runtime(
                    self.workspace["id"], RuntimeObservation(**self.payload), self.principal
                )
            )
            self.assertEqual(record["container_id"], self.container_id)
            lookup = asyncio.run(
                get_workspace_by_container(self.container_id, self.principal)
            )
            self.assertEqual(lookup["owner_id"], "alice")
            self.assertEqual(lookup["node_id"], "track")

            wrong_node = Principal(
                username="node-agent:workstation", role="RESEARCHER", kind="node_agent", node_id="workstation"
            )
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(get_workspace_by_container(self.container_id, wrong_node))
            self.assertEqual(raised.exception.status_code, 404)

    def test_unsafe_runtime_claim_is_rejected_before_registry_write(self):
        unsafe_payload = dict(self.payload, privileged=True)
        with patch.object(service, "workspace_controller", self.registry):
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(
                    record_workspace_runtime(
                        self.workspace["id"], RuntimeObservation(**unsafe_payload), self.principal
                    )
                )
        self.assertEqual(raised.exception.status_code, 403)
        self.assertIsNone(self.registry.lookup_container_record(self.container_id, "track"))


if __name__ == "__main__":
    unittest.main()
