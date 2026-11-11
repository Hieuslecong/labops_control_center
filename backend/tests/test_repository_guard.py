import unittest
from unittest.mock import patch, MagicMock
from app.services.security_scanner import evaluate_policy, SecurityScanner


class TestRepositoryGuardScanner(unittest.TestCase):
    def test_clean_scan_full_coverage_passes(self):
        report = {
            "Results": [
                {
                    "Target": "package.json",
                    "Vulnerabilities": [{"Severity": "LOW"}],
                    "Secrets": [],
                    "Misconfigurations": []
                }
            ],
            "coverage": {
                "vulnerability_scan": "COMPLETED",
                "secret_scan": "COMPLETED",
                "misconfiguration_scan": "COMPLETED",
                "overall_coverage": "POLICY_COMPLETE"
            }
        }
        res = evaluate_policy(report)
        self.assertEqual(res["verdict"], "PASSED")
        self.assertEqual(res["secrets"], 0)
        self.assertEqual(res["vulnerabilities"]["CRITICAL"], 0)

    def test_incomplete_coverage_fails_closed_to_review_required(self):
        """Bảo đảm khi scan coverage bị thiếu hoặc fallback, KHÔNG BAO GIỜ tự ý PASSED"""
        report = {
            "Results": [
                {
                    "Target": "package.json",
                    "Vulnerabilities": [],
                    "Secrets": [],
                    "Misconfigurations": []
                }
            ],
            "coverage": {
                "vulnerability_scan": "FAILED (exit 1)",
                "secret_scan": "COMPLETED",
                "misconfiguration_scan": "COMPLETED",
                "overall_coverage": "INCOMPLETE"
            }
        }
        res = evaluate_policy(report)
        self.assertEqual(res["verdict"], "REVIEW_REQUIRED")
        self.assertTrue(any("Scan coverage incomplete" in r for r in res["policy_reasons"]))

    def test_critical_vulnerability_rejects(self):
        report = {
            "Results": [
                {
                    "Target": "requirements.txt",
                    "Vulnerabilities": [{"Severity": "CRITICAL"}],
                    "Secrets": [],
                    "Misconfigurations": []
                }
            ],
            "coverage": {
                "overall_coverage": "POLICY_COMPLETE"
            }
        }
        res = evaluate_policy(report)
        self.assertEqual(res["verdict"], "REJECTED")

    def test_leaked_secret_rejects(self):
        report = {
            "Results": [
                {
                    "Target": ".env",
                    "Vulnerabilities": [],
                    "Secrets": [{"RuleID": "aws-secret", "Match": "AKIAIOSFODNN7EXAMPLE"}],
                    "Misconfigurations": []
                }
            ],
            "coverage": {
                "overall_coverage": "POLICY_COMPLETE"
            }
        }
        res = evaluate_policy(report)
        self.assertEqual(res["verdict"], "REJECTED")
        self.assertEqual(res["secrets"], 1)

    def test_high_vulnerability_requires_review(self):
        report = {
            "Results": [
                {
                    "Target": "Gemfile.lock",
                    "Vulnerabilities": [{"Severity": "HIGH"}],
                    "Secrets": [],
                    "Misconfigurations": []
                }
            ],
            "coverage": {
                "overall_coverage": "POLICY_COMPLETE"
            }
        }
        res = evaluate_policy(report)
        self.assertEqual(res["verdict"], "REVIEW_REQUIRED")


if __name__ == "__main__":
    unittest.main()
