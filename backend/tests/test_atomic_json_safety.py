import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from app.services.atomic_json import atomic_write_json


class AtomicJSONSafetyTests(unittest.TestCase):
    def test_replace_failure_preserves_previous_complete_state(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "state.json"
            target.write_text('{"generation": 1}', encoding="utf-8")

            with patch("app.services.atomic_json.os.replace", side_effect=OSError("injected rename failure")):
                with self.assertRaises(OSError):
                    atomic_write_json(str(target), {"generation": 2})

            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"generation": 1})
            self.assertEqual(list(Path(root).glob("*.tmp")), [])

    def test_existing_symlink_target_is_rejected_without_touching_referent(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "state.json"
            referent = Path(root) / "outside.json"
            referent.write_text('{"keep": true}', encoding="utf-8")
            target.symlink_to(referent)

            with self.assertRaises(ValueError):
                atomic_write_json(str(target), {"keep": False})

            self.assertEqual(referent.read_text(encoding="utf-8"), '{"keep": true}')
            self.assertTrue(target.is_symlink())

    def test_concurrent_replacements_always_leave_valid_json(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "state.json"
            values = [{"writer": n, "items": list(range(100))} for n in range(12)]
            with ThreadPoolExecutor(max_workers=6) as pool:
                list(pool.map(lambda value: atomic_write_json(str(target), value), values))

            final = json.loads(target.read_text(encoding="utf-8"))
            self.assertIn(final, values)
            self.assertEqual(list(Path(root).glob("*.tmp")), [])

    def test_directory_sync_failure_is_reported_after_replace(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / "state.json"
            real_fsync = os.fsync
            calls = 0

            def fail_directory_sync(fd):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("injected directory sync failure")
                return real_fsync(fd)

            with patch("app.services.atomic_json.os.fsync", side_effect=fail_directory_sync):
                with self.assertRaises(OSError):
                    atomic_write_json(str(target), {"committed": True})

            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"committed": True})


if __name__ == "__main__":
    unittest.main()
