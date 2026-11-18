"""Session-wide pytest isolation so backend tests NEVER touch live backend/data/*.json."""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile

_SESSION_DATA_DIR = tempfile.mkdtemp(prefix="labops-pytest-data-")
_SESSION_USERS_DIR = os.path.join(_SESSION_DATA_DIR, "users")
os.makedirs(_SESSION_USERS_DIR, exist_ok=True)
atexit.register(lambda: shutil.rmtree(_SESSION_DATA_DIR, ignore_errors=True))

os.environ["LABOPS_DATA_DIR"] = _SESSION_DATA_DIR
os.environ.setdefault("LABOPS_USER_DATA_DIR", _SESSION_USERS_DIR)
os.environ.setdefault(
    "LABOPS_SESSION_SECRET",
    "pytest-session-secret-0123456789abcdef0123456789abcdef",
)
