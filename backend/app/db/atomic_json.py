from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from typing import Any, Callable, Iterator


class PersistenceCorruptionError(RuntimeError):
    """Raised when an existing state file cannot be parsed (fail-closed)."""


@contextmanager
def file_lock(path: str) -> Iterator[None]:
    lock_path = path + ".lock"
    os.makedirs(os.path.dirname(os.path.abspath(lock_path)), exist_ok=True)
    with open(lock_path, "a+", encoding="utf-8") as lf:
        fcntl.flock(lf, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lf, fcntl.LOCK_UN)


def atomic_write_json_unlocked(path: str, data: Any, *, mode: int = 0o600) -> None:
    abs_path = os.path.abspath(path)
    parent = os.path.dirname(abs_path)
    os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=f".{os.path.basename(abs_path)}.", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, abs_path)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_json(path: str, data: Any, *, mode: int = 0o600, use_lock: bool = True) -> None:
    abs_path = os.path.abspath(path)
    if not use_lock:
        atomic_write_json_unlocked(abs_path, data, mode=mode)
        return
    with file_lock(abs_path):
        atomic_write_json_unlocked(abs_path, data, mode=mode)


def safe_load_json(path: str, default_factory: Callable[[], Any]) -> Any:
    abs_path = os.path.abspath(path)
    if not os.path.exists(abs_path):
        return default_factory()
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        raise PersistenceCorruptionError(
            f"Corrupt or unreadable persistence file '{abs_path}': {exc}"
        ) from exc
