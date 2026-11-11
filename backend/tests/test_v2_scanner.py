import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from app.services.security_scanner import SecurityScanner, evaluate_policy

SHA = "a" * 40
URL = "https://github.com/example/repo"


class TestScanner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "scans.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_missing_trivy_never_passes(self):
        sc = SecurityScanner(self.path, async_mode=False)
        sc.runner = sc._run_trivy
        with patch("app.services.security_scanner.shutil.which", return_value=None):
            s = sc.submit(URL, SHA, "alice")
        self.assertEqual(s["state"], "FAILED")
        self.assertIn("NOT_CONFIGURED", s["error"])

    def test_real_trivy_binary_executes_on_aiotlab_rack(self):
        if not shutil.which("trivy"):
            self.skipTest("trivy binary not installed")
        versions = SecurityScanner.scanner_versions()
        self.assertIsNotNone(versions["scanner_version"])

        # 1. Quét thư mục sạch bằng binary /usr/bin/trivy thật
        with tempfile.TemporaryDirectory() as clean_dir:
            with open(os.path.join(clean_dir, "app.py"), "w", encoding="utf-8") as f:
                f.write("def add(a, b):\n    return a + b\n")
            out_clean = subprocess.check_output(
                ["trivy", "fs", "--quiet", "--format", "json", "--scanners", "secret,misconfig", clean_dir],
                text=True,
                timeout=60,
            )
            verdict_clean = evaluate_policy(json.loads(out_clean))
            self.assertEqual(verdict_clean["verdict"], "PASSED")

        # 2. Quét thư mục chứa khóa bí mật rò rỉ bằng binary /usr/bin/trivy thật -> REJECTED
        with tempfile.TemporaryDirectory() as leak_dir:
            hdr = "-----BEGIN " + "RSA PRIVATE KEY-----"
            ftr = "-----END " + "RSA PRIVATE KEY-----"
            body = base64.b64encode(b"x" * 64).decode("ascii")
            with open(os.path.join(leak_dir, "id_rsa"), "w", encoding="utf-8") as f:
                f.write(f"{hdr}\n{body}\n{ftr}\n")
            out_leak = subprocess.check_output(
                ["trivy", "fs", "--quiet", "--format", "json", "--scanners", "secret,misconfig", leak_dir],
                text=True,
                timeout=60,
            )
            verdict_leak = evaluate_policy(json.loads(out_leak))
            self.assertEqual(verdict_leak["verdict"], "REJECTED")
            self.assertGreaterEqual(verdict_leak["secrets"], 1)

    def test_invalid_sha_rejected(self):
        sc = SecurityScanner(self.path, async_mode=False)
        with self.assertRaises(ValueError):
            sc.submit(URL, "abc", "alice")

    def test_invalid_url_rejected(self):
        sc = SecurityScanner(self.path, async_mode=False)
        with self.assertRaises(ValueError):
            sc.submit("file:///etc/passwd", SHA, "alice")

    def test_policy_secret_rejects(self):
        r = evaluate_policy({"Results": [{"Secrets": [{"RuleID": "aws"}]}]})
        self.assertEqual(r["verdict"], "REJECTED")

    def test_policy_high_requires_review(self):
        r = evaluate_policy({"Results": [{"Vulnerabilities": [{"Severity": "HIGH"}]}]})
        self.assertEqual(r["verdict"], "REVIEW_REQUIRED")

    def test_clean_passes_and_cache(self):
        calls = []
        sc = SecurityScanner(self.path, runner=lambda u, c: calls.append(1) or {"Results": []}, async_mode=False)
        a = sc.submit(URL, SHA, "alice")
        self.assertEqual(a["state"], "PASSED")
        b = sc.submit(URL, SHA, "bob")
        self.assertTrue(b.get("cache_hit"))
        self.assertEqual(len(calls), 1)

    def test_cannot_approve_failed(self):
        sc = SecurityScanner(self.path, runner=lambda u, c: (_ for _ in ()).throw(RuntimeError("x")), async_mode=False)
        s = sc.submit(URL, SHA, "alice")
        self.assertEqual(s["state"], "FAILED")
        with self.assertRaises(ValueError):
            sc.decide(s["id"], "APPROVED", "admin")


if __name__ == "__main__":
    unittest.main()
