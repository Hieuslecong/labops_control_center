"""Keep pytest imports and global service instances inside disposable state."""

from __future__ import annotations

import os
import tempfile


_test_state = tempfile.TemporaryDirectory(prefix="labops-pytest-state-")
_test_users = os.path.join(_test_state.name, "users")
_test_storage = os.path.join(_test_state.name, "storage")

# Do not inherit Rack credentials, runtime endpoints, or explicit live-test opt-ins.
for _name in tuple(os.environ):
    if _name.startswith(("LABOPS_", "WG_EASY_", "WIREGUARD_")):
        os.environ.pop(_name, None)
for _name in ("DATABASE_URL", "LOCAL_HOST_ID"):
    os.environ.pop(_name, None)

os.environ["LABOPS_STATE_DIR"] = _test_state.name
os.environ["LABOPS_USER_DATA_DIR"] = _test_users
os.environ["LABOPS_STORAGE_DATA_PATH"] = _test_storage


def pytest_sessionfinish(session, exitstatus):
    _test_state.cleanup()
