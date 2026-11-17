"""Validation for provider-issued WireGuard client configurations."""
from __future__ import annotations

from dataclasses import dataclass


class VPNConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ProviderVPNConfig:
    config: str
    client_address: str
    endpoint: str


def parse_provider_vpn_config(config: object) -> ProviderVPNConfig:
    """Validate an issued config and read its endpoint without substituting defaults."""
    if not isinstance(config, str) or not config.strip():
        raise VPNConfigError("provider-issued VPN configuration is unavailable")

    sections: dict[str, dict[str, str]] = {}
    current: str | None = None
    for raw_line in config.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            if section not in {"interface", "peer"} or section in sections:
                raise VPNConfigError("provider-issued VPN configuration has invalid sections")
            current = section
            sections[current] = {}
            continue
        if current is None or "=" not in line:
            raise VPNConfigError("provider-issued VPN configuration is malformed")
        key, value = line.split("=", 1)
        key = key.strip().lower()
        value = value.strip()
        if not key or not value or key in sections[current]:
            raise VPNConfigError("provider-issued VPN configuration has invalid fields")
        sections[current][key] = value

    interface = sections.get("interface", {})
    peer = sections.get("peer", {})
    address = interface.get("address", "")
    endpoint = peer.get("endpoint", "")
    if not interface.get("privatekey") or not address:
        raise VPNConfigError("provider-issued VPN configuration lacks interface identity")
    if not peer.get("publickey") or not peer.get("allowedips") or not _valid_endpoint(endpoint):
        raise VPNConfigError("provider-issued VPN configuration lacks a valid peer")
    return ProviderVPNConfig(config=config, client_address=address, endpoint=endpoint)


def _valid_endpoint(endpoint: str) -> bool:
    if not endpoint or any(char.isspace() for char in endpoint):
        return False
    if endpoint.startswith("["):
        closing = endpoint.find("]:")
        if closing <= 1:
            return False
        host, port = endpoint[1:closing], endpoint[closing + 2:]
    else:
        if ":" not in endpoint:
            return False
        host, port = endpoint.rsplit(":", 1)
    return bool(host and port.isdigit() and 1 <= int(port) <= 65535)
