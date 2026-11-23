#!/usr/bin/env python3
"""
Reviewable & atomic maintenance repair and schema migration utility for the `testacc` split-brain state.

Modes:
  --dry-run   (default) Strictly read-only inspection; outputs plan & precheck verification.
  --apply               Requires stopped Backend (unless --skip-running-check for temp test dirs):
                        backs up DB & JSON with SHA-256 manifest, upgrades schema to Alembic 0002,
                        cleans uncommitted orphan `mem-aaf261206f13` & orphans, archives old `v2_ssh_accounts`
                        entry for `mem-0226d821478c`, migrates revocation scope, preserves all history & files,
                        records repair audit event, and verifies post-checks (auto-rolling back on failure).
  --rollback BACKUP_DIR Restores exact SQLite DB and JSON files verified against manifest.sha256.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

_BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

from app.db.atomic_json import atomic_write_json, safe_load_json
from app.db.session import inspect_schema_capabilities, upgrade_database_schema

TARGET_USERNAME = "testacc"
HISTORICAL_MEMBER_ID = "mem-0226d821478c"
HISTORICAL_DELETE_OP_ID = "op-del-25c8ea9f2a10"
ORPHAN_MEMBER_ID = "mem-aaf261206f13"
ORPHAN_ENDPOINT_ID = "ep-10d9499e"
ORPHAN_WORKSPACE_ID = "ws-8f2ef16a3f"

_BACKUP_JSON_FILES = [
    "members.json",
    "ssh_endpoints.json",
    "v2_workspaces.json",
    "v2_ssh_accounts.json",
    "v2_ssh_keys.json",
    "revoked_tokens.json",
    "audit_logs.json",
]


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _detect_running_backend_pids(data_dir: str) -> List[Dict[str, Any]]:
    """Detect running uvicorn/LabOps backend processes cleanly (read-only)."""
    matches: List[Dict[str, Any]] = []
    try:
        out = subprocess.check_output(["ps", "-eo", "pid,args"], text=True, timeout=5)
    except Exception:
        return matches
    norm_data = os.path.abspath(data_dir)
    is_default_prod = norm_data == os.path.abspath(os.path.join(_BACKEND_DIR, "data"))
    for line in out.splitlines()[1:]:
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        pid_str, cmd = parts
        if not pid_str.isdigit():
            continue
        pid = int(pid_str)
        if pid == os.getpid():
            continue
        if ("uvicorn" in cmd and "app.main:app" in cmd) or ("labops_control_center" in cmd and "uvicorn" in cmd):
            if is_default_prod or norm_data in cmd:
                matches.append({"pid": pid, "cmd": cmd})
    return matches


def inspect_state(data_dir: str, *, skip_running_check: bool = False) -> Dict[str, Any]:
    data_dir = os.path.abspath(data_dir)
    db_path = os.path.join(data_dir, "labops_v2.sqlite3")
    running = [] if skip_running_check else _detect_running_backend_pids(data_dir)

    findings: Dict[str, Any] = {
        "data_dir": data_dir,
        "db_exists": os.path.isfile(db_path),
        "running_backend_processes": running,
        "prechecks": {"ok": True, "errors": [], "warnings": []},
    }
    errors: List[str] = findings["prechecks"]["errors"]
    warnings: List[str] = findings["prechecks"]["warnings"]

    if not os.path.isfile(db_path):
        errors.append(f"Missing SQLite database: {db_path}")
        findings["prechecks"]["ok"] = False
        return findings

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        alembic_rev = None
        if "alembic_version" in tables:
            row = cur.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
            alembic_rev = row[0] if row else None

        idx_rows = [
            {"name": r["name"], "sql": r["sql"]}
            for r in cur.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name='members'").fetchall()
        ]
        hist_row = cur.execute(
            "SELECT id, username, status, provisioning_state, archived_at, deleted_at, version FROM members WHERE id = ?",
            (HISTORICAL_MEMBER_ID,),
        ).fetchone()
        orphan_in_db = cur.execute(
            "SELECT id, status FROM members WHERE id = ?",
            (ORPHAN_MEMBER_ID,),
        ).fetchone()
        testacc_rows_db = [
            dict(r)
            for r in cur.execute(
                "SELECT id, username, status, provisioning_state, deleted_at, version FROM members WHERE lower(username) = ?",
                (TARGET_USERNAME,),
            ).fetchall()
        ]
        del_op_row = cur.execute(
            "SELECT op_id, member_id, username, status, dry_run, completed_at FROM deletion_operations WHERE op_id = ?",
            (HISTORICAL_DELETE_OP_ID,),
        ).fetchone()
        pending_ops = [
            dict(r)
            for r in cur.execute(
                "SELECT op_id, member_id, status FROM deletion_operations WHERE lower(username) = ? AND dry_run = 0 AND status != 'COMPLETED'",
                (TARGET_USERNAME,),
            ).fetchall()
        ]
        active_gpus = [
            dict(r)
            for r in cur.execute(
                "SELECT id, state FROM gpu_reservations WHERE lower(owner) = ? AND state IN ('RESERVED','STARTING','RUNNING','STOP_REQUESTED')",
                (TARGET_USERNAME,),
            ).fetchall()
        ]
    finally:
        conn.close()

    from sqlalchemy import create_engine

    eng = create_engine(f"sqlite:///{db_path}", future=True)
    try:
        schema_caps = inspect_schema_capabilities(eng)
    finally:
        eng.dispose()

    members = safe_load_json(os.path.join(data_dir, "members.json"), list)
    orphan_members = [m for m in members if m.get("id") == ORPHAN_MEMBER_ID or (m.get("username") or "").lower() == TARGET_USERNAME]
    endpoints = safe_load_json(os.path.join(data_dir, "ssh_endpoints.json"), list)
    orphan_endpoints = [ep for ep in endpoints if ep.get("id") == ORPHAN_ENDPOINT_ID or ep.get("member_id") == ORPHAN_MEMBER_ID]
    ws_doc = safe_load_json(os.path.join(data_dir, "v2_workspaces.json"), lambda: {"workspaces": []})
    workspaces = ws_doc.get("workspaces", []) if isinstance(ws_doc, dict) else []
    orphan_workspaces = [
        {k: v for k, v in w.items() if "key" not in k.lower()}
        for w in workspaces
        if w.get("id") == ORPHAN_WORKSPACE_ID or w.get("owner") == TARGET_USERNAME
    ]
    accts_doc = safe_load_json(os.path.join(data_dir, "v2_ssh_accounts.json"), lambda: {"accounts": {}})
    acct_entry = (accts_doc.get("accounts") or {}).get(TARGET_USERNAME) if isinstance(accts_doc, dict) else None
    keys_doc = safe_load_json(os.path.join(data_dir, "v2_ssh_keys.json"), lambda: {"keys": []})
    key_list = keys_doc.get("keys", []) if isinstance(keys_doc, dict) else []
    testacc_keys = [
        {"id": k.get("id"), "desired_state": k.get("desired_state"), "observed_state": k.get("observed_state")}
        for k in key_list
        if (k.get("username") or "").lower() == TARGET_USERNAME
    ]
    rev_doc = safe_load_json(os.path.join(data_dir, "revoked_tokens.json"), lambda: {"jtis": {}, "subjects": {}})
    ws_disk_dir = os.path.join(data_dir, "workspaces", "workstation", TARGET_USERNAME)

    if running:
        errors.append(f"Backend process is currently running (PIDs: {[r['pid'] for r in running]}); stop it in a maintenance window first.")
    if hist_row is None:
        errors.append(f"Historical deleted record {HISTORICAL_MEMBER_ID} missing from SQLite members.")
    elif hist_row["status"] != "DELETED" or not hist_row["deleted_at"]:
        errors.append(f"Expected {HISTORICAL_MEMBER_ID} status='DELETED' with deleted_at set; got {dict(hist_row)}")
    if del_op_row is None or del_op_row["status"] != "COMPLETED":
        errors.append(f"Expected completed deletion op {HISTORICAL_DELETE_OP_ID}; got {dict(del_op_row) if del_op_row else None}")
    if pending_ops:
        errors.append(f"Found incomplete deletion operations for {TARGET_USERNAME}: {pending_ops}")
    if orphan_in_db is not None:
        errors.append(f"Unexpected DB row exists for {ORPHAN_MEMBER_ID}: {dict(orphan_in_db)}")
    if active_gpus:
        errors.append(f"Active GPU reservations found for {TARGET_USERNAME}: {active_gpus}")
    for ow in orphan_workspaces:
        if ow.get("container_id"):
            errors.append(f"Workspace {ow.get('id')} has real container_id={ow.get('container_id')}; manual reconciliation required.")
    for om in orphan_members:
        if om.get("id") != ORPHAN_MEMBER_ID:
            errors.append(f"Unexpected member in members.json for {TARGET_USERNAME}: {om.get('id')} ({om.get('status')})")
        wg_devs = ((om.get("wireguard") or {}).get("devices") or [])
        if wg_devs:
            errors.append(f"Orphan member {om.get('id')} has WireGuard devices attached ({len(wg_devs)}); inspect before cleanup.")
        if om.get("status") not in {"PENDING_PROVISIONING", "SUSPENDED", "ARCHIVE", "ARCHIVED", "DELETE_ELIGIBLE"}:
            warnings.append(f"Orphan member {om.get('id')} status is {om.get('status')}")

    findings["prechecks"]["ok"] = len(errors) == 0
    findings["schema"] = {
        "alembic_revision": alembic_rev,
        "members_indexes": idx_rows,
        **schema_caps,
    }
    findings["sqlite_testacc_members"] = testacc_rows_db
    findings["historical_delete_op"] = dict(del_op_row) if del_op_row else None
    findings["json_findings"] = {
        "orphan_members": [{"id": m.get("id"), "status": m.get("status"), "ssh_port": m.get("ssh_port")} for m in orphan_members],
        "orphan_endpoints": orphan_endpoints,
        "orphan_workspaces": orphan_workspaces,
        "v2_ssh_accounts_testacc": (
            {"member_id": acct_entry.get("member_id"), "password_status": acct_entry.get("password_status"), "activation_status": acct_entry.get("activation_status")}
            if isinstance(acct_entry, dict) else None
        ),
        "testacc_ssh_keys_count": len(testacc_keys),
        "testacc_ssh_keys_states": testacc_keys,
        "revoked_subject_testacc": (rev_doc.get("subjects") or {}).get(TARGET_USERNAME) if isinstance(rev_doc, dict) else None,
        "workspace_disk_dir_exists": os.path.isdir(ws_disk_dir),
    }
    return findings


def create_backup(data_dir: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir = os.path.join(os.path.abspath(data_dir), "backups", f"repair-testacc-{ts}")
    os.makedirs(backup_dir, mode=0o700, exist_ok=True)
    os.chmod(backup_dir, 0o700)

    manifest: Dict[str, Any] = {"created_at": ts, "data_dir": os.path.abspath(data_dir), "files": {}}

    src_db = os.path.join(data_dir, "labops_v2.sqlite3")
    dst_db = os.path.join(backup_dir, "labops_v2.sqlite3")
    src_conn = sqlite3.connect(src_db)
    dst_conn = sqlite3.connect(dst_db)
    try:
        src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()
    os.chmod(dst_db, 0o600)
    manifest["files"]["labops_v2.sqlite3"] = _sha256_file(dst_db)

    for fname in _BACKUP_JSON_FILES:
        src_p = os.path.join(data_dir, fname)
        if os.path.exists(src_p):
            dst_p = os.path.join(backup_dir, fname)
            shutil.copy2(src_p, dst_p)
            os.chmod(dst_p, 0o600)
            manifest["files"][fname] = _sha256_file(dst_p)

    atomic_write_json(os.path.join(backup_dir, "manifest.sha256.json"), manifest, mode=0o600, use_lock=False)
    return backup_dir


def rollback_from_backup(backup_dir: str, data_dir: Optional[str] = None) -> Dict[str, Any]:
    backup_dir = os.path.abspath(backup_dir)
    manifest_path = os.path.join(backup_dir, "manifest.sha256.json")
    manifest = safe_load_json(manifest_path, dict)
    target_dir = os.path.abspath(data_dir or manifest["data_dir"])
    files: Dict[str, str] = manifest.get("files") or {}
    if "labops_v2.sqlite3" not in files:
        raise RuntimeError("Invalid backup manifest: missing labops_v2.sqlite3")

    for fname, expected_sha in files.items():
        fpath = os.path.join(backup_dir, fname)
        if not os.path.isfile(fpath) or _sha256_file(fpath) != expected_sha:
            raise RuntimeError(f"Backup checksum verification failed for {fname}")

    restored: List[str] = []
    # Restore SQLite cleanly and clear stale WAL/SHM
    src_db = os.path.join(backup_dir, "labops_v2.sqlite3")
    dst_db = os.path.join(target_dir, "labops_v2.sqlite3")
    tmp_db = dst_db + ".restore.tmp"
    shutil.copy2(src_db, tmp_db)
    os.chmod(tmp_db, 0o600)
    os.replace(tmp_db, dst_db)
    for sidecar in ("-wal", "-shm"):
        sp = dst_db + sidecar
        if os.path.exists(sp):
            os.unlink(sp)
    restored.append("labops_v2.sqlite3")

    for fname in _BACKUP_JSON_FILES:
        dst_p = os.path.join(target_dir, fname)
        bak_p = os.path.join(backup_dir, fname)
        if fname in files:
            tmp_p = dst_p + ".restore.tmp"
            shutil.copy2(bak_p, tmp_p)
            os.chmod(tmp_p, 0o600)
            os.replace(tmp_p, dst_p)
            restored.append(fname)

    return {"status": "ROLLED_BACK", "backup_dir": backup_dir, "data_dir": target_dir, "restored_files": restored}


def apply_repair(data_dir: str, *, skip_running_check: bool = False) -> Dict[str, Any]:
    data_dir = os.path.abspath(data_dir)
    pre = inspect_state(data_dir, skip_running_check=skip_running_check)
    if not pre["prechecks"]["ok"]:
        raise RuntimeError(f"PRECHECK_FAILED: {'; '.join(pre['prechecks']['errors'])}")

    backup_dir = create_backup(data_dir)
    db_path = os.path.join(data_dir, "labops_v2.sqlite3")
    actions: Dict[str, Any] = {"backup_dir": backup_dir}

    try:
        # 1. Explicit version-tracked schema migration (0001 -> 0002)
        mig_res = upgrade_database_schema(f"sqlite:///{db_path}")
        actions["schema_migration"] = mig_res

        # 2. Clean ONLY uncommitted orphan member record in members.json (preserve all others)
        members_path = os.path.join(data_dir, "members.json")
        members = safe_load_json(members_path, list)
        new_members = [m for m in members if m.get("id") != ORPHAN_MEMBER_ID]
        atomic_write_json(members_path, new_members, mode=0o600)
        actions["members_removed"] = len(members) - len(new_members)

        # 3. Clean orphan SSH endpoint (ep-10d9499e / member_id=mem-aaf261206f13)
        ep_path = os.path.join(data_dir, "ssh_endpoints.json")
        if os.path.exists(ep_path):
            endpoints = safe_load_json(ep_path, list)
            new_endpoints = [
                ep for ep in endpoints
                if ep.get("id") != ORPHAN_ENDPOINT_ID and ep.get("member_id") != ORPHAN_MEMBER_ID
            ]
            atomic_write_json(ep_path, new_endpoints, mode=0o600)
            actions["endpoints_removed"] = len(endpoints) - len(new_endpoints)

        # 4. Clean unverified orphan workspace record (ws-8f2ef16a3f / no container_id)
        ws_path = os.path.join(data_dir, "v2_workspaces.json")
        if os.path.exists(ws_path):
            ws_doc = safe_load_json(ws_path, lambda: {"workspaces": [], "idempotency": {}})
            ws_list = ws_doc.get("workspaces", [])
            new_ws = [
                w for w in ws_list
                if not ((w.get("id") == ORPHAN_WORKSPACE_ID or w.get("owner") == TARGET_USERNAME) and not w.get("container_id"))
            ]
            ws_doc["workspaces"] = new_ws
            atomic_write_json(ws_path, ws_doc, mode=0o600)
            actions["workspaces_removed"] = len(ws_list) - len(new_ws)

        # 5. Archive old v2_ssh_accounts.json entry under archived_accounts[old_member_id]
        acct_path = os.path.join(data_dir, "v2_ssh_accounts.json")
        if os.path.exists(acct_path):
            acct_doc = safe_load_json(acct_path, lambda: {"accounts": {}})
            accts = acct_doc.get("accounts", {})
            archived_entry = accts.pop(TARGET_USERNAME, None)
            if isinstance(archived_entry, dict):
                arch_mid = str(archived_entry.get("member_id") or HISTORICAL_MEMBER_ID)
                acct_doc.setdefault("archived_accounts", {})[arch_mid] = archived_entry
                atomic_write_json(acct_path, acct_doc, mode=0o600)
                actions["archived_ssh_account_member_id"] = arch_mid
            else:
                actions["archived_ssh_account_member_id"] = None

        # 6. Migrate revoked_tokens.json so old member_id & legacy pre-repair tokens remain permanently revoked,
        #    without blocking a brand-new recreated member_id in the future.
        rev_path = os.path.join(data_dir, "revoked_tokens.json")
        rev_doc = safe_load_json(rev_path, lambda: {"jtis": {}, "subjects": {}, "members": {}, "active_members": {}})
        rev_doc.setdefault("jtis", {})
        rev_doc.setdefault("subjects", {})
        rev_doc.setdefault("members", {})
        rev_doc.setdefault("active_members", {})
        subj_entry = rev_doc["subjects"].get(TARGET_USERNAME)
        cutoff_epoch = int(subj_entry.get("revoked_before", 0)) if isinstance(subj_entry, dict) else 0
        rev_doc["members"][HISTORICAL_MEMBER_ID] = {
            "revoked_before": cutoff_epoch,
            "username": TARGET_USERNAME,
            "updated_at": cutoff_epoch,
            "reason": "deleted:admin",
            "permanent": True,
        }
        rev_doc["members"][ORPHAN_MEMBER_ID] = {
            "revoked_before": cutoff_epoch,
            "username": TARGET_USERNAME,
            "updated_at": cutoff_epoch,
            "reason": "orphan_repaired:admin",
            "permanent": True,
        }
        if isinstance(subj_entry, dict) and not subj_entry.get("member_id"):
            subj_entry["member_id"] = HISTORICAL_MEMBER_ID
        rev_doc["active_members"].pop(TARGET_USERNAME, None)
        atomic_write_json(rev_path, rev_doc, mode=0o600)

        # 7. Append repair audit event to SQLite + audit_logs.json (preserve full historical trail)
        now_iso = datetime.now(timezone.utc).isoformat()
        aud_id = f"aud-repair-{uuid.uuid4().hex[:8]}"
        corr_id = f"op-repair-{uuid.uuid4().hex[:8]}"
        details = (
            f"Admin repair cleaned uncommitted orphan {ORPHAN_MEMBER_ID} (and {ORPHAN_ENDPOINT_ID}, {ORPHAN_WORKSPACE_ID}) "
            f"for @{TARGET_USERNAME}, migrated DB schema to Alembic {mig_res['current_revision']}, "
            f"while preserving historical tombstone {HISTORICAL_MEMBER_ID} and deletion op {HISTORICAL_DELETE_OP_ID}."
        )
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "INSERT INTO audit_events (id, timestamp, actor, action, target_type, target_id, result, correlation_id, details) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (aud_id, now_iso, "admin", "MEMBER_STATE_REPAIRED", "MEMBER", TARGET_USERNAME, "SUCCESS", corr_id, details),
            )
            conn.commit()
        finally:
            conn.close()

        audit_path = os.path.join(data_dir, "audit_logs.json")
        if os.path.exists(audit_path):
            audits = safe_load_json(audit_path, list)
            audits.insert(0, {
                "id": aud_id,
                "actor": "admin",
                "action": "MEMBER_STATE_REPAIRED",
                "target_type": "MEMBER",
                "target_id": TARGET_USERNAME,
                "timestamp": now_iso,
                "result": "SUCCESS",
                "correlation_id": corr_id,
                "details": details,
            })
            atomic_write_json(audit_path, audits, mode=0o600)

        # 8. Post-checks (must prove historical records intact & orphan gone & schema ready)
        post = verify_post_repair(data_dir)
        actions["postchecks"] = post
        if not post["ok"]:
            raise RuntimeError(f"POSTCHECK_FAILED: {'; '.join(post['errors'])}")
        return {"status": "APPLIED", **actions}
    except Exception:
        rollback_from_backup(backup_dir, data_dir=data_dir)
        raise


def verify_post_repair(data_dir: str) -> Dict[str, Any]:
    data_dir = os.path.abspath(data_dir)
    db_path = os.path.join(data_dir, "labops_v2.sqlite3")
    errors: List[str] = []

    from sqlalchemy import create_engine

    eng = create_engine(f"sqlite:///{db_path}", future=True)
    try:
        caps = inspect_schema_capabilities(eng)
    finally:
        eng.dispose()

    if caps["has_legacy_username_unique_index"] or not caps["has_active_username_partial_index"]:
        errors.append(f"Expected active-only partial unique index on members.username, got caps={caps}")
    if not caps["revoked_tokens_has_member_id"] or not caps["has_recovery_table"]:
        errors.append(f"Expected Alembic 0002 columns/tables, got caps={caps}")

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rev_row = conn.execute("SELECT version_num FROM alembic_version LIMIT 1").fetchone()
        if not rev_row or rev_row[0] not in {"0002_member_username_reuse", "0003_labops_ai_sre_agent_v2", "0004_labops_mram_v1"}:
            errors.append(f"Unexpected alembic_version: {rev_row[0] if rev_row else None}")
        hist = conn.execute("SELECT id, status, deleted_at FROM members WHERE id = ?", (HISTORICAL_MEMBER_ID,)).fetchone()
        if not hist or hist["status"] != "DELETED" or not hist["deleted_at"]:
            errors.append("Historical deleted tombstone mem-0226d821478c missing or modified!")
        op = conn.execute("SELECT status FROM deletion_operations WHERE op_id = ?", (HISTORICAL_DELETE_OP_ID,)).fetchone()
        if not op or op["status"] != "COMPLETED":
            errors.append("Historical deletion operation op-del-25c8ea9f2a10 missing or modified!")
    finally:
        conn.close()

    members = safe_load_json(os.path.join(data_dir, "members.json"), list)
    if any(m.get("id") == ORPHAN_MEMBER_ID for m in members):
        errors.append(f"Orphan {ORPHAN_MEMBER_ID} still in members.json")

    return {"ok": len(errors) == 0, "errors": errors, "schema": caps}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="LabOps testacc split-brain repair & schema migration tool")
    parser.add_argument("--data-dir", default=os.path.join(_BACKEND_DIR, "data"))
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", help="Read-only inspection and plan output (default)")
    group.add_argument("--apply", action="store_true", help="Create backup, migrate DB, repair orphan records atomically")
    group.add_argument("--rollback", metavar="BACKUP_DIR", help="Restore exact state from verified backup dir")
    parser.add_argument("--skip-running-check", action="store_true", help="Skip live backend process check (tests only)")
    args = parser.parse_args(argv)

    if args.rollback:
        res = rollback_from_backup(args.rollback, data_dir=args.data_dir)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    if args.apply:
        res = apply_repair(args.data_dir, skip_running_check=args.skip_running_check)
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    res = inspect_state(args.data_dir, skip_running_check=args.skip_running_check)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res["prechecks"]["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
