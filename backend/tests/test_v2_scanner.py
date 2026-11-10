import os, sys, tempfile, unittest
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
        sc.runner = sc._run_trivy  # real runner
        import shutil
        if shutil.which("trivy"):
            self.skipTest("trivy installed")
        s = sc.submit(URL, SHA, "alice")
        self.assertEqual(s["state"], "FAILED")
        self.assertIn("NOT_CONFIGURED", s["error"])

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
