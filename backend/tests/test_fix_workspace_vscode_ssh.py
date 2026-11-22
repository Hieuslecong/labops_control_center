"""Unit tests for scripts/ops/fix-workspace-vscode-ssh.py."""
from __future__ import annotations

import importlib.util
import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "ops" / "fix-workspace-vscode-ssh.py"
_SPEC = importlib.util.spec_from_file_location("fix_workspace_vscode_ssh", SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


SAMPLE_OLD_CONFIG = (
    "Port 2222\n"
    "ListenAddress 0.0.0.0\n"
    "HostKey /tmp/ssh_host_ed25519_key\n"
    "PidFile /tmp/sshd.pid\n"
    "PermitRootLogin no\n"
    "PasswordAuthentication no\n"
    "KbdInteractiveAuthentication no\n"
    "ChallengeResponseAuthentication no\n"
    "UsePAM no\n"
    "PubkeyAuthentication yes\n"
    "AuthorizedKeysFile /etc/labops-ssh/authorized_keys\n"
    "StrictModes no\n"
    "AllowUsers testacc\n"
    "AllowTcpForwarding no\n"
    "X11Forwarding no\n"
    "Subsystem sftp internal-sftp\n"
)


class TestFixWorkspaceVSCodeSSH(unittest.TestCase):
    def test_migrate_sshd_config_success_and_idempotent(self) -> None:
        migrated, status = mod.migrate_sshd_config(SAMPLE_OLD_CONFIG)
        self.assertEqual(status, "MIGRATED")
        self.assertIn("AllowTcpForwarding local\n", migrated)
        self.assertIn("PermitOpen 127.0.0.1:* localhost:*\n", migrated)
        self.assertIn("AllowStreamLocalForwarding no\n", migrated)
        self.assertIn("GatewayPorts no\n", migrated)
        self.assertNotIn("AllowTcpForwarding no\n", migrated)
        self.assertIn("PasswordAuthentication no\n", migrated)
        self.assertIn("AllowUsers testacc\n", migrated)

        second, status2 = mod.migrate_sshd_config(migrated)
        self.assertEqual(status2, "ALREADY_MIGRATED")
        self.assertEqual(second, migrated)

    def test_migrate_sshd_config_rejects_unexpected_directives(self) -> None:
        with self.assertRaises(ValueError):
            mod.migrate_sshd_config(SAMPLE_OLD_CONFIG + "PermitOpen any\n")
        with self.assertRaises(ValueError):
            mod.migrate_sshd_config(SAMPLE_OLD_CONFIG.replace("AllowTcpForwarding no\n", ""))

    def test_rejects_symlink_and_untrusted_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rt_root = os.path.join(td, "runtime")
            ws_dir = os.path.join(rt_root, "ws-1")
            ssh_ro = os.path.join(ws_dir, "ssh_ro")
            os.makedirs(ssh_ro, exist_ok=True)
            cfg = os.path.join(ssh_ro, "sshd_config")
            outside = os.path.join(td, "evil_sshd_config")
            with open(outside, "w", encoding="utf-8") as wf:
                wf.write(SAMPLE_OLD_CONFIG)
            os.symlink(outside, cfg)

            with self.assertRaises(ValueError):
                mod.resolve_verified_sshd_config_path(
                    runtime_root=rt_root,
                    runtime_dir=ws_dir,
                    docker_ssh_mount_src=ssh_ro,
                )

    def test_dry_run_does_not_modify_file_and_apply_backs_up_with_rollback_on_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            rt_root = os.path.join(td, "runtime")
            ws_id = "ws-test-1"
            ws_dir = os.path.join(rt_root, ws_id)
            ssh_ro = os.path.join(ws_dir, "ssh_ro")
            os.makedirs(ssh_ro, exist_ok=True)
            cfg = os.path.join(ssh_ro, "sshd_config")
            ak = os.path.join(ssh_ro, "authorized_keys")
            with open(cfg, "w", encoding="utf-8") as wf:
                wf.write(SAMPLE_OLD_CONFIG)
            os.chmod(cfg, 0o644)
            with open(ak, "w", encoding="utf-8") as wf:
                wf.write("ssh-ed25519 AAAA\n")

            cid = "a" * 64
            reg_path = os.path.join(td, "workspace_registry.json")
            with open(reg_path, "w", encoding="utf-8") as wf:
                json.dump(
                    {
                        "records": {
                            ws_id: {
                                "workspace_id": ws_id,
                                "member_username": "testacc",
                                "canonical_container_id": cid,
                                "runtime_dir": ws_dir,
                                "ssh_port": 22001,
                            }
                        }
                    },
                    wf,
                )

            inspect_payload = [
                {
                    "Id": cid,
                    "Name": "/ws-testacc",
                    "Config": {"Labels": {"labops.managed": "true", "labops.workspace_id": ws_id}},
                    "HostConfig": {"PortBindings": {"2222/tcp": [{"HostIp": "192.168.167.251", "HostPort": "22001"}]}},
                    "Mounts": [{"Destination": "/etc/labops-ssh", "Source": ssh_ro, "RW": False}],
                }
            ]

            def fake_run_ok(cmd, capture_output=True, text=True, timeout=10):
                if cmd[:2] == ["docker", "inspect"]:
                    return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(inspect_payload), stderr="")
                if cmd[:4] == ["docker", "exec", cid, "/usr/sbin/sshd"]:
                    if "-t" in cmd:
                        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
                    return subprocess.CompletedProcess(
                        cmd,
                        0,
                        stdout=(
                            "allowtcpforwarding local\n"
                            "permitopen 127.0.0.1:* localhost:*\n"
                            "allowstreamlocalforwarding no\n"
                            "gatewayports no\n"
                            "x11forwarding no\n"
                            "passwordauthentication no\n"
                            "permitrootlogin no\n"
                        ),
                        stderr="",
                    )
                raise AssertionError(f"Unexpected subprocess command: {cmd}")

            with patch.object(mod.subprocess, "run", side_effect=fake_run_ok):
                dry = mod.run_migration(
                    workspace_id=ws_id,
                    mode="dry-run",
                    registry_path=reg_path,
                    runtime_root=rt_root,
                )
            self.assertEqual(dry["status"], "MIGRATED")
            with open(cfg, "r", encoding="utf-8") as rf:
                self.assertEqual(rf.read(), SAMPLE_OLD_CONFIG)

            # Simulate sshd -t failure during apply -> must roll back automatically
            def fake_run_fail_sshd(cmd, capture_output=True, text=True, timeout=10):
                if cmd[:2] == ["docker", "inspect"]:
                    return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(inspect_payload), stderr="")
                return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="bad sshd_config line")

            with patch.object(mod.subprocess, "run", side_effect=fake_run_fail_sshd):
                with self.assertRaises(RuntimeError):
                    mod.run_migration(
                        workspace_id=ws_id,
                        mode="apply",
                        registry_path=reg_path,
                        runtime_root=rt_root,
                    )
            with open(cfg, "r", encoding="utf-8") as rf:
                self.assertEqual(rf.read(), SAMPLE_OLD_CONFIG)

            # Real apply succeeds, preserves mode & authorized_keys, and creates backup
            with patch.object(mod.subprocess, "run", side_effect=fake_run_ok):
                applied = mod.run_migration(
                    workspace_id=ws_id,
                    mode="apply",
                    registry_path=reg_path,
                    runtime_root=rt_root,
                )
            self.assertEqual(applied["status"], "MIGRATED")
            self.assertEqual(stat.S_IMODE(os.stat(cfg).st_mode), 0o644)
            self.assertTrue(os.path.isfile(applied["backup_path"]))
            with open(cfg, "r", encoding="utf-8") as rf:
                self.assertIn("AllowTcpForwarding local\n", rf.read())
            with open(ak, "r", encoding="utf-8") as rf:
                self.assertEqual(rf.read(), "ssh-ed25519 AAAA\n")

            # Rollback restores exact old config
            with patch.object(mod.subprocess, "run", side_effect=fake_run_ok):
                rb = mod.run_migration(
                    workspace_id=ws_id,
                    mode="rollback",
                    rollback_file=applied["backup_path"],
                    registry_path=reg_path,
                    runtime_root=rt_root,
                )
            self.assertEqual(rb["status"], "ROLLED_BACK")
            with open(cfg, "r", encoding="utf-8") as rf:
                self.assertEqual(rf.read(), SAMPLE_OLD_CONFIG)


if __name__ == "__main__":
    unittest.main()
