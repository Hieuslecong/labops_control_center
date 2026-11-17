import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.services.gpu_scheduler import GpuScheduler, SchedulerError, observed_scheduler_state


class GpuSchedulerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="labops-scheduler-test-")
        self.addCleanup(self.tmp.cleanup)
        self.state_path = str(Path(self.tmp.name) / "scheduler.json")
        self.telemetry = [{
            "uuid": "GPU-test", "server_id": "test-node", "active_processes": [],
            "live": True, "freshness": "ONLINE",
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }]
        self.scheduler = GpuScheduler(
            self.state_path,
            lambda: self.telemetry,
        )

    def test_idempotency_key_is_scoped_to_authenticated_owner_and_actor(self):
        first = self.scheduler.reserve(
            gpu_uuid="GPU-test", owner="alice", actor="alice",
            job_name="experiment", idempotency_key="shared-client-key",
        )

        with self.assertRaises(SchedulerError) as raised:
            self.scheduler.reserve(
                gpu_uuid="GPU-test", owner="bob", actor="bob",
                job_name="other", idempotency_key="shared-client-key",
            )

        self.assertEqual(raised.exception.code, "IDEMPOTENCY_KEY_REUSED")
        self.assertEqual(self.scheduler.list_reservations()[0]["id"], first["id"])
        self.assertEqual(self.scheduler.list_reservations()[0]["owner"], "alice")

    def test_reusing_idempotency_key_for_different_request_is_rejected(self):
        self.scheduler.reserve(
            gpu_uuid="GPU-test", owner="alice", actor="alice",
            job_name="experiment", idempotency_key="same-request-key",
        )
        with self.assertRaises(SchedulerError) as raised:
            self.scheduler.reserve(
                gpu_uuid="GPU-test", owner="alice", actor="alice",
                job_name="different-job", idempotency_key="same-request-key",
            )
        self.assertEqual(raised.exception.code, "IDEMPOTENCY_KEY_REUSED")

    def test_state_write_syncs_before_replacing_persisted_file(self):
        previous = {"reservations": [], "idempotency": {}}
        Path(self.state_path).write_text(json.dumps(previous), encoding="utf-8")
        with patch("app.services.gpu_scheduler.os.fsync", wraps=__import__("os").fsync) as sync:
            self.scheduler._save({"reservations": [{"id": "new"}], "idempotency": {}})
        self.assertGreaterEqual(sync.call_count, 2)
        self.assertEqual(json.loads(Path(self.state_path).read_text(encoding="utf-8"))["reservations"][0]["id"], "new")

    def test_corrupt_reservation_records_fail_closed(self):
        Path(self.state_path).write_text(
            json.dumps({"reservations": [{"gpu_uuid": "GPU-test", "state": "RUNNING"}], "idempotency": {}}),
            encoding="utf-8",
        )
        with self.assertRaises(SchedulerError) as raised:
            self.scheduler.list_reservations()
        self.assertEqual(raised.exception.code, "STATE_INVALID")

    def test_stale_gpu_telemetry_cannot_be_reserved(self):
        self.telemetry[0]["collected_at"] = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        with self.assertRaises(SchedulerError) as raised:
            self.scheduler.reserve(gpu_uuid="GPU-test", owner="alice", actor="alice", job_name="stale")
        self.assertEqual(raised.exception.code, "GPU_TELEMETRY_STALE")

    def test_unverified_or_non_live_gpu_telemetry_cannot_be_reserved(self):
        self.telemetry[0]["live"] = False
        with self.assertRaises(SchedulerError) as raised:
            self.scheduler.reserve(gpu_uuid="GPU-test", owner="alice", actor="alice", job_name="fake")
        self.assertEqual(raised.exception.code, "GPU_TELEMETRY_STALE")

    def test_duplicate_gpu_uuid_observation_is_rejected(self):
        self.telemetry.append(dict(self.telemetry[0], id="duplicate-index", index=1))
        with self.assertRaises(SchedulerError) as raised:
            self.scheduler.reserve(gpu_uuid="GPU-test", owner="alice", actor="alice", job_name="duplicate")
        self.assertEqual(raised.exception.code, "GPU_NOT_OBSERVED")

    def test_concurrent_reservations_cannot_allocate_one_gpu_twice(self):
        def reserve(index):
            try:
                return self.scheduler.reserve(
                    gpu_uuid="GPU-test", owner=f"user-{index}", actor=f"user-{index}",
                    job_name=f"job-{index}", idempotency_key=f"request-{index}",
                )["state"]
            except SchedulerError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(reserve, range(8)))
        self.assertEqual(results.count("RESERVED"), 1)
        self.assertEqual(len(self.scheduler.list_reservations()), 1)

    def test_gpu_is_not_reported_free_if_reservation_state_is_unavailable_or_telemetry_stale(self):
        self.assertEqual(
            observed_scheduler_state(self.telemetry[0], [], reservation_state_available=False),
            "UNKNOWN",
        )
        stale_gpu = dict(self.telemetry[0], live=False)
        self.assertEqual(
            observed_scheduler_state(stale_gpu, [], reservation_state_available=True),
            "UNKNOWN",
        )
        busy_gpu = dict(self.telemetry[0], active_processes=[{"pid": 7}])
        self.assertEqual(
            observed_scheduler_state(busy_gpu, [], reservation_state_available=True),
            "BUSY_EXTERNAL",
        )

    def test_runtime_confirmation_is_bound_to_reservation_node(self):
        reservation = self.scheduler.reserve(
            gpu_uuid="GPU-test", owner="alice", actor="alice", job_name="experiment",
        )

        with self.assertRaises(SchedulerError) as raised:
            self.scheduler.confirm_from_agent(reservation["id"], "RUNNING", 321, node_id="other-node")

        self.assertEqual(raised.exception.code, "NODE_MISMATCH")
        self.assertEqual(self.scheduler.list_reservations()[0]["state"], "RESERVED")

    def test_runtime_confirmation_requires_positive_pid(self):
        reservation = self.scheduler.reserve(
            gpu_uuid="GPU-test", owner="alice", actor="alice", job_name="experiment",
        )

        with self.assertRaises(SchedulerError) as raised:
            self.scheduler.confirm_from_agent(reservation["id"], "RUNNING", 0, node_id="test-node")

        self.assertEqual(raised.exception.code, "INVALID_RUNTIME_PID")
        self.assertEqual(self.scheduler.list_reservations()[0]["state"], "RESERVED")

    def test_runtime_observation_cannot_clear_stop_request(self):
        reservation = self.scheduler.reserve(
            gpu_uuid="GPU-test", owner="alice", actor="alice", job_name="experiment",
        )
        self.scheduler.confirm_from_agent(reservation["id"], "RUNNING", 320, node_id="test-node")
        self.scheduler.request_stop(reservation["id"], actor="alice", is_admin=False)

        observed = self.scheduler.confirm_from_agent(
            reservation["id"], "RUNNING", 321, node_id="test-node",
        )

        self.assertEqual(observed["state"], "STOP_REQUESTED")
        self.assertTrue(observed["runtime_verified"])

    def test_ended_reservation_rejects_late_agent_update(self):
        reservation = self.scheduler.reserve(
            gpu_uuid="GPU-test", owner="alice", actor="alice", job_name="experiment",
        )
        self.scheduler.confirm_from_agent(reservation["id"], "RUNNING", 320, node_id="test-node")
        self.scheduler.request_stop(reservation["id"], actor="alice", is_admin=False)
        self.scheduler.confirm_from_agent(reservation["id"], "STOPPED", None, node_id="test-node")

        with self.assertRaises(SchedulerError) as raised:
            self.scheduler.confirm_from_agent(reservation["id"], "RUNNING", 321, node_id="test-node")

        self.assertEqual(raised.exception.code, "INVALID_STATE_TRANSITION")


if __name__ == "__main__":
    unittest.main()
