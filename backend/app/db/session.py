from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base


def normalize_sync_database_url(raw_url: str) -> str:
    url = (raw_url or "").strip()
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url[len("postgresql+asyncpg://"):]
    return url


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


class DatabaseManager:
    """Synchronous SQLAlchemy 2.x session manager supporting WAL SQLite and PostgreSQL."""

    def __init__(self, data_dir: str, database_url: Optional[str] = None) -> None:
        self.data_dir = os.path.abspath(data_dir)
        os.makedirs(self.data_dir, exist_ok=True)
        resolved = normalize_sync_database_url(database_url or os.getenv("DATABASE_URL", ""))
        if not resolved:
            db_file = os.path.join(self.data_dir, "labops_v2.sqlite3")
            resolved = f"sqlite:///{db_file}"
        self.database_url = resolved

        kwargs = {"future": True}
        if resolved.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 10}
        self.engine = create_engine(resolved, **kwargs)
        _attach_sqlite_pragmas(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False, class_=Session)
        self.init_schema()

    def init_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session_scope(self) -> Iterator[Session]:
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
