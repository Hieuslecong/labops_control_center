import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class EgressScriptSafetyTests(unittest.TestCase):
    def test_legacy_pilot_scripts_are_non_mutating_guards(self):
        command = re.compile(r"^\s*(?:sudo\s+)?(?:iptables|ip6tables|nft|nmcli|ip|wg|docker|systemctl)\s")
        for name in ("pilot_workspace_egress.sh", "pilot_workspace_rollback.sh"):
            with self.subTest(script=name):
                script = (ROOT / "scripts" / name).read_text(encoding="utf-8")
                executable_lines = [line for line in script.splitlines() if line.strip() and not line.lstrip().startswith("#")]
                self.assertIn("exit 2", executable_lines)
                self.assertFalse(any(command.search(line) for line in executable_lines))
                self.assertIn("No network or service operation was performed.", script)


if __name__ == "__main__":
    unittest.main()
