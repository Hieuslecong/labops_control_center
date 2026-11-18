from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base

logger = logging.getLogger("labops.db")


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
        default_sqlite = f"sqlite:///{os.path.join(self.data_dir, 'labops_v2.sqlite3')}"
        resolved = normalize_sync_database_url(database_url or os.getenv("DATABASE_URL", ""))
        if not resolved:
            resolved = default_sqlite
        self.database_url = resolved

        kwargs = {"future": True}
        if resolved.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 10}

        try:
            self.engine = create_engine(resolved, **kwargs)
            _attach_sqlite_pragmas(self.engine)
            self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False, class_=Session)
            self.init_schema()
        except Exception as e:
            if not resolved.startswith("sqlite"):
                logger.warning(
                    "Không thể khởi tạo cơ sở dữ liệu cấu hình (%s): %s. Tự động chuyển về SQLite WAL an toàn: %s",
                    resolved.split("@")[-1] if "@" in resolved else resolved,
                    e,
                    default_sqlite,
                )
                self.database_url = default_sqlite
                fallback_kwargs = {"future": True, "connect_args": {"check_same_thread": False, "timeout": 10}}
                self.engine = create_engine(default_sqlite, **fallback_kwargs)
                _attach_sqlite_pragmas(self.engine)
                self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False, class_=Session)
                self.init_schema()
            else:
                raise

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
