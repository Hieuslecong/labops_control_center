from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base

logger = logging.getLogger("labops.db")


def normalize_sync_database_url(raw_url: str) -> str:
    url = (raw_url or "").strip()
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url[len("postgresql+asyncpg://"):]
    return url


def resolve_sqlite_abs_path(database_url: str) -> Optional[str]:
    """Resolve a SQLite SQLAlchemy URL (`sqlite:///...` or `sqlite:////...`) to an absolute file path."""
    url = normalize_sync_database_url(database_url)
    if not url.startswith("sqlite:///"):
        return None
    raw_path = url[len("sqlite:///"):]
    if raw_path == ":memory:" or raw_path.startswith("file::memory:"):
        return None
    if raw_path.startswith("file:"):
        raw_path = raw_path[len("file:"):].split("?", 1)[0]
    return os.path.abspath(raw_path)


def _attach_sqlite_pragmas(engine: Engine) -> None:
    if not str(engine.url).startswith("sqlite"):
        return

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=5000")
        finally:
            cursor.close()


def inspect_schema_capabilities(engine: Engine) -> Dict[str, Any]:
    """Inspect schema state read-only without running implicit DDL migrations."""
    insp = inspect(engine)
    tables = set(insp.get_table_names())

    revoked_has_member_id = False
    revoked_has_permanent = False
    if "revoked_tokens" in tables:
        cols = {c["name"] for c in insp.get_columns("revoked_tokens")}
        revoked_has_member_id = "member_id" in cols
        revoked_has_permanent = "permanent" in cols

    has_active_partial_idx = False
    has_legacy_unconditional_unique = False
    if "members" in tables:
        for idx in insp.get_indexes("members"):
            cols = list(idx.get("column_names") or [])
            name = str(idx.get("name") or "")
            is_unique = bool(idx.get("unique"))
            if cols == ["username"] and is_unique:
                if name == "uq_members_active_username":
                    has_active_partial_idx = True
                else:
                    has_legacy_unconditional_unique = True
        for uc in insp.get_unique_constraints("members"):
            if list(uc.get("column_names") or []) == ["username"]:
                has_legacy_unconditional_unique = True
        if str(engine.url).startswith("sqlite") and not has_legacy_unconditional_unique:
            # Also check SQLite autoindexes not reported by get_unique_constraints
            with engine.connect() as conn:
                idx_rows = conn.exec_driver_sql("PRAGMA index_list('members')").fetchall()
                for row in idx_rows:
                    idx_name = str(row[1])
                    is_unique = bool(row[2])
                    if is_unique and idx_name != "uq_members_active_username":
                        info_rows = conn.exec_driver_sql(f'PRAGMA index_info("{idx_name}")').fetchall()
                        idx_cols = [str(ir[2]) for ir in info_rows]
                        if idx_cols == ["username"]:
                            has_legacy_unconditional_unique = True
                            break

    return {
        "revoked_tokens_has_member_id": revoked_has_member_id,
        "revoked_tokens_has_permanent": revoked_has_permanent,
        "has_active_username_partial_index": has_active_partial_idx,
        "has_legacy_username_unique_index": has_legacy_unconditional_unique or not has_active_partial_idx,
        "has_recovery_table": "persistence_recovery_records" in tables,
    }


def upgrade_database_schema(database_url: str) -> Dict[str, Any]:
    """Explicitly run version-tracked Alembic migrations up to head for SQLite/PostgreSQL."""
    from alembic import command as alembic_command
    from alembic.config import Config as AlembicConfig
    from alembic.runtime.migration import MigrationContext

    sync_url = normalize_sync_database_url(database_url)
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    cfg = AlembicConfig(os.path.join(backend_dir, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend_dir, "alembic"))
    cfg.set_main_option("sqlalchemy.url", sync_url)

    engine = create_engine(sync_url, future=True)
    try:
        _attach_sqlite_pragmas(engine)
        before_caps = inspect_schema_capabilities(engine)
        with engine.connect() as conn:
            insp = inspect(conn)
            existing_tables = set(insp.get_table_names())
            current_rev = MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()

    if "members" in existing_tables and not current_rev:
        # Legacy DB created via Base.metadata.create_all without alembic_version -> stamp 0001 first
        alembic_command.stamp(cfg, "0001_labops_v2_hardened")

    alembic_command.upgrade(cfg, "head")

    engine_after = create_engine(sync_url, future=True)
    try:
        _attach_sqlite_pragmas(engine_after)
        after_caps = inspect_schema_capabilities(engine_after)
        with engine_after.connect() as conn:
            new_rev = MigrationContext.configure(conn).get_current_revision()
    finally:
        engine_after.dispose()

    return {
        "previous_revision": current_rev,
        "current_revision": new_rev,
        "before": before_caps,
        "after": after_caps,
    }


class DatabaseManager:
    """Synchronous SQLAlchemy 2.x session manager supporting WAL SQLite and PostgreSQL."""

    def __init__(self, data_dir: str, database_url: Optional[str] = None) -> None:
        self.data_dir = os.path.abspath(data_dir)
        os.makedirs(self.data_dir, exist_ok=True)
        default_sqlite = f"sqlite:///{os.path.join(self.data_dir, 'labops_v2.sqlite3')}"
        resolved = normalize_sync_database_url(database_url or os.getenv("DATABASE_URL", ""))
        if not resolved:
            resolved = default_sqlite
        self.database_url = resolved

        kwargs: Dict[str, Any] = {"future": True}
        if resolved.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 10}

        self.engine = create_engine(resolved, **kwargs)
        self._metrics_lock = threading.Lock()
        self._sqlite_lock_errors = 0
        self.revoked_tokens_has_member_id = False
        self.revoked_tokens_has_permanent = False
        self.has_legacy_username_unique_index = False
        self.has_recovery_table = False
        try:
            _attach_sqlite_pragmas(self.engine)
            self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False, class_=Session)
            self.init_schema()
            self.refresh_schema_capabilities()
        except Exception:
            self.engine.dispose()
            raise

    def refresh_schema_capabilities(self) -> Dict[str, Any]:
        caps = inspect_schema_capabilities(self.engine)
        self.revoked_tokens_has_member_id = bool(caps["revoked_tokens_has_member_id"])
        self.revoked_tokens_has_permanent = bool(caps["revoked_tokens_has_permanent"])
        self.has_legacy_username_unique_index = bool(caps["has_legacy_username_unique_index"])
        self.has_recovery_table = bool(caps["has_recovery_table"])
        return caps

    def init_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    def get_db_metrics(self) -> Dict[str, Any]:
        """Return non-sensitive database footprint, dialect, journal mode, and SQLite lock error metrics."""
        dialect = self.engine.dialect.name
        abs_path = resolve_sqlite_abs_path(self.database_url) if dialect == "sqlite" else None
        db_bytes = 0
        wal_bytes = 0
        shm_bytes = 0
        journal_mode = dialect
        if abs_path:
            if os.path.exists(abs_path):
                db_bytes = os.path.getsize(abs_path)
            wal_path = f"{abs_path}-wal"
            shm_path = f"{abs_path}-shm"
            if os.path.exists(wal_path):
                wal_bytes = os.path.getsize(wal_path)
            if os.path.exists(shm_path):
                shm_bytes = os.path.getsize(shm_path)
            try:
                with self.engine.connect() as conn:
                    row = conn.exec_driver_sql("PRAGMA journal_mode").fetchone()
                    if row and row[0]:
                        journal_mode = str(row[0])
            except Exception:
                pass
        with self._metrics_lock:
            lock_errs = int(self._sqlite_lock_errors)
        return {
            "dialect": dialect,
            "sqlite_path_abs": abs_path,
            "db_size_bytes": db_bytes,
            "wal_size_bytes": wal_bytes,
            "shm_size_bytes": shm_bytes,
            "total_bytes": db_bytes + wal_bytes + shm_bytes,
            "sqlite_lock_errors": lock_errs,
            "journal_mode": journal_mode,
        }

    @contextmanager
    def session_scope(self) -> Iterator[Session]:
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except OperationalError as exc:
            msg = str(exc).lower()
            if "locked" in msg or "busy" in msg:
                with self._metrics_lock:
                    self._sqlite_lock_errors += 1
            session.rollback()
            raise
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
