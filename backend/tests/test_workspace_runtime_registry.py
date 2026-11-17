import tempfile
import unittest

from app.services.workspace_controller import WorkspaceController, WorkspaceSecurityError


class WorkspaceRuntimeRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="labops-workspace-registry-")
        self.registry = WorkspaceController(self.temp_dir.name)
        self.workspace = self.registry.request_workspace(
            owner="alice",
            host_id="track",
            ssh_public_key="ssh-ed25519 QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFB fixture",
            cpu_cores=4,
            ram_mb=8192,
            pids_limit=512,
            storage_quota_gb=20,
            idempotency_key="workspace-test-1",
        )
        self.container_id = "a" * 64
        self.labels = {
            "labops.managed": "true",
            "labops.workspace_id": self.workspace["id"],
            "labops.owner_id": "alice",
            "labops.node_id": "track",
        }

    def tearDown(self):
        self.temp_dir.cleanup()

    def observation(self, **overrides):
        result = {
            "container_id": self.container_id,
            "container_name": self.workspace["container_name"],
            "labels": dict(self.labels),
            "state": "running",
            "network_mode": "bridge",
            "privileged": False,
            "docker_socket_mounted": False,
            "cpu_cores": 4,
            "ram_bytes": 8192 * 1024 * 1024,
            "pids_limit": 512,
        }
        result.update(overrides)
        return result

    def test_authenticated_node_observation_registers_canonical_runtime(self):
        record = self.registry.record_runtime_observation(
            self.workspace["id"], "track", self.observation()
        )
        self.assertEqual(record["container_id"], self.container_id)
        self.assertEqual(record["owner_id"], "alice")
        self.assertEqual(record["node_id"], "track")
        self.assertEqual(record["state"], "ACTIVE")
        self.assertEqual(
            self.registry.lookup_container_record(self.container_id, "track"), record
        )

    def test_wrong_node_labels_and_unsafe_runtime_are_rejected_without_registration(self):
        cases = [
            ("workstation", self.observation(), "node"),
            ("track", self.observation(labels={**self.labels, "labops.owner_id": "mallory"}), "labels"),
            ("track", self.observation(privileged=True), "privileged"),
            ("track", self.observation(docker_socket_mounted=True), "socket"),
            ("track", self.observation(state="exited"), "state"),
            ("track", self.observation(cpu_cores=5), "CPU"),
        ]
        for node_id, observation, _label in cases:
            with self.subTest(case=_label), self.assertRaises(WorkspaceSecurityError):
                self.registry.record_runtime_observation(
                    self.workspace["id"], node_id, observation
                )
        self.assertIsNone(
            self.registry.lookup_container_record(self.container_id, "track")
        )

    def test_container_replacement_requires_explicit_recovery(self):
        self.registry.record_runtime_observation(
            self.workspace["id"], "track", self.observation()
        )
        replacement_id = "b" * 64
        replacement = self.observation(container_id=replacement_id)
        with self.assertRaises(WorkspaceSecurityError) as raised:
            self.registry.record_runtime_observation(
                self.workspace["id"], "track", replacement
            )
        self.assertEqual(raised.exception.code, "CONTAINER_REPLACEMENT")
        self.assertIsNone(
            self.registry.lookup_container_record(replacement_id, "track")
        )

    def test_noncanonical_id_and_wrong_owner_are_rejected(self):
        invalid = self.observation(container_id="../" + "a" * 61)
        with self.assertRaises(WorkspaceSecurityError):
            self.registry.record_runtime_observation(
                self.workspace["id"], "track", invalid
            )
        wrong_owner = self.observation(
            labels={**self.labels, "labops.owner_id": "bob"}
        )
        with self.assertRaises(WorkspaceSecurityError):
            self.registry.record_runtime_observation(
                self.workspace["id"], "track", wrong_owner
            )

    def test_repository_enforced_workspace_requires_and_records_network_none(self):
        enforced = WorkspaceController(self.temp_dir.name + "/enforced")
        workspace = enforced.request_workspace(
            owner="alice",
            host_id="track",
            ssh_public_key="ssh-ed25519 QUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFB fixture",
            cpu_cores=4,
            ram_mb=8192,
            pids_limit=512,
            storage_quota_gb=20,
            repository_policy_mode="REPOSITORY_ENFORCED",
        )
        labels = {
            "labops.managed": "true",
            "labops.workspace_id": workspace["id"],
            "labops.owner_id": "alice",
            "labops.node_id": "track",
        }
        with self.assertRaises(WorkspaceSecurityError) as raised:
            enforced.record_runtime_observation(
                workspace["id"], "track", self.observation(
                    labels=labels,
                    container_name=workspace["container_name"],
                    network_mode="bridge",
                )
            )
        self.assertEqual(raised.exception.code, "WORKSPACE_EGRESS_NOT_ISOLATED")
        self.assertEqual(workspace["lifecycle_state"], "PENDING_RETRY")

        record = enforced.record_runtime_observation(
            workspace["id"], "track", self.observation(
                labels=labels,
                container_name=workspace["container_name"],
                network_mode="none",
            )
        )
        self.assertEqual(record["repository_policy_mode"], "REPOSITORY_ENFORCED")
        self.assertEqual(record["observed_network_mode"], "none")
        self.assertEqual(record["egress_verification"], "VERIFIED_NO_NETWORK")


if __name__ == "__main__":
    unittest.main()
