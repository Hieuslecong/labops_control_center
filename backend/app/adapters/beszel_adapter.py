"""
Beszel Monitoring & Alerting Adapter (LabOps V2 — Mục 16).

- Kết nối API chính thức của Beszel Hub (PocketBase REST API `/api/collections/systems/records`
  và `/api/collections/system_stats/records`).
- Luôn trả kèm nguồn dữ liệu (`source`), thời điểm cập nhật (`collected_at`),
  và trạng thái độ tươi (`ONLINE / STALE / OFFLINE / UNKNOWN`).
- Khi chưa cấu hình `BESZEL_HUB_URL` -> trả `NOT_CONFIGURED`, tuyệt đối không sinh biểu đồ giả.
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

PINNED_BESZEL_VERSION = "v0.9.1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class BeszelAdapter:
    def __init__(self, hub_url: Optional[str] = None, token: Optional[str] = None):
        self.hub_url = (hub_url if hub_url is not None else os.getenv("BESZEL_HUB_URL", "")).rstrip("/")
        self.token = token if token is not None else os.getenv("BESZEL_API_TOKEN", "")

    @property
    def is_configured(self) -> bool:
        return bool(self.hub_url)

    def get_monitoring_overview(self, live_hosts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        if not self.is_configured:
            return {
                "service": "Beszel Infrastructure Monitoring",
                "pinned_version": PINNED_BESZEL_VERSION,
                "status": "NOT_CONFIGURED",
                "freshness": "UNKNOWN",
                "source": "beszel_api",
                "collected_at": _now(),
                "message": "Chưa cấu hình BESZEL_HUB_URL. Hiển thị chỉ số trực tiếp từ psutil/nvidia-smi cục bộ nếu khả dụng.",
                "systems": [],
                "alerts": [],
                "local_fallback_hosts": live_hosts or [],
            }

        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(
            f"{self.hub_url}/api/collections/systems/records?perPage=50",
            headers=headers,
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                items = payload.get("items", []) if isinstance(payload, dict) else []
                return {
                    "service": "Beszel Infrastructure Monitoring",
                    "pinned_version": PINNED_BESZEL_VERSION,
                    "status": "ONLINE",
                    "freshness": "ONLINE",
                    "source": "beszel_api",
                    "collected_at": _now(),
                    "message": "Đã đồng bộ dữ liệu giám sát từ Beszel Hub.",
                    "systems": items,
                    "alerts": [],
                    "local_fallback_hosts": live_hosts or [],
                }
        except Exception as exc:
            return {
                "service": "Beszel Infrastructure Monitoring",
                "pinned_version": PINNED_BESZEL_VERSION,
                "status": "UNAVAILABLE",
                "freshness": "OFFLINE",
                "source": "beszel_api",
                "collected_at": _now(),
                "message": f"Không thể kết nối Beszel Hub: {str(exc)[:200]}",
                "systems": [],
                "alerts": [],
                "local_fallback_hosts": live_hosts or [],
            }

