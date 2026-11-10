"""
Arcane Official API Integration Adapter (LabOps V2 — Mục 13).

- Sử dụng giao diện REST chính thức của Arcane (ghim phiên bản tương thích: v1.1.0).
- Chỉ phục vụ Quản trị viên (Admin): liệt kê môi trường Docker, container, image,
  volume, network, compose project và trạng thái tích hợp.
- Nghiên cứu viên (Researcher) KHÔNG BAO GIỜ được cấp quyền truy cập Arcane hay Docker socket.
- Khi chưa cấu hình ARCANE_BASE_URL / ARCANE_API_KEY hoặc dịch vụ không phản hồi ->
  trả trạng thái trung thực `NOT_CONFIGURED` hoặc `UNAVAILABLE`, không bịa dữ liệu.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

PINNED_ARCANE_VERSION = "v1.1.0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ArcaneAdapter:
    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None):
        self.base_url = (base_url if base_url is not None else os.getenv("ARCANE_BASE_URL", "")).rstrip("/")
        self.api_key = api_key if api_key is not None else os.getenv("ARCANE_API_KEY", "")

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def _get(self, path: str) -> Dict[str, Any]:
        if not self.is_configured:
            return {"status": "NOT_CONFIGURED", "data": None}
        url = f"{self.base_url}/api/{path.lstrip('/')}"
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "X-Arcane-Client-Version": PINNED_ARCANE_VERSION,
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                return {"status": "ONLINE", "data": payload}
        except Exception as exc:
            return {"status": "UNAVAILABLE", "error": str(exc)[:240], "data": None}

    def get_integration_summary(self) -> Dict[str, Any]:
        if not self.is_configured:
            return {
                "service": "Arcane Docker Management",
                "pinned_version": PINNED_ARCANE_VERSION,
                "status": "NOT_CONFIGURED",
                "freshness": "UNKNOWN",
                "collected_at": _now(),
                "source": "arcane_api",
                "message": "Chưa cấu hình ARCANE_BASE_URL và ARCANE_API_KEY. Không hiển thị dữ liệu container giả.",
                "environments": [],
                "containers": [],
                "images": [],
                "volumes": [],
                "networks": [],
                "stacks": [],
                "security_boundary": {
                    "admin_access": True,
                    "researcher_access": False,
                    "docker_socket_exposed_to_researcher": False,
                },
            }

        env_res = self._get("environments")
        if env_res["status"] != "ONLINE":
            return {
                "service": "Arcane Docker Management",
                "pinned_version": PINNED_ARCANE_VERSION,
                "status": "UNAVAILABLE",
                "freshness": "OFFLINE",
                "collected_at": _now(),
                "source": "arcane_api",
                "message": f"Arcane không phản hồi: {env_res.get('error', 'unverified')}",
                "environments": [],
                "containers": [],
                "images": [],
                "volumes": [],
                "networks": [],
                "stacks": [],
                "security_boundary": {
                    "admin_access": True,
                    "researcher_access": False,
                    "docker_socket_exposed_to_researcher": False,
                },
            }

        containers = self._get("containers").get("data") or []
        images = self._get("images").get("data") or []
        volumes = self._get("volumes").get("data") or []
        networks = self._get("networks").get("data") or []
        stacks = self._get("stacks").get("data") or []
        return {
            "service": "Arcane Docker Management",
            "pinned_version": PINNED_ARCANE_VERSION,
            "status": "ONLINE",
            "freshness": "ONLINE",
            "collected_at": _now(),
            "source": "arcane_api",
            "message": "Đã kết nối Arcane API chính thức.",
            "environments": env_res.get("data") or [],
            "containers": containers if isinstance(containers, list) else [],
            "images": images if isinstance(images, list) else [],
            "volumes": volumes if isinstance(volumes, list) else [],
            "networks": networks if isinstance(networks, list) else [],
            "stacks": stacks if isinstance(stacks, list) else [],
            "security_boundary": {
                "admin_access": True,
                "researcher_access": False,
                "docker_socket_exposed_to_researcher": False,
            },
        }

