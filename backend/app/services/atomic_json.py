"""Small, fail-closed JSON persistence helper for local application state."""

import json
import os
import secrets
import stat
from typing import Any


def atomic_write_json(path: str, value: Any, *, mode: int = 0o600) -> None:
    """Replace a JSON file atomically and durably within its existing directory.

    The parent must be a canonical directory. Existing symlinks and non-regular
    targets are rejected. The previous file remains intact when serialization,
    temporary-file writing, fsync, or replace fails. A directory-fsync failure is
    reported even though the replacement may already be visible; callers must not
    report the associated state transition as successful.
    """
    encoded = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    absolute = os.path.abspath(path)
    parent = os.path.dirname(absolute)
    name = os.path.basename(absolute)
    if not name or name in {".", ".."}:
        raise ValueError("JSON state path must name a file")
    if os.path.realpath(parent) != parent:
        raise ValueError("JSON state parent must be canonical")

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    dir_fd = os.open(parent, flags)
    temporary = f".{name}.{secrets.token_hex(12)}.tmp"
    file_fd = None
    try:
        try:
            current = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            current = None
        if current is not None and not stat.S_ISREG(current.st_mode):
            raise ValueError("existing JSON state target must be a regular file")

        file_fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            mode,
            dir_fd=dir_fd,
        )
        with os.fdopen(file_fd, "wb") as stream:
            file_fd = None
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())

        os.replace(temporary, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        os.fsync(dir_fd)
    finally:
        if file_fd is not None:
            os.close(file_fd)
        try:
            os.unlink(temporary, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
        finally:
            os.close(dir_fd)
