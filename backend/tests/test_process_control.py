import os
import sys
import time
import subprocess
import unittest

backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

os.environ.setdefault("LABOPS_SESSION_SECRET", "test-secret-process-ctrl")
from fastapi.testclient import TestClient
from app.main import app
from app.core.security import issue_token
from app.services.labops_service import service

class TestProcessControl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.admin_token = issue_token("admin_user", "ADMIN")
        cls.member_token = issue_token("normal_member", "RESEARCHER")

    def test_list_processes_unauthorized(self):
        """Yêu cầu không có JWT token bị từ chối 401."""
        res = self.client.get("/api/v1/processes")
        self.assertEqual(res.status_code, 401)

    def test_list_processes_success(self):
        """Admin và Member đều có thể xem danh sách tiến trình."""
        res = self.client.get(
            "/api/v1/processes",
            headers={"Authorization": f"Bearer {self.admin_token}"}
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("items", data)
        self.assertIn("total_count", data)

        # Kiểm tra không có uvicorn/python backend nào bị phân loại nhầm thành RESEARCH_JOB
        for item in data["items"]:
            cmd = (item.get("cmdline") or "").lower()
            name = (item.get("name") or "").lower()
            if any(k in cmd for k in ["uvicorn", "app.main", "labops-agent"]):
                self.assertEqual(
                    item.get("classification"),
                    "SYSTEM_PROCESS",
                    f"Tiến trình hệ thống '{name}' ({cmd}) không được phân loại là SYSTEM_PROCESS!"
                )
                self.assertTrue(
                    item.get("is_protected"),
                    f"Tiến trình hệ thống '{name}' bắt buộc phải có is_protected=True"
                )

    def test_filter_processes_by_classification(self):
        """Kiểm tra lọc tiến trình theo phân loại 4 nhóm."""
        res = self.client.get(
            "/api/v1/processes?classification=SYSTEM_PROCESS",
            headers={"Authorization": f"Bearer {self.admin_token}"}
        )
        self.assertEqual(res.status_code, 200)
        items = res.json()["items"]
        for it in items:
            self.assertEqual(it["classification"], "SYSTEM_PROCESS")

    def test_terminate_protected_process_pid1_rejected(self):
        """Từ chối dừng PID 1 (systemd) với lỗi 403 PROTECTED_PROCESS_DENIED."""
        # Ref của PID 1
        ref = "track:host:1:17"
        payload = {
            "reason": "Test cố tình dừng PID 1",
            "terminate_tree": False,
            "timeout_seconds": 3
        }
        res = self.client.post(
            f"/api/v1/processes/{ref}/stop",
            json=payload,
            headers={"Authorization": f"Bearer {self.admin_token}"}
        )
        self.assertEqual(res.status_code, 403)
        self.assertIn("PROTECTED_PROCESS_DENIED", res.text)

    def test_terminate_requires_admin_role(self):
        """Thành viên thường (RESEARCHER) không thể gửi lệnh dừng tiến trình (403)."""
        ref = "track:host:9999:12345"
        payload = {"reason": "Non-admin test", "terminate_tree": False}
        res = self.client.post(
            f"/api/v1/processes/{ref}/stop",
            json=payload,
            headers={"Authorization": f"Bearer {self.member_token}"}
        )
        self.assertEqual(res.status_code, 403)

    def test_kill_requires_force_confirmed(self):
        """Buộc dừng SIGKILL bắt buộc phải có force_confirmed: true."""
        ref = "track:host:9999:12345"
        payload = {"reason": "Test missing force", "force_confirmed": False}
        res = self.client.post(
            f"/api/v1/processes/{ref}/kill",
            json=payload,
            headers={"Authorization": f"Bearer {self.admin_token}"}
        )
        self.assertEqual(res.status_code, 400)

    def test_pid_reuse_preflight_rejection(self):
        """
        Kiểm tra cơ chế chống PID reuse:
        Nếu start_time_ticks trong ref không khớp với tiến trình thực trên host -> từ chối 409 Conflict.
        """
        # Giả sử PID 1 có start_time_ticks thật khác với 9999999999
        ref_fake_ticks = "track:host:1:9999999999"
        payload = {
            "reason": "Test chống PID reuse",
            "force_confirmed": True,
            "kill_tree": False
        }
        res = self.client.post(
            f"/api/v1/processes/{ref_fake_ticks}/kill",
            json=payload,
            headers={"Authorization": f"Bearer {self.admin_token}"}
        )
        # Bị chặn bởi PID reuse (409) hoặc Protected (403)
        self.assertIn(res.status_code, [403, 409])

    def test_spawn_and_terminate_dummy_workload(self):
        """
        Khởi tạo một tiến trình dummy con chạy lệnh sleep,
        lấy ref từ danh sách và thực hiện dừng an toàn (SIGTERM).
        Xác nhận tiến trình thoát và audit log được lưu vết.
        """
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            pid = proc.pid
            time.sleep(0.5)

            # Lấy thông tin start_time ticks từ /proc/{pid}/stat
            with open(f"/proc/{pid}/stat", "r") as f:
                parts = f.read().split(")")
                after = parts[1].split()
                ticks = int(after[19])

            ref = f"track:host:{pid}:{ticks}"

            # Gửi lệnh dừng qua API
            payload = {
                "reason": "Kiểm thử tự động dừng tiến trình worker",
                "terminate_tree": True,
                "timeout_seconds": 5
            }
            res = self.client.post(
                f"/api/v1/processes/{ref}/stop",
                json=payload,
                headers={"Authorization": f"Bearer {self.admin_token}"}
            )

            # Phải thành công hoặc kết thúc
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertIn(data.get("status"), ["SUCCESS", "TERMINATED"])

            # Chờ tiến trình thoát
            proc.poll()
            self.assertIsNotNone(proc.returncode, "Tiến trình dummy phải nhận SIGTERM và kết thúc")

            # Xác minh audit log
            audit_events = service.audit_logs
            found = any(a.get("action") == "PROCESS_SIGTERM" and ref in a.get("target_id", "") for a in audit_events)
            self.assertTrue(found, "Thao tác dừng tiến trình phải được ghi nhận trong audit_logs")

        finally:
            if proc.poll() is None:
                proc.kill()

if __name__ == "__main__":
    unittest.main()
