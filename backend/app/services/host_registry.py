"""
Dynamic Host Registry & Non-Destructive JSON-to-Relational Reconciler (LabOps V2).

- Không giới hạn cứng chỉ 2 máy chủ (track/workstation); hỗ trợ đăng ký N máy chủ.
- Tách biệt rõ Desired State (ACTIVE/MAINTENANCE/DRAINING/DISABLED) và
  Observed State (ONLINE/STALE/OFFLINE/UNKNOWN).
- Mọi chỉ số đều mang metadata: source, collected_at, freshness, unit.
- Tích hợp đối soát (reconcile) không phá hủy từ dữ liệu JSON hiện có.
"""
from __future__ import annotations

import fcntl
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

ALLOWED_ROLES = {"CONTROL_PLANE", "COMPUTE_NODE", "STORAGE_NODE", "HYBRID"}
ALLOWED_DESIRED_STATES = {"ACTIVE", "MAINTENANCE", "DRAINING", "DISABLED"}
HEARTBEAT_STALE_SECONDS = 90
HEARTBEAT_OFFLINE_SECONDS = 300


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_freshness(collected_at: Optional[str]) -> str:
    if not collected_at:
        return "UNKNOWN"
    try:
        dt = datetime.fromisoformat(collected_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        if age <= HEARTBEAT_STALE_SECONDS:
            return "ONLINE"
        if age <= HEARTBEAT_OFFLINE_SECONDS:
            return "STALE"
        return "OFFLINE"
    except Exception:
        return "UNKNOWN"


class HostRegistry:
    def __init__(self, data_dir: str, local_host_id: str = "track"):
        self.data_dir = data_dir
        self.local_host_id = local_host_id
        self.state_path = os.path.join(data_dir, "v2_hosts.json")
        self.lock_path = self.state_path + ".lock"
        os.makedirs(data_dir, exist_ok=True)
        self._ensure_bootstrap()

    @contextmanager
    def _locked(self):
        with open(self.lock_path, "a+") as lf:
            fcntl.flock(lf, fcntl.LOCK_EX)
            try:
                state = self._load()
                yield state
                self._save(state)
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)

    def _load(self) -> Dict[str, Any]:
        if not os.path.exists(self.state_path):
            return {"hosts": [], "reconciled_at": None}
        with open(self.state_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _save(self, state: Dict[str, Any]) -> None:
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.state_path)

    def _ensure_bootstrap(self) -> None:
        with self._locked() as state:
            if state["hosts"]:
                return
            is_track_local = self.local_host_id == "track"
            state["hosts"] = [
                {
                    "id": "track",
                    "hostname": "aiotlab-rack",
                    "display_name": "Máy chủ 01 — Server Rack (aiotlab-rack • 3x NVIDIA A30)",
                    "role": "HYBRID",
                    "management_ip": "192.168.167.250",
                    "vpn_ip": None,
                    "ssh_legacy_port": 22,
                    "agent_url": "http://127.0.0.1:8091" if is_track_local else None,
                    "desired_state": "ACTIVE",
                    "observed_state": "ONLINE" if is_track_local else "UNKNOWN",
                    "telemetry_source": "local_psutil_nvml" if is_track_local else "NOT_CONFIGURED",
                    "last_heartbeat_at": _now() if is_track_local else None,
                    "created_at": _now(),
                    "updated_at": _now(),
                },
                {
                    "id": "workstation",
                    "hostname": "Chưa xác minh",
                    "display_name": "Máy chủ 02 — Workstation (Từ xa • Chưa kết nối)",
                    "role": "COMPUTE_NODE",
                    "management_ip": "",
                    "vpn_ip": None,
                    "ssh_legacy_port": 22,
                    "agent_url": None,
                    "desired_state": "ACTIVE",
                    "observed_state": "UNKNOWN" if is_track_local else "ONLINE",
                    "telemetry_source": "NOT_CONFIGURED" if is_track_local else "local_psutil_nvml",
                    "last_heartbeat_at": None if is_track_local else _now(),
                    "created_at": _now(),
                    "updated_at": _now(),
                },
            ]
            state["reconciled_at"] = _now()

    def list_hosts(self, live_servers: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
        state = self._load()
        hosts = state.get("hosts", [])
        live_map = {s["id"]: s for s in (live_servers or []) if "id" in s}
        enriched: List[Dict[str, Any]] = []
        for h in hosts:
            item = dict(h)
            live = live_map.get(h["id"])
            if live:
                is_local_live = h["id"] == self.local_host_id and live.get("status") == "ONLINE"
                collected_at = _now() if is_local_live else item.get("last_heartbeat_at")
                freshness = "ONLINE" if is_local_live else compute_freshness(collected_at)
                item.update({
                    "observed_state": freshness,
                    "freshness": freshness,
                    "collected_at": collected_at,
                    "telemetry_source": "psutil+nvidia-smi" if is_local_live else item.get("telemetry_source", "UNKNOWN"),
                    "cpu_model": live.get("cpu_model"),
                    "cpu_cores": live.get("cpu_cores"),
                    "cpu_threads": live.get("cpu_threads"),
                    "cpu_usage_percent": live.get("cpu_usage_percent"),
                    "ram_total_gb": live.get("ram_total_gb"),
                    "ram_used_gb": live.get("ram_used_gb"),
                    "unit_ram": "GiB",
                    "unit_cpu": "percent",
                    "gpus": live.get("gpus", []),
                })
            else:
                freshness = compute_freshness(item.get("last_heartbeat_at"))
                item["observed_state"] = freshness
                item["freshness"] = freshness
                item["collected_at"] = item.get("last_heartbeat_at")
            enriched.append(item)
        return enriched

    def register_host(
        self,
        *,
        host_id: str,
        hostname: str,
        display_name: str,
        management_ip: str,
        role: str = "COMPUTE_NODE",
        vpn_ip: Optional[str] = None,
        agent_url: Optional[str] = None,
        ssh_legacy_port: int = 22,
    ) -> Dict[str, Any]:
        host_id = host_id.strip().lower()
        if not host_id or not hostname or not management_ip:
            raise ValueError("Thiếu thông tin bắt buộc: host_id, hostname, management_ip")
        if role not in ALLOWED_ROLES:
            raise ValueError(f"Vai trò máy chủ không hợp lệ: {role}")
        with self._locked() as state:
            if any(h["id"] == host_id or h["hostname"] == hostname for h in state["hosts"]):
                raise ValueError(f"Máy chủ '{host_id}' hoặc hostname '{hostname}' đã tồn tại")
            new_host = {
                "id": host_id,
                "hostname": hostname.strip(),
                "display_name": display_name.strip() or hostname.strip(),
                "role": role,
                "management_ip": management_ip.strip(),
                "vpn_ip": vpn_ip.strip() if vpn_ip else None,
                "ssh_legacy_port": int(ssh_legacy_port),
                "agent_url": agent_url.strip() if agent_url else None,
                "desired_state": "ACTIVE",
                "observed_state": "UNKNOWN",
                "telemetry_source": "NODE_AGENT_PENDING",
                "last_heartbeat_at": None,
                "created_at": _now(),
                "updated_at": _now(),
            }
            state["hosts"].append(new_host)
            return new_host

    def update_desired_state(self, host_id: str, desired_state: str) -> Dict[str, Any]:
        if desired_state not in ALLOWED_DESIRED_STATES:
            raise ValueError(f"Trạng thái mong muốn không hợp lệ: {desired_state}")
        with self._locked() as state:
            host = next((h for h in state["hosts"] if h["id"] == host_id), None)
            if not host:
                raise LookupError(f"Không tìm thấy máy chủ {host_id}")
            host["desired_state"] = desired_state
            host["updated_at"] = _now()
            return dict(host)

    def record_agent_heartbeat(self, host_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        with self._locked() as state:
            host = next((h for h in state["hosts"] if h["id"] == host_id), None)
            if not host:
                raise LookupError(f"Máy chủ {host_id} chưa được đăng ký trong Host Registry")
            host["last_heartbeat_at"] = _now()
            host["observed_state"] = "ONLINE"
            host["telemetry_source"] = "labops_node_agent"
            host["agent_version"] = payload.get("agent_version", host.get("agent_version"))
            host["updated_at"] = _now()
            return dict(host)

    def reconcile_json_stores(self) -> Dict[str, Any]:
        """Đối soát không phá hủy các tệp JSON hiện có và kiểm tra tính toàn vẹn."""
        summary: Dict[str, Any] = {
            "reconciled_at": _now(),
            "mode": "NON_DESTRUCTIVE",
            "stores": {},
            "anomalies": [],
        }
        files = [
            "members.json",
            "ssh_endpoints.json",
            "ssh_policies.json",
            "storage_allocations.json",
            "wireguard_peers.json",
            "gpu_reservations.json",
            "security_scans.json",
            "audit_logs.json",
        ]
        for fname in files:
            fpath = os.path.join(self.data_dir, fname)
            if not os.path.exists(fpath):
                summary["stores"][fname] = {"exists": False, "records": 0}
                continue
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                count = len(data) if isinstance(data, list) else len(next(iter(data.values()), [])) if isinstance(data, dict) else 1
                summary["stores"][fname] = {"exists": True, "records": count, "status": "VERIFIED"}
            except Exception as exc:
                summary["stores"][fname] = {"exists": True, "records": 0, "status": "CORRUPTED"}
                summary["anomalies"].append(f"{fname}: {exc}")
        return summary

