#!/usr/bin/env python3
"""Safe runtime SSHD config migration tool for existing LabOps Docker Workspaces."""
from __future__ import annotations

import argparse
import difflib
import json
import os
import stat
import subprocess
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

OLD_DIRECTIVE = "AllowTcpForwarding no\n"
NEW_DIRECTIVES = (
    "AllowTcpForwarding local\n"
    "PermitOpen 127.0.0.1:* localhost:*\n"
    "AllowStreamLocalForwarding no\n"
    "GatewayPorts no\n"
)

DEFAULT_REGISTRY_PATH = "/var/lib/labops/workspace_registry.json"
DEFAULT_RUNTIME_ROOT = "/var/lib/labops-agent/runtime"


def migrate_sshd_config(content: str) -> Tuple[str, str]:
    """Migrate container sshd_config safely and idempotently for VS Code Remote-SSH."""
    if (
        "AllowTcpForwarding local\n" in content
        and "PermitOpen 127.0.0.1:* localhost:*\n" in content
        and "AllowStreamLocalForwarding no\n" in content
        and "GatewayPorts no\n" in content
        and content.count("AllowTcpForwarding ") == 1
    ):
        return content, "ALREADY_MIGRATED"

    if content.count(OLD_DIRECTIVE) != 1:
        raise ValueError(
            "Unexpected SSH forwarding configuration (missing or duplicate 'AllowTcpForwarding no'); manual review required"
        )

    for conflict in ("PermitOpen ", "AllowStreamLocalForwarding ", "GatewayPorts ", "DisableForwarding "):
        if conflict in content:
            raise ValueError(f"Existing '{conflict.strip()}' directive requires manual review")

    return content.replace(OLD_DIRECTIVE, NEW_DIRECTIVES, 1), "MIGRATED"


def assert_no_symlink_components(path: str, *, stop_at: Optional[str] = None) -> None:
    """Reject any symlink component along path down to stop_at (or filesystem root)."""
    abs_target = os.path.abspath(path)
    abs_stop = os.path.abspath(stop_at) if stop_at else None

    if abs_stop and (abs_target == abs_stop or abs_target.startswith(abs_stop + os.sep)):
        cur = abs_stop
        if os.path.islink(cur):
            raise ValueError(f"SYMLINK_FORBIDDEN: '{cur}' is a symlink")
        rel = os.path.relpath(abs_target, abs_stop)
        if rel != ".":
            for part in rel.split(os.sep):
                cur = os.path.join(cur, part)
                if os.path.lexists(cur) and os.path.islink(cur):
                    raise ValueError(f"SYMLINK_FORBIDDEN: '{cur}' is a symlink")
        return

    cur = os.sep
    for part in abs_target.lstrip(os.sep).split(os.sep):
        if not part:
            continue
        cur = os.path.join(cur, part)
        if os.path.lexists(cur) and os.path.islink(cur):
            raise ValueError(f"SYMLINK_FORBIDDEN: '{cur}' is a symlink")


def resolve_verified_sshd_config_path(
    *,
    runtime_root: str,
    runtime_dir: str,
    docker_ssh_mount_src: str,
) -> Tuple[str, str]:
    """Verify runtime paths, symlink safety, containment in runtime_root, and match with Docker mount."""
    assert_no_symlink_components(runtime_root)
    real_root = os.path.realpath(runtime_root)
    assert_no_symlink_components(runtime_dir, stop_at=runtime_root)
    real_runtime_dir = os.path.realpath(runtime_dir)
    if real_runtime_dir != real_root and not real_runtime_dir.startswith(real_root + os.sep):
        raise ValueError(f"UNTRUSTED_RUNTIME_DIR: '{runtime_dir}' escapes '{runtime_root}'")

    expected_ssh_ro = os.path.join(runtime_dir, "ssh_ro")
    assert_no_symlink_components(expected_ssh_ro, stop_at=runtime_dir)
    real_expected_ssh_ro = os.path.realpath(expected_ssh_ro)
    real_mount_src = os.path.realpath(docker_ssh_mount_src)
    if real_mount_src != real_expected_ssh_ro:
        raise ValueError(
            f"MOUNT_SOURCE_MISMATCH: Docker mount '{docker_ssh_mount_src}' != expected '{expected_ssh_ro}'"
        )

    sshd_cfg = os.path.join(expected_ssh_ro, "sshd_config")
    assert_no_symlink_components(sshd_cfg, stop_at=expected_ssh_ro)
    if not os.path.isfile(sshd_cfg):
        raise ValueError(f"SSHD_CONFIG_MISSING: '{sshd_cfg}' does not exist or is not a regular file")
    real_cfg = os.path.realpath(sshd_cfg)
    if not real_cfg.startswith(real_root + os.sep):
        raise ValueError(f"UNTRUSTED_CONFIG_PATH: '{real_cfg}' outside '{real_root}'")
    return expected_ssh_ro, sshd_cfg


def load_trusted_record(registry_path: str, workspace_id: str) -> Dict[str, Any]:
    with open(registry_path, "r", encoding="utf-8") as rf:
        data = json.load(rf)
    records = data.get("records") if isinstance(data, dict) else None
    if not isinstance(records, dict) or workspace_id not in records:
        raise ValueError(f"UNKNOWN_WORKSPACE: '{workspace_id}' not found in '{registry_path}'")
    rec = records[workspace_id]
    if not isinstance(rec, dict):
        raise ValueError(f"INVALID_RECORD: workspace '{workspace_id}' entry is invalid")
    return rec


def inspect_docker_container(container_id: str) -> Dict[str, Any]:
    proc = subprocess.run(
        ["docker", "inspect", container_id],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"DOCKER_INSPECT_FAILED ({container_id}): {proc.stderr.strip()}")
    arr = json.loads(proc.stdout)
    if not isinstance(arr, list) or len(arr) != 1 or not isinstance(arr[0], dict):
        raise RuntimeError(f"DOCKER_INSPECT_AMBIGUOUS ({container_id})")
    return arr[0]


def verify_container_ownership(
    inspect_data: Dict[str, Any],
    *,
    workspace_id: str,
    rec: Dict[str, Any],
) -> Tuple[str, str]:
    cid = str(inspect_data.get("Id") or "")
    expected_cid = str(rec.get("canonical_container_id") or "")
    if not expected_cid or cid != expected_cid:
        raise ValueError(f"CONTAINER_ID_MISMATCH: inspect='{cid}' != registry='{expected_cid}'")

    labels = (inspect_data.get("Config") or {}).get("Labels") or {}
    if labels.get("labops.managed") != "true":
        raise ValueError("UNMANAGED_CONTAINER: label labops.managed != 'true'")
    if labels.get("labops.workspace_id") != workspace_id:
        raise ValueError(
            f"WORKSPACE_LABEL_MISMATCH: label='{labels.get('labops.workspace_id')}' != '{workspace_id}'"
        )

    expected_port = str(rec.get("ssh_port") or "")
    port_bindings = (inspect_data.get("HostConfig") or {}).get("PortBindings") or {}
    tcp2222 = port_bindings.get("2222/tcp") or []
    bound_ports = [str(b.get("HostPort") or "") for b in tcp2222 if isinstance(b, dict)]
    if expected_port and expected_port not in bound_ports:
        raise ValueError(f"SSH_PORT_MISMATCH: bound={bound_ports} expected={expected_port}")

    ssh_mount: Optional[Dict[str, Any]] = None
    for m in inspect_data.get("Mounts") or []:
        if isinstance(m, dict) and m.get("Destination") == "/etc/labops-ssh":
            ssh_mount = m
            break
    if not ssh_mount or not ssh_mount.get("Source"):
        raise ValueError("SSH_MOUNT_MISSING: container lacks /etc/labops-ssh mount")
    if ssh_mount.get("RW") is not False:
        raise ValueError("SSH_MOUNT_WRITABLE: /etc/labops-ssh mount must be read-only")

    return cid, str(ssh_mount["Source"])


def atomic_write_preserve_meta(target_path: str, content: str, ref_stat: os.stat_result) -> None:
    parent = os.path.dirname(target_path)
    tmp_path = os.path.join(parent, f".{os.path.basename(target_path)}.tmp.{os.getpid()}")
    mode = stat.S_IMODE(ref_stat.st_mode)
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as wf:
            wf.write(content)
            wf.flush()
            os.fsync(wf.fileno())
        os.chmod(tmp_path, mode)
        if os.geteuid() == 0:
            os.chown(tmp_path, ref_stat.st_uid, ref_stat.st_gid)
        os.replace(tmp_path, target_path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


def validate_sshd_in_container(container_id: str) -> List[str]:
    check = subprocess.run(
        ["docker", "exec", container_id, "/usr/sbin/sshd", "-t", "-f", "/etc/labops-ssh/sshd_config"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if check.returncode != 0:
        raise RuntimeError(f"SSHD_SYNTAX_CHECK_FAILED: {check.stderr.strip()}")

    dump = subprocess.run(
        ["docker", "exec", container_id, "/usr/sbin/sshd", "-T", "-f", "/etc/labops-ssh/sshd_config"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if dump.returncode != 0:
        raise RuntimeError(f"SSHD_EFFECTIVE_CONFIG_FAILED: {dump.stderr.strip()}")

    lines = [ln.strip() for ln in dump.stdout.splitlines() if ln.strip()]
    wanted_prefixes = (
        "allowtcpforwarding ",
        "permitopen ",
        "allowstreamlocalforwarding ",
        "gatewayports ",
        "x11forwarding ",
        "passwordauthentication ",
        "permitrootlogin ",
    )
    filtered = [ln for ln in lines if ln.lower().startswith(wanted_prefixes)]

    lowered = {ln.lower() for ln in filtered}
    required = {
        "allowtcpforwarding local",
        "permitopen 127.0.0.1:* localhost:*",
        "allowstreamlocalforwarding no",
        "gatewayports no",
        "x11forwarding no",
        "passwordauthentication no",
        "permitrootlogin no",
    }
    missing = sorted(required - lowered)
    if missing:
        raise RuntimeError(f"SSHD_POLICY_MISMATCH: missing effective directives {missing}")
    return filtered


def run_migration(
    *,
    workspace_id: str,
    mode: str,
    rollback_file: Optional[str] = None,
    registry_path: str = DEFAULT_REGISTRY_PATH,
    runtime_root: str = DEFAULT_RUNTIME_ROOT,
) -> Dict[str, Any]:
    rec = load_trusted_record(registry_path, workspace_id)
    runtime_dir = str(rec.get("runtime_dir") or "").strip() or os.path.join(runtime_root, workspace_id)
    inspect_data = inspect_docker_container(str(rec.get("canonical_container_id") or ""))
    cid, docker_mount_src = verify_container_ownership(inspect_data, workspace_id=workspace_id, rec=rec)
    _, sshd_cfg = resolve_verified_sshd_config_path(
        runtime_root=runtime_root,
        runtime_dir=runtime_dir,
        docker_ssh_mount_src=docker_mount_src,
    )
    st = os.lstat(sshd_cfg)
    with open(sshd_cfg, "r", encoding="utf-8") as rf:
        old_content = rf.read()

    if mode == "rollback":
        if not rollback_file:
            raise ValueError("--rollback requires backup file path")
        assert_no_symlink_components(rollback_file)
        real_bak = os.path.realpath(rollback_file)
        real_rt = os.path.realpath(runtime_dir)
        if not real_bak.startswith(real_rt + os.sep) or not os.path.isfile(real_bak):
            raise ValueError(f"INVALID_BACKUP_PATH: '{rollback_file}' must be regular file under '{runtime_dir}'")
        with open(real_bak, "r", encoding="utf-8") as bf:
            bak_content = bf.read()
        atomic_write_preserve_meta(sshd_cfg, bak_content, st)
        check = subprocess.run(
            ["docker", "exec", cid, "/usr/sbin/sshd", "-t", "-f", "/etc/labops-ssh/sshd_config"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if check.returncode != 0:
            raise RuntimeError(f"ROLLBACK_SSHD_T_FAILED: {check.stderr.strip()}")
        return {
            "workspace_id": workspace_id,
            "container_id": cid,
            "sshd_config": sshd_cfg,
            "mode": "rollback",
            "status": "ROLLED_BACK",
            "restored_from": real_bak,
        }

    new_content, status = migrate_sshd_config(old_content)
    diff = "".join(
        difflib.unified_diff(
            old_content.splitlines(keepends=True),
            new_content.splitlines(keepends=True),
            fromfile=f"{sshd_cfg} (before)",
            tofile=f"{sshd_cfg} (after)",
        )
    )

    result: Dict[str, Any] = {
        "workspace_id": workspace_id,
        "member_username": rec.get("member_username"),
        "container_id": cid,
        "container_name": str(inspect_data.get("Name") or "").lstrip("/"),
        "ssh_port": rec.get("ssh_port"),
        "sshd_config": sshd_cfg,
        "mode": mode,
        "status": status,
        "diff": diff,
        "reload_executed": False,
        "next_reload_note": "Approval required before running: docker kill --signal=HUP (see RUNTIME_MIGRATION.md for OpenSSH PID 1 behavior)",
    }

    if mode == "dry-run":
        return result

    if status == "ALREADY_MIGRATED":
        effective = validate_sshd_in_container(cid)
        result["effective_directives"] = effective
        return result

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = os.path.join(runtime_dir, f"sshd_config.{ts}.bak")
    atomic_write_preserve_meta(backup_path, old_content, st)
    result["backup_path"] = backup_path

    try:
        atomic_write_preserve_meta(sshd_cfg, new_content, st)
        effective = validate_sshd_in_container(cid)
        result["effective_directives"] = effective
    except Exception:
        atomic_write_preserve_meta(sshd_cfg, old_content, st)
        result["rolled_back_on_error"] = True
        raise

    return result


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Safe LabOps workspace sshd_config migration for VS Code Remote-SSH.")
    parser.add_argument("--workspace-id", required=True, help="Workspace ID in Trusted Registry (e.g. ws-7e9cc034ff)")
    parser.add_argument("--registry-path", default=DEFAULT_REGISTRY_PATH, help="Path to workspace_registry.json")
    parser.add_argument("--runtime-root", default=DEFAULT_RUNTIME_ROOT, help="Trusted LabOps runtime root")
    parser.add_argument("--json", action="store_true", help="Output structured JSON")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Verify ownership/paths and print diff without modifying files")
    group.add_argument("--apply", action="store_true", help="Backup, atomically migrate sshd_config, and verify sshd -t/-T")
    group.add_argument("--rollback", metavar="BACKUP_FILE", help="Atomically restore sshd_config from backup file")

    args = parser.parse_args(argv)
    mode = "dry-run" if args.dry_run else ("apply" if args.apply else "rollback")

    try:
        res = run_migration(
            workspace_id=args.workspace_id,
            mode=mode,
            rollback_file=args.rollback,
            registry_path=args.registry_path,
            runtime_root=args.runtime_root,
        )
    except Exception as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            sys.stderr.write(f"ERROR: {exc}\n")
        return 1

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"Workspace: {res['workspace_id']} ({res.get('container_name', '')})")
        print(f"Container: {res['container_id']}")
        print(f"Config:    {res['sshd_config']}")
        print(f"Mode/St:   {res['mode']} -> {res['status']}")
        if res.get("backup_path"):
            print(f"Backup:    {res['backup_path']}")
        if res.get("diff"):
            print("--- DIFF ---")
            print(res["diff"], end="")
        if res.get("effective_directives"):
            print("--- SSHD -T ---")
            for line in res["effective_directives"]:
                print(f"  {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
