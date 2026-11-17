"""Ownership checks for VPN provider IDs stored in member records."""
from __future__ import annotations

from typing import Any, Dict, List


class DeviceOwnershipError(PermissionError):
    pass


def find_owned_member_device(
    members: List[Dict[str, Any]],
    member: Dict[str, Any],
    requested_id: str,
) -> Dict[str, Any]:
    """Resolve a member-local ID only when its provider identity is uniquely owned."""
    if not isinstance(requested_id, str) or not requested_id:
        raise LookupError("VPN device was not found")

    member_indexes = [index for index, item in enumerate(members) if item is member]
    if len(member_indexes) != 1:
        raise DeviceOwnershipError("Member registry identity is ambiguous")
    member_index = member_indexes[0]
    profile = member.get("wireguard")
    devices = profile.get("devices", []) if isinstance(profile, dict) else []
    if not isinstance(devices, list):
        raise DeviceOwnershipError("Member VPN registry is invalid")

    matches = []
    for device_index, device in enumerate(devices):
        if not isinstance(device, dict):
            raise DeviceOwnershipError("Member VPN registry contains an invalid device")
        aliases = (device.get("device_id"), device.get("external_id"), device.get("id"))
        if any(isinstance(alias, (str, int)) and not isinstance(alias, bool) and str(alias) == requested_id for alias in aliases):
            matches.append((device_index, device))
    if len(matches) != 1:
        if not matches:
            raise LookupError("VPN device was not found")
        raise DeviceOwnershipError("Member VPN device ID is ambiguous")

    device_index, selected = matches[0]
    external_id = selected.get("external_id", selected.get("id"))
    if not isinstance(external_id, (str, int)) or isinstance(external_id, bool) or not str(external_id).strip():
        raise DeviceOwnershipError("VPN device has no trusted provider identity")
    provider = selected.get("provider", "wg-easy")
    if not isinstance(provider, str) or provider not in {"wg-easy", "legacy-wg0"}:
        raise DeviceOwnershipError("VPN device provider is not trusted")

    references = []
    for owner_index, owner in enumerate(members):
        owner_profile = owner.get("wireguard") if isinstance(owner, dict) else None
        owner_devices = owner_profile.get("devices", []) if isinstance(owner_profile, dict) else []
        if not isinstance(owner_devices, list):
            raise DeviceOwnershipError("Member VPN registry is invalid")
        for candidate_index, candidate in enumerate(owner_devices):
            if not isinstance(candidate, dict):
                raise DeviceOwnershipError("Member VPN registry contains an invalid device")
            candidate_provider = candidate.get("provider", "wg-easy")
            candidate_id = candidate.get("external_id", candidate.get("id"))
            if candidate_provider == provider and isinstance(candidate_id, (str, int)) and not isinstance(candidate_id, bool) and str(candidate_id) == str(external_id):
                references.append((owner_index, candidate_index, candidate))

    if len(references) != 1 or references[0][:2] != (member_index, device_index):
        raise DeviceOwnershipError("VPN provider identity is shared or assigned to another member")
    return selected
