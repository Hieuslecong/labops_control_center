import json
import os
import re
import shutil
import smtplib
import ssl
import subprocess
from email.message import EmailMessage
from typing import Any, Dict, Optional
from urllib.parse import urlsplit


class TeleportError(RuntimeError):
    """Teleport is unavailable or did not verify the requested operation."""


class TeleportAdapter:
    COMMUNITY_VERSION = "18.11.8"

    def __init__(self, tctl_path: str = "tctl"):
        self.tctl_path = tctl_path

    @staticmethod
    def role_resource(username: str, workspace: str, user_type: str) -> Dict[str, Any]:
        if not re.fullmatch(r"[a-z_][a-z0-9_-]{2,31}", username) or username in {"root", "admin", "teleport"}:
            raise TeleportError("Teleport/Linux login is invalid")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{2,63}", workspace):
            raise TeleportError("Workspace label is invalid")
        if user_type not in {"RESEARCHER", "ADMIN"}:
            raise TeleportError("User type is invalid")
        ttl = "2h" if user_type == "ADMIN" else "12h"
        labels = ["*"] if user_type == "ADMIN" else [workspace]
        return {
            "kind": "role",
            "version": "v8",
            "metadata": {"name": f"labops-{user_type.lower()}-{username}"},
            "spec": {
                "allow": {"logins": [username], "node_labels": {"labops-workspace": labels}},
                "options": {
                    "max_session_ttl": ttl,
                    "disconnect_expired_cert": True,
                    "require_session_mfa": user_type == "ADMIN",
                    "forward_agent": False,
                },
            },
        }

    def _binary(self) -> str:
        binary = shutil.which(self.tctl_path)
        if not binary:
            raise TeleportError("Teleport tctl is not installed")
        return binary

    def _run(self, args, *, input_text: Optional[str] = None, check: bool = True) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(
                [self._binary(), *args], input=input_text, text=True,
                capture_output=True, timeout=15, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise TeleportError("Teleport command could not be completed") from exc
        if check and result.returncode:
            raise TeleportError("Teleport command failed")
        return result

    def health(self) -> Dict[str, Any]:
        if not shutil.which(self.tctl_path):
            return {"status": "NOT_CONFIGURED", "version": None}
        try:
            self._run(["status"])
            version_output = self._run(["version"]).stdout
        except TeleportError:
            return {"status": "UNAVAILABLE", "version": None}
        match = re.search(r"Teleport v?(\d+\.\d+\.\d+)", version_output)
        version = match.group(1) if match else None
        return {
            "status": "AVAILABLE" if version == self.COMMUNITY_VERSION else "VERSION_MISMATCH",
            "version": version,
            "cluster_version": "NOT_VERIFIED",
        }

    def _resource(self, kind: str, name: str) -> Optional[Dict[str, Any]]:
        result = self._run(["get", f"{kind}/{name}", "--format=json"], check=False)
        if result.returncode == 0:
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError as exc:
                raise TeleportError("Teleport returned an invalid resource") from exc
        if re.search(r"not found|does not exist|doesn't exist", result.stderr, re.IGNORECASE):
            return None
        raise TeleportError("Teleport resource lookup failed")

    def _ensure_role(self, role: Dict[str, Any]) -> None:
        name = role["metadata"]["name"]
        existing = self._resource("role", name)
        if existing:
            if existing.get("spec") != role["spec"]:
                raise TeleportError("Existing Teleport role differs from the required access policy")
            return
        self._run(["create", "-f", "-"], input_text=json.dumps(role))

    @staticmethod
    def _invitation_url(output: str, public_addr: str) -> str:
        match = re.search(r"https://[^\s]+/web/invite/[^\s]+", output)
        if not match:
            raise TeleportError("Teleport did not return an invitation URL")
        invite = urlsplit(match.group(0).rstrip(".,)"))
        expected = urlsplit(public_addr if "://" in public_addr else f"https://{public_addr}")
        if invite.scheme != "https" or invite.hostname != expected.hostname or invite.port != expected.port:
            raise TeleportError("Teleport invitation host does not match the configured proxy")
        return invite.geturl()

    @staticmethod
    def _deliver_invitation(email: str, invite_url: str) -> None:
        host = os.getenv("SMTP_HOST", "")
        sender = os.getenv("SMTP_FROM", "")
        port = int(os.getenv("SMTP_PORT", "587"))
        username = os.getenv("SMTP_USERNAME", "")
        password = os.getenv("SMTP_PASSWORD", "")
        if not host or not sender:
            raise TeleportError("Secure invitation email delivery is not configured")
        message = EmailMessage()
        message["Subject"] = "Activate your LabOps access"
        message["From"] = sender
        message["To"] = email
        message.set_content(f"Activate your LabOps access and register WebAuthn MFA using this one-time link (expires in one hour):\n\n{invite_url}\n")
        context = ssl.create_default_context()
        try:
            if port == 465:
                with smtplib.SMTP_SSL(host, port, timeout=10, context=context) as client:
                    if username:
                        client.login(username, password)
                    client.send_message(message)
            else:
                with smtplib.SMTP(host, port, timeout=10) as client:
                    client.ehlo()
                    client.starttls(context=context)
                    client.ehlo()
                    if username:
                        client.login(username, password)
                    client.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            raise TeleportError("Secure invitation email delivery failed") from exc

    def provision_user(self, username: str, email: str, workspace: str, user_type: str) -> Dict[str, str]:
        public_addr = os.getenv("TELEPORT_PUBLIC_ADDR", "")
        if not public_addr or not os.getenv("SMTP_HOST") or not os.getenv("SMTP_FROM"):
            raise TeleportError("Teleport proxy and secure invitation delivery are not configured")
        try:
            smtp_port = int(os.getenv("SMTP_PORT", "587"))
        except ValueError as exc:
            raise TeleportError("Secure invitation email delivery configuration is invalid") from exc
        if not 1 <= smtp_port <= 65535 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
            raise TeleportError("Secure invitation email configuration or user email is invalid")
        proxy = urlsplit(public_addr if "://" in public_addr else f"https://{public_addr}")
        if proxy.scheme != "https" or not proxy.hostname:
            raise TeleportError("Teleport public address must use HTTPS")
        role = self.role_resource(username, workspace, user_type)
        self._binary()
        self._ensure_role(role)
        existing = self._resource("user", username)
        if existing:
            roles = existing.get("spec", {}).get("roles", [])
            if roles != [role["metadata"]["name"]]:
                raise TeleportError("Existing Teleport user has a different role assignment")
            return {"status": "EXISTS", "role": role["metadata"]["name"]}
        result = self._run([
            "users", "add", username, f"--roles={role['metadata']['name']}",
            f"--logins={username}", "--ttl=1h",
        ])
        try:
            invite_url = self._invitation_url(result.stdout, public_addr)
            self._deliver_invitation(email, invite_url)
        except TeleportError as exc:
            try:
                self._run(["users", "rm", username])
            except TeleportError as rollback_error:
                raise TeleportError("Invitation failed and Teleport user rollback needs manual review") from rollback_error
            raise exc
        return {"status": "INVITED", "role": role["metadata"]["name"]}

    def revoke_user(self, username: str) -> Dict[str, str]:
        """Remove Teleport identity; Community session termination still needs live verification."""
        self._run(["users", "rm", username])
        return {"status": "PARTIAL_FAILURE", "reason": "Active SSH sessions were not verified as terminated"}
