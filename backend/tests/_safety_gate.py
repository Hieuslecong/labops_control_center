"""Opt-in gate for tests that inspect or start host runtime resources."""

from __future__ import annotations

import os
import sys
import unittest


def require_live_test_opt_in(test_name: str, variable: str = "LABOPS_ENABLE_LIVE_TESTS") -> None:
    """Skip a host-facing test module unless its operator explicitly opts in."""
    if os.environ.get(variable) == "1":
        return

    reason = f"{test_name} is host-facing; set {variable}=1 only in an isolated test environment"
    if "pytest" in sys.modules:
        sys.modules["pytest"].skip(reason, allow_module_level=True)
    raise unittest.SkipTest(reason)
