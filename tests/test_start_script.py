"""Exercise startup decisions without opening ports or touching services."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class StartupScriptTests(unittest.TestCase):
    def run_start(self, health_status: int) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copy(ROOT / 'start.sh', root / 'start.sh')
            (root / 'backend/.venv/bin').mkdir(parents=True)
            (root / 'bin').mkdir()
            python = root / 'backend/.venv/bin/python'
            python.write_text('''#!/bin/bash
if [[ "$2" == *"urllib.request"* ]]; then exit "$TEST_HEALTH_STATUS"; fi
if [[ "$2" == *"connect_ex"* ]]; then exit 0; fi
exit 0
''')
            python.chmod(0o755)
            for name, contents in {
                'curl': '#!/bin/sh\nexit 1\n',
                'bun': '#!/bin/sh\necho FRONTEND_STARTED\n'
            }.items():
                executable = root / 'bin' / name
                executable.write_text(contents)
                executable.chmod(0o755)
            env = os.environ.copy()
            env.update(PATH=f"{root / 'bin'}:{env['PATH']}", TEST_HEALTH_STATUS=str(health_status))
            return subprocess.run(['bash', str(root / 'start.sh')], env=env, text=True,
                                  capture_output=True, timeout=15)

    def test_reuses_healthy_backend_and_starts_frontend(self):
        result = self.run_start(0)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Backend LabOps đang hoạt động', result.stdout)
        self.assertIn('FRONTEND_STARTED', result.stdout)

    def test_occupied_port_with_unknown_service_is_not_killed(self):
        result = self.run_start(1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Cổng 8000 đang được sử dụng', result.stdout)
        self.assertNotIn('FRONTEND_STARTED', result.stdout)


if __name__ == '__main__':
    unittest.main()
