#!/usr/bin/env python3
"""LabOps V2 non-destructive database backup and checksum-verified restore tool."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from sqlalchemy import DateTime, MetaData, Table, create_engine, inspect, select, text

REPO_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.db.models import Base  # noqa: E402
from app.db.session import normalize_sync_database_url  # noqa: E402


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _serialize_cell(val: Any) -> Any:
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        return {"__labops_type__": "datetime", "value": val.isoformat()}
    return val


def _deserialize_row(row: Dict[str, Any], table: Table) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for col in table.columns:
        if col.name not in row:
            continue
        val = row[col.name]
        if isinstance(val, dict) and val.get("__labops_type__") == "datetime":
            val = datetime.fromisoformat(str(val["value"]))
        elif isinstance(val, str) and isinstance(col.type, DateTime):
            val = datetime.fromisoformat(val)
        out[col.name] = val
    return out


def _sqlite_path_from_url(url: str) -> str | None:
    if url.startswith("sqlite:///"):
        raw = url[len("sqlite:///"):]
        if raw == ":memory:":
            return None
        return raw
    return None


def backup_database(db_url: str, out_dir: Path) -> Dict[str, Any]:
    normalized = normalize_sync_database_url(db_url)
    out_dir = out_dir.resolve()
    tables_dir = out_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    engine = create_engine(normalized, future=True)
    artifacts: Dict[str, Dict[str, Any]] = {}
    table_counts: Dict[str, int] = {}
    alembic_version: str | None = None

    try:
        insp = inspect(engine)
        existing_names = set(insp.get_table_names())
        metadata = MetaData()
        metadata.reflect(bind=engine)

        ordered_tables: List[Table] = [
            metadata.tables[t.name]
            for t in Base.metadata.sorted_tables
            if t.name in metadata.tables
        ]
        seen = {t.name for t in ordered_tables}
        for name in sorted(existing_names - seen):
            ordered_tables.append(metadata.tables[name])

        with engine.connect() as conn:
            if "alembic_version" in existing_names:
                ver_row = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).fetchone()
                if ver_row:
                    alembic_version = str(ver_row[0])

            for table in ordered_tables:
                rows = conn.execute(select(table)).mappings().all()
                table_counts[table.name] = len(rows)
                rel_path = f"tables/{table.name}.jsonl"
                target_file = out_dir / rel_path
                with target_file.open("w", encoding="utf-8") as f:
                    for r in rows:
                        serialized = {k: _serialize_cell(v) for k, v in dict(r).items()}
                        f.write(json.dumps(serialized, ensure_ascii=False, sort_keys=True) + "\n")
                artifacts[rel_path] = {
                    "sha256": _sha256_file(target_file),
                    "bytes": target_file.stat().st_size,
                }

        sqlite_src = _sqlite_path_from_url(normalized)
        if sqlite_src and os.path.exists(sqlite_src):
            snap_rel = "sqlite_snapshot.db"
            snap_path = out_dir / snap_rel
            src_conn = sqlite3.connect(sqlite_src)
            dst_conn = sqlite3.connect(str(snap_path))
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
                src_conn.close()
            artifacts[snap_rel] = {
                "sha256": _sha256_file(snap_path),
                "bytes": snap_path.stat().st_size,
            }
    finally:
        engine.dispose()

    manifest = {
        "format": "labops-db-backup-v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_dialect": engine.dialect.name,
        "alembic_version": alembic_version,
        "table_counts": table_counts,
        "artifacts": artifacts,
    }
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return manifest


def restore_database(
    bundle_dir: Path, target_db_url: str, *, allow_overwrite: bool = False
) -> Dict[str, Any]:
    bundle_dir = bundle_dir.resolve()
    manifest_path = bundle_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing manifest.json in backup bundle: {bundle_dir}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    artifacts = manifest.get("artifacts") or {}
    if not isinstance(artifacts, dict) or not artifacts:
        raise ValueError("Invalid backup manifest: missing artifacts map")

    # 1. Verify EVERY artifact SHA-256 BEFORE modifying any destination DB
    for rel_path, meta in artifacts.items():
        art_file = (bundle_dir / rel_path).resolve()
        if not str(art_file).startswith(str(bundle_dir) + os.sep) or not art_file.exists():
            raise FileNotFoundError(f"Missing backup artifact: {rel_path}")
        expected_hash = str(meta.get("sha256") or "")
        actual_hash = _sha256_file(art_file)
        if not expected_hash or actual_hash != expected_hash:
            raise ValueError(
                f"Checksum mismatch for '{rel_path}': expected {expected_hash}, got {actual_hash}"
            )

    normalized = normalize_sync_database_url(target_db_url)
    sqlite_dst = _sqlite_path_from_url(normalized)
    if sqlite_dst:
        Path(sqlite_dst).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(normalized, future=True)
    try:
        insp = inspect(engine)
        existing_tables = set(insp.get_table_names())
        non_empty_tables: List[str] = []
        if existing_tables:
            with engine.connect() as conn:
                for tbl in existing_tables:
                    if tbl == "alembic_version":
                        continue
                    cnt = conn.execute(text(f'SELECT COUNT(*) FROM "{tbl}"')).scalar() or 0
                    if int(cnt) > 0:
                        non_empty_tables.append(f"{tbl}({cnt})")
        if non_empty_tables and not allow_overwrite:
            raise RuntimeError(
                f"Refusing to overwrite non-empty database without --allow-overwrite: {', '.join(non_empty_tables)}"
            )

        Base.metadata.create_all(engine)

        # Ensure alembic_version table exists if it was captured
        table_counts = manifest.get("table_counts") or {}
        if "alembic_version" in table_counts and "alembic_version" not in set(inspect(engine).get_table_names()):
            with engine.begin() as conn:
                conn.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))

        metadata = MetaData()
        metadata.reflect(bind=engine)
        ordered_names = [t.name for t in Base.metadata.sorted_tables if t.name in table_counts]
        for extra in sorted(set(table_counts.keys()) - set(ordered_names)):
            ordered_names.append(extra)

        with engine.begin() as conn:
            if allow_overwrite:
                for name in reversed(ordered_names):
                    if name in metadata.tables:
                        conn.execute(metadata.tables[name].delete())

            for name in ordered_names:
                rel_path = f"tables/{name}.jsonl"
                art_file = bundle_dir / rel_path
                if not art_file.exists() or name not in metadata.tables:
                    continue
                table = metadata.tables[name]
                batch: List[Dict[str, Any]] = []
                with art_file.open("r", encoding="utf-8") as f:
                    for raw_line in f:
                        line = raw_line.strip()
                        if not line:
                            continue
                        batch.append(_deserialize_row(json.loads(line), table))
                if batch:
                    conn.execute(table.insert(), batch)

        # Post-restore verification
        restored_counts: Dict[str, int] = {}
        with engine.connect() as conn:
            for name, expected_count in table_counts.items():
                actual_count = int(conn.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar() or 0)
                restored_counts[name] = actual_count
                if actual_count != int(expected_count):
                    raise RuntimeError(
                        f"Post-restore count mismatch on '{name}': expected {expected_count}, got {actual_count}"
                    )
    finally:
        engine.dispose()

    return {
        "status": "RESTORED_VERIFIED",
        "bundle_dir": str(bundle_dir),
        "target_dialect": engine.dialect.name,
        "alembic_version": manifest.get("alembic_version"),
        "restored_counts": restored_counts,
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="LabOps V2 database backup and verified restore")
    sub = parser.add_subparsers(dest="command", required=True)

    p_bak = sub.add_parser("backup", help="Create checksummed backup bundle")
    p_bak.add_argument("--db-url", required=True, help="SQLAlchemy source database URL")
    p_bak.add_argument("--out-dir", required=True, help="Output backup directory")

    p_res = sub.add_parser("restore", help="Verify checksums and restore database bundle")
    p_res.add_argument("--bundle-dir", required=True, help="Backup bundle directory")
    p_res.add_argument("--target-db-url", required=True, help="SQLAlchemy target database URL")
    p_res.add_argument("--allow-overwrite", action="store_true", help="Allow overwriting non-empty target DB")

    args = parser.parse_args(argv)
    try:
        if args.command == "backup":
            res = backup_database(args.db_url, Path(args.out_dir))
            print(json.dumps(res, indent=2, ensure_ascii=False))
            return 0
        if args.command == "restore":
            res = restore_database(
                Path(args.bundle_dir),
                args.target_db_url,
                allow_overwrite=bool(args.allow_overwrite),
            )
            print(json.dumps(res, indent=2, ensure_ascii=False))
            return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
