import ast
import unittest
from pathlib import Path


class StartupSafetyTests(unittest.TestCase):
    def test_global_service_state_directory_can_be_isolated_for_tests(self):
        source = Path(__file__).parents[1].joinpath("app", "services", "labops_service.py").read_text(encoding="utf-8")
        self.assertIn('os.getenv("LABOPS_STATE_DIR"', source)

    def test_uvicorn_reload_is_not_unconditionally_enabled(self):
        source = Path(__file__).parents[1].joinpath("app", "main.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        run_server = next(
            node for node in module.body
            if isinstance(node, ast.FunctionDef) and node.name == "run_server"
        )
        calls = [
            node for node in ast.walk(run_server)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "uvicorn"
            and node.func.attr == "run"
        ]
        self.assertEqual(len(calls), 1)
        reload_value = next(
            (kw.value for kw in calls[0].keywords if kw.arg == "reload"), None
        )
        self.assertIsInstance(reload_value, ast.Name)
        self.assertEqual(reload_value.id, "reload_enabled")
        self.assertIn('os.getenv("LABOPS_DEV_RELOAD", "").lower() == "true"', source)
        self.assertIn('os.getenv("LABOPS_ENVIRONMENT", "production").lower() != "production"', source)

    def test_local_startup_requires_explicit_opt_in_and_never_downloads_bun(self):
        source = Path(__file__).parents[2].joinpath("start.sh").read_text(encoding="utf-8")
        self.assertIn('LABOPS_ALLOW_LOCAL_START', source)
        self.assertIn('LABOPS_LOAD_LOCAL_ENV', source)
        self.assertIn('LABOPS_BACKEND_PORT:-18000', source)
        self.assertIn('LABOPS_FRONTEND_PORT:-3012', source)
        self.assertIn('LABOPS_MODE=demo', source)
        self.assertIn('LABOPS_USER_DATA_DIR="${REPO_ROOT}/backend/data/dev-users"', source)
        self.assertIn('không kết nối hoặc dừng tiến trình hiện có', source)
        self.assertNotIn('urlretrieve(', source)
        self.assertNotIn('./bin/labops-agent &', source)
        self.assertNotIn('curl -s http://127.0.0.1:9100/health', source)

    def test_runtime_install_and_recovery_scripts_require_explicit_apply(self):
        root = Path(__file__).parents[2]
        for name in ("install-agent.sh", "install-gateway.sh", "upgrade-agent.sh", "rollback-agent.sh", "enroll-node.sh"):
            with self.subTest(script=name):
                source = (root / "deploy" / name).read_text(encoding="utf-8")
                self.assertIn('"--apply"', source)


if __name__ == "__main__":
    unittest.main()
