import os
import sys
import unittest

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

os.environ.setdefault("LABOPS_SESSION_SECRET", "test-secret-workloads")
from fastapi.testclient import TestClient
from app.main import app
from app.core.security import issue_token
from app.services.labops_service import service

class TestUserWorkloadsFiltering(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.admin_token = issue_token("admin_user", "ADMIN")
        cls.member_token = issue_token("normal_member", "RESEARCHER")

    def test_active_jobs_only_contains_user_workloads(self):
        """
        Kiểm tra /api/v1/jobs/active chỉ chứa các tác vụ người dùng thực tế.
        Tuyệt đối không chứa:
        - Tiến trình hệ thống (systemd, journald, dockerd)
        - Dịch vụ desktop/session (dbus-daemon, pulseaudio)
        - IDE helpers (code-server, bootstrap-fork, copilot-runtime)
        - Idle shell (sh, bash chờ lệnh)
        - LabOps backend / uvicorn / main.py
        """
        res = self.client.get(
            "/api/v1/jobs/active",
            headers={"Authorization": f"Bearer {self.admin_token}"}
        )
        self.assertEqual(res.status_code, 200)
        jobs = res.json()

        for j in jobs:
            cmd = (j.get("cmdline") or "").lower()
            script = (j.get("script") or "").lower()
            username = j.get("username")

            # 1. Không chứa OS & Core platform daemons
            self.assertNotIn("systemd", script)
            self.assertNotIn("dbus-daemon", script)
            self.assertNotIn("pulseaudio", script)
            self.assertNotIn("applet.py", script)
            self.assertNotIn("backend/app/main", cmd)
            self.assertNotIn("labops-agent", cmd)

            # 2. Không chứa IDE helpers
            self.assertNotIn("bootstrap-fork", cmd)
            self.assertNotIn("copilot-runtime", cmd)
            self.assertNotIn("server-main.js", cmd)

            # 3. Không chứa idle shells
            if script in ["sh", "bash", "zsh", "-bash"]:
                self.assertTrue(
                    ".sh" in cmd or ".py" in cmd or "train" in cmd or "run" in cmd,
                    f"Idle shell '{script}' ({cmd}) không được xuất hiện trong active jobs"
                )

            # 4. Root workloads không phải là worker lặp lại
            self.assertTrue(j.get("isRootWorkload", True))

    def test_all_processes_retains_raw_processes(self):
        """
        Kiểm tra chế độ Chẩn đoán Nâng cao /api/v1/processes?classification=all
        vẫn lưu trữ toàn bộ các tiến trình hệ thống phục vụ quản trị.
        """
        res = self.client.get(
            "/api/v1/processes?classification=all",
            headers={"Authorization": f"Bearer {self.admin_token}"}
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertGreater(data.get("total_count", 0), 100)

    def test_prevent_killing_system_process(self):
        """
        Bảo đảm an toàn: Không thể gửi lệnh dừng hoặc kill tiến trình hệ thống.
        """
        res = self.client.post(
            "/api/v1/processes/track:host:1:17/stop",
            headers={"Authorization": f"Bearer {self.admin_token}"},
            json={"reason": "Test stop init"}
        )
        self.assertIn(res.status_code, [400, 403, 500])

if __name__ == "__main__":
    unittest.main()
