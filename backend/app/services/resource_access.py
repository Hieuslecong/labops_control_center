"""Pure ownership filtering helpers for API resources."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from app.core.security import Principal


def visible_storage_allocations(
    allocations: Iterable[Dict[str, Any]],
    members: Iterable[Dict[str, Any]],
    principal: Principal,
) -> List[Dict[str, Any]]:
    if principal.kind != "user":
        return []
    items = list(allocations)
    if principal.is_admin:
        return items
    member_ids = {
        member.get("id") for member in members
        if isinstance(member, dict) and member.get("username") == principal.username and member.get("id")
    }
    return [
        item for item in items
        if isinstance(item, dict)
        and (
            item.get("username") == principal.username
            or item.get("owner") == principal.username
            or item.get("member_id") in member_ids
        )
    ]


def find_visible_workspace(
    workspaces: Iterable[Dict[str, Any]],
    requested_id: str,
    principal: Principal,
) -> Optional[Dict[str, Any]]:
    if principal.kind != "user":
        return None
    matches = [
        workspace for workspace in workspaces
        if isinstance(workspace, dict)
        and requested_id in (workspace.get("id"), workspace.get("name"))
        and (principal.is_admin or workspace.get("owner") == principal.username)
    ]
    return matches[0] if len(matches) == 1 else None
