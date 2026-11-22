import pytest

from app.db import session


def test_explicit_database_failure_does_not_create_sqlite(monkeypatch, tmp_path):
    attempted = []

    def create_engine(url, **kwargs):
        attempted.append(url)
        raise RuntimeError("fixture database unavailable")

    monkeypatch.setattr(session, "create_engine", create_engine)
    with pytest.raises(RuntimeError, match="fixture database unavailable"):
        session.DatabaseManager(str(tmp_path), "postgresql://fixture@localhost/labops")
    assert len(attempted) == 1
    assert attempted[0].startswith("postgresql")
    assert not list(tmp_path.iterdir())
