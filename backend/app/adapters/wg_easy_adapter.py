"""
WgEasyAdapter: Bộ điều hợp giao tiếp REST API với wg-easy v15.
Cung cấp toàn bộ tính năng quản trị WireGuard VPN tự động:
- Khởi tạo và cấp phát Client
- Lấy tệp cấu hình .conf dùng ngay
- Quản lý kích hoạt/vô hiệu hóa/thu hồi peer
- Giám sát lưu lượng Rx/Tx và thời điểm Handshake thực tế
- Tự động đảm bảo subnet 10.88.40.0/24 được áp dụng
"""
from __future__ import annotations

import base64
import ipaddress
import json
import logging
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.adapters.vpn_provider import VPNProvider, VPNProviderError
from app.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_HANDSHAKE_ONLINE_TTL_SECONDS = 180


def _parse_handshake_age_seconds(raw_ts: Any, now_ts: Optional[float] = None) -> Optional[int]:
    """Chuyển đổi timestamp handshake (ISO-8601 hoặc epoch s/ms) thành số giây đã trôi qua."""
    if raw_ts is None or raw_ts == "" or raw_ts == 0:
        return None
    ts: Optional[float] = None
    if isinstance(raw_ts, (int, float)):
        val = float(raw_ts)
        if val <= 0:
            return None
        ts = val / 1000.0 if val > 1e12 else val
    elif isinstance(raw_ts, str):
        s = raw_ts.strip()
        if not s or s == "0":
            return None
        try:
            num = float(s)
            if num <= 0:
                return None
            ts = num / 1000.0 if num > 1e12 else num
        except ValueError:
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                ts = dt.timestamp()
            except ValueError:
                return None
    if ts is None:
        return None
    ref = now_ts if now_ts is not None else time.time()
    return int(max(0.0, ref - ts))


class WgEasyAdapter(VPNProvider):
    def __init__(
        self,
        base_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        public_endpoint: Optional[str] = None,
        vpn_subnet: Optional[str] = None,
        container_name: Optional[str] = None,
        timeout: int = 5,
    ) -> None:
        self.base_url = (base_url or getattr(settings, "WG_EASY_API_URL", "http://127.0.0.1:51821")).rstrip("/")
        self.username = username or getattr(settings, "WG_EASY_ADMIN_USER", "admin")
        self.password = password if password is not None else getattr(settings, "WG_EASY_ADMIN_PASSWORD", "")
        self.public_endpoint = (
            public_endpoint
            if public_endpoint is not None
            else getattr(settings, "WG_EASY_PUBLIC_ENDPOINT", "")
        )
        self.vpn_subnet = vpn_subnet or getattr(settings, "WG_EASY_VPN_SUBNET", "10.88.40.0/24")
        self.container_name = container_name or getattr(settings, "WG_EASY_CONTAINER_NAME", "labops-wg-easy")
        self.timeout = timeout
        self.online_ttl_seconds = max(
            30,
            int(os.getenv("WG_EASY_ONLINE_TTL_SECONDS", str(DEFAULT_HANDSHAKE_ONLINE_TTL_SECONDS))),
        )
        self._circuit_open_until = 0.0
        self._failure_count = 0
        self._cached_interface_info: Optional[Dict[str, Any]] = None

    @property
    def name(self) -> str:
        return "wg-easy"

    def _auth_header(self) -> str:
        raw = f"{self.username}:{self.password}".encode("utf-8")
        return f"Basic {base64.b64encode(raw).decode('ascii')}"

    def _request(
        self,
        path: str,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        is_text_response: bool = False,
    ) -> Any:
        if not self.password:
            raise VPNProviderError("WG_EASY_ADMIN_PASSWORD chưa được cấu hình")
        now = time.time()
        if now < self._circuit_open_until:
            raise VPNProviderError(f"wg-easy API đang tạm ngưng do lỗi kết nối trước đó (chờ thử lại)")

        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {
            "Authorization": self._auth_header(),
            "User-Agent": "LabOps-ControlCenter-V2",
        }
        encoded_data = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            encoded_data = json.dumps(data).encode("utf-8")

        req = Request(url, data=encoded_data, headers=headers, method=method)
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                status = resp.status
                body = resp.read()
                self._failure_count = 0
                if is_text_response:
                    return body.decode("utf-8", errors="replace")
                if not body:
                    return {}
                try:
                    return json.loads(body.decode("utf-8"))
                except json.JSONDecodeError:
                    return body.decode("utf-8", errors="replace")
        except HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else ""
            msg = f"wg-easy API trả về mã lỗi {e.code}: {e.reason}"
            try:
                parsed = json.loads(err_body)
                if isinstance(parsed, dict) and parsed.get("message"):
                    msg = f"{msg} — {parsed['message']}"
            except Exception:
                if err_body:
                    msg = f"{msg} — {err_body[:200]}"
            raise VPNProviderError(msg) from e
        except (URLError, TimeoutError, OSError) as e:
            self._failure_count += 1
            if self._failure_count >= 3:
                self._circuit_open_until = time.time() + 15.0
            raise VPNProviderError(f"Không thể kết nối tới wg-easy tại {self.base_url}: {e}") from e

    def restart_interface(self) -> Dict[str, Any]:
        """Khởi động lại WireGuard interface trong wg-easy qua API chính thức để đồng bộ CIDR/route/hooks."""
        res = self._request("api/admin/interface/restart", method="POST", data={})
        return res if isinstance(res, dict) else {"success": True}

    def get_hooks(self) -> Dict[str, Any]:
        res = self._request("api/admin/hooks", method="GET")
        return res if isinstance(res, dict) else {}

    def update_hooks(self, hooks: Dict[str, Any]) -> Dict[str, Any]:
        res = self._request("api/admin/hooks", method="POST", data=hooks)
        return res if isinstance(res, dict) else {"success": True}

    def get_userconfig(self) -> Dict[str, Any]:
        res = self._request("api/admin/userconfig", method="GET")
        return res if isinstance(res, dict) else {}

    def ensure_subnet_configured(self, restart_on_change: bool = False) -> None:
        """Đồng bộ persisted CIDR (giữ nguyên IPv6 hiện hữu); chỉ restart khi được gọi tường minh."""
        try:
            iface = self._request("api/admin/interface", method="GET")
            if isinstance(iface, dict):
                current_cidr = iface.get("ipv4Cidr", "")
                ipv6_cidr = iface.get("ipv6Cidr") or "fdcc:ad94:bacf:61a4::cafe:0/112"
                if current_cidr != self.vpn_subnet:
                    logger.info(
                        "Cập nhật persisted subnet WireGuard wg-easy từ %s sang %s (restart=%s)",
                        current_cidr,
                        self.vpn_subnet,
                        restart_on_change,
                    )
                    self._request(
                        "api/admin/interface/cidr",
                        method="POST",
                        data={"ipv4Cidr": self.vpn_subnet, "ipv6Cidr": ipv6_cidr},
                    )
                    if restart_on_change:
                        self.restart_interface()
        except Exception as e:
            logger.warning(f"Không thể kiểm tra/cập nhật subnet WireGuard trên wg-easy: {e}")

    def list_devices(self) -> List[Dict[str, Any]]:
        raw_list = self._request("api/client", method="GET")
        if not isinstance(raw_list, list):
            return []
        devices = []
        for c in raw_list:
            if not isinstance(c, dict):
                continue
            enabled = bool(c.get("enabled", True))
            last_handshake_raw = c.get("latestHandshakeAt")
            has_handshake = bool(last_handshake_raw) and enabled
            handshake_age = _parse_handshake_age_seconds(last_handshake_raw) if enabled else None
            is_online = bool(enabled and handshake_age is not None and handshake_age <= self.online_ttl_seconds)
            devices.append({
                "id": str(c.get("id")),
                "provider": self.name,
                "external_id": c.get("id"),
                "name": str(c.get("name") or "Device"),
                "vpn_ip": str(c.get("ipv4Address") or c.get("address") or "").split("/")[0],
                "public_key": str(c.get("publicKey") or ""),
                "enabled": enabled,
                "status": "ACTIVE" if enabled else "DISABLED",
                "handshake_status": "HANDSHAKE_VERIFIED" if has_handshake else "NO_HANDSHAKE",
                "last_handshake": last_handshake_raw,
                "is_online": is_online,
                "handshake_age_seconds": handshake_age,
                "transfer_rx": int(c.get("transferRx") or 0),
                "transfer_tx": int(c.get("transferTx") or 0),
                "created_at": c.get("createdAt"),
                "updated_at": c.get("updatedAt"),
                "expires_at": c.get("expiresAt"),
            })
        return devices

    def create_device(
        self,
        name: str,
        expires_at: Optional[str] = None,
        public_key: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Tạo client trên wg-easy v15. Tự động cấp IP và sinh keypair nếu chưa có."""
        # Không tự đổi CIDR khi tạo peer: đổi CIDR chỉ được thực hiện có kiểm soát (kèm restart interface).
        payload = {"name": name, "expiresAt": expires_at}
        res = self._request("api/client", method="POST", data=payload)
        client_id = res.get("clientId") or res.get("id")
        if not client_id:
            raise VPNProviderError(f"wg-easy không trả về clientId hợp lệ: {res}")
        # Lấy thông tin chi tiết client vừa tạo
        device = self.get_device(str(client_id))
        if not device:
            return {
                "id": str(client_id),
                "provider": self.name,
                "external_id": client_id,
                "name": name,
                "vpn_ip": str(res.get("ipv4Address") or res.get("address") or "").split("/")[0],
                "status": "ACTIVE",
                "enabled": True,
            }
        return device

    def get_device(self, device_id: str) -> Optional[Dict[str, Any]]:
        all_devs = self.list_devices()
        for d in all_devs:
            if str(d.get("id")) == str(device_id) or str(d.get("external_id")) == str(device_id):
                return d
        return None

    def disable_device(self, device_id: str) -> bool:
        res = self._request(f"api/client/{device_id}/disable", method="POST", data={})
        return bool(res.get("success", True))

    def enable_device(self, device_id: str) -> bool:
        res = self._request(f"api/client/{device_id}/enable", method="POST", data={})
        return bool(res.get("success", True))

    def revoke_device(self, device_id: str) -> bool:
        res = self._request(f"api/client/{device_id}", method="DELETE")
        return bool(res.get("success", True))

    def get_client_configuration(self, device_id: str) -> str:
        """Tải file .conf từ wg-easy để đóng gói trực tiếp vào ZIP."""
        conf_text = self._request(f"api/client/{device_id}/configuration", method="GET", is_text_response=True)
        if not conf_text or not isinstance(conf_text, str) or "[Interface]" not in conf_text:
            raise VPNProviderError(f"Không thể lấy tệp cấu hình hợp lệ cho thiết bị {device_id}")
        if self.public_endpoint:
            conf_text = re.sub(r"Endpoint\s*=\s*[^\r\n]+", f"Endpoint = {self.public_endpoint}", conf_text)
        return conf_text

    def inspect_runtime_network(self, interface_name: str = "wg0") -> Dict[str, Any]:
        """Kiểm tra wg0 IPv4 CIDR, kernel route và NAT POSTROUTING thực tế trong container khi docker.sock khả dụng."""
        docker_bin = shutil.which("docker")
        sock_path = "/var/run/docker.sock"
        if not docker_bin or not os.path.exists(sock_path) or not os.access(sock_path, os.R_OK | os.W_OK):
            return {"inspected": False, "reason": "DOCKER_SOCKET_UNAVAILABLE"}
        try:
            addr_p = subprocess.run(
                [docker_bin, "exec", self.container_name, "ip", "-4", "-o", "addr", "show", "dev", interface_name],
                capture_output=True, text=True, timeout=4,
            )
            route_p = subprocess.run(
                [docker_bin, "exec", self.container_name, "ip", "-4", "route", "show", "dev", interface_name],
                capture_output=True, text=True, timeout=4,
            )
            nat_p = subprocess.run(
                [docker_bin, "exec", self.container_name, "iptables", "-t", "nat", "-S", "POSTROUTING"],
                capture_output=True, text=True, timeout=4,
            )
            if addr_p.returncode != 0:
                return {"inspected": False, "reason": f"DOCKER_EXEC_FAILED: {addr_p.stderr.strip()}"}

            runtime_wg_cidr = ""
            m_addr = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+/\d+)", addr_p.stdout)
            if m_addr:
                runtime_wg_cidr = str(ipaddress.ip_network(m_addr.group(1), strict=False))

            runtime_routes = [ln.strip().split()[0] for ln in route_p.stdout.splitlines() if ln.strip()]
            nat_subnets = re.findall(r"-s\s+(\d+\.\d+\.\d+\.\d+/\d+).*?-j\s+MASQUERADE", nat_p.stdout)
            return {
                "inspected": True,
                "runtime_wg_cidr": runtime_wg_cidr,
                "runtime_routes": runtime_routes,
                "nat_subnets": nat_subnets,
            }
        except Exception as exc:
            return {"inspected": False, "reason": f"RUNTIME_PROBE_ERROR: {exc}"}

    def detect_routing_inconsistencies(
        self,
        persisted_cidr: str,
        clients: List[Dict[str, Any]],
        hooks: Optional[Dict[str, Any]] = None,
        runtime_probe: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """So khớp configured CIDR, persisted CIDR, runtime wg0/route/NAT, hooks và IP từng peer."""
        issues: List[str] = []
        state_flags: Dict[str, bool] = {
            "VPN_API_HEALTHY": True,
            "VPN_INTERFACE_MISMATCH": False,
            "VPN_ROUTE_MISMATCH": False,
            "VPN_NAT_MISMATCH": False,
            "VPN_PEER_REGISTERED": len(clients) > 0,
            "VPN_HANDSHAKE_VERIFIED": any(
                bool(c.get("last_handshake")) and c.get("status") == "ACTIVE" for c in clients
            ),
            "VPN_DATA_PATH_VERIFIED": False,
            "VPN_SSH_REACHABLE": False,
        }
        try:
            configured = ipaddress.ip_network(self.vpn_subnet, strict=False)
        except ValueError:
            state_flags["VPN_INTERFACE_MISMATCH"] = True
            return {
                "issues": [f"CONFIGURED_CIDR_INVALID: {self.vpn_subnet}"],
                "state_flags": state_flags,
            }

        effective_net = configured
        if persisted_cidr:
            try:
                persisted_net = ipaddress.ip_network(persisted_cidr, strict=False)
                effective_net = persisted_net
                if persisted_net != configured:
                    state_flags["VPN_INTERFACE_MISMATCH"] = True
                    issues.append(f"VPN_INTERFACE_MISMATCH: configured={configured} persisted={persisted_net}")
            except ValueError:
                state_flags["VPN_INTERFACE_MISMATCH"] = True
                issues.append(f"PERSISTED_CIDR_INVALID: {persisted_cidr}")

        probe = runtime_probe or {}
        if probe.get("inspected"):
            rt_cidr = str(probe.get("runtime_wg_cidr") or "")
            if not rt_cidr:
                state_flags["VPN_INTERFACE_MISMATCH"] = True
                issues.append("VPN_INTERFACE_MISMATCH: runtime wg0 has no IPv4 address")
            else:
                rt_net = ipaddress.ip_network(rt_cidr, strict=False)
                if rt_net != configured or (persisted_cidr and rt_net != ipaddress.ip_network(persisted_cidr, strict=False)):
                    state_flags["VPN_INTERFACE_MISMATCH"] = True
                    issues.append(f"VPN_INTERFACE_MISMATCH: runtime_wg0={rt_net} expected={configured}")

            rt_routes = [str(r) for r in (probe.get("runtime_routes") or [])]
            if str(configured) not in rt_routes:
                state_flags["VPN_ROUTE_MISMATCH"] = True
                issues.append(f"VPN_ROUTE_MISMATCH: missing {configured} in wg0 routes ({rt_routes})")

            nat_subnets = [str(s) for s in (probe.get("nat_subnets") or [])]
            if str(configured) not in nat_subnets:
                state_flags["VPN_NAT_MISMATCH"] = True
                issues.append(f"VPN_NAT_MISMATCH: POSTROUTING MASQUERADE missing {configured} ({nat_subnets})")

        if isinstance(hooks, dict) and "postUp" in hooks:
            post_up = str(hooks.get("postUp") or "")
            if "MASQUERADE" not in post_up:
                state_flags["VPN_NAT_MISMATCH"] = True
                issues.append("VPN_NAT_MISMATCH: persisted postUp hook missing MASQUERADE")
            else:
                for add_match in re.findall(r"-A\s+POSTROUTING\s+[^\n;]*?-s\s+(\d+\.\d+\.\d+\.\d+/\d+)", post_up):
                    try:
                        if ipaddress.ip_network(add_match, strict=False) != configured:
                            state_flags["VPN_NAT_MISMATCH"] = True
                            issues.append(f"VPN_NAT_MISMATCH: postUp hardcodes {add_match} != {configured}")
                    except ValueError:
                        pass

        for c in clients:
            ip = str(c.get("vpn_ip") or "")
            if not ip:
                continue
            try:
                addr = ipaddress.ip_address(ip)
                if addr not in configured or addr not in effective_net:
                    issues.append(f"PEER_IP_OUTSIDE_SUBNET: {c.get('name')} {ip} not in {configured}")
            except ValueError:
                issues.append(f"PEER_IP_INVALID: {c.get('name')} {ip}")

        if (
            probe.get("inspected")
            and not issues
            and state_flags["VPN_PEER_REGISTERED"]
            and state_flags["VPN_HANDSHAKE_VERIFIED"]
        ):
            state_flags["VPN_DATA_PATH_VERIFIED"] = True

        return {"issues": issues, "state_flags": state_flags}

    def get_connection_status(self) -> Dict[str, Any]:
        try:
            iface = self._request("api/admin/interface", method="GET")
            clients = self.list_devices()
            hooks: Optional[Dict[str, Any]] = None
            userconfig: Dict[str, Any] = {}
            try:
                h = self._request("api/admin/hooks", method="GET")
                if isinstance(h, dict):
                    hooks = h
            except Exception:
                hooks = None
            try:
                uc = self._request("api/admin/userconfig", method="GET")
                if isinstance(uc, dict):
                    userconfig = uc
            except Exception:
                userconfig = {}

            iface_name = str(iface.get("name") or "wg0") if isinstance(iface, dict) else "wg0"
            runtime_probe = self.inspect_runtime_network(iface_name)
            active_handshakes = sum(1 for c in clients if c.get("is_online"))
            ever_connected_clients = sum(
                1 for c in clients if c.get("last_handshake") and c.get("status") == "ACTIVE"
            )
            total_rx = sum(int(c.get("transfer_rx", 0)) for c in clients)
            total_tx = sum(int(c.get("transfer_tx", 0)) for c in clients)
            server_pub = iface.get("publicKey") if isinstance(iface, dict) else ""
            persisted_cidr = iface.get("ipv4Cidr", "") if isinstance(iface, dict) else ""

            live_host = str(userconfig.get("host") or "").strip()
            live_port = userconfig.get("port") or (iface.get("port") if isinstance(iface, dict) else None)
            live_endpoint = f"{live_host}:{live_port}" if live_host and live_port else self.public_endpoint

            diag = self.detect_routing_inconsistencies(
                persisted_cidr, clients, hooks=hooks, runtime_probe=runtime_probe
            )
            routing_issues = diag["issues"]
            state_flags = diag["state_flags"]
            inspected = bool(runtime_probe.get("inspected"))

            if routing_issues:
                status = "VPN_ROUTING_MISCONFIGURED"
                service_health = "VPN_ROUTING_MISCONFIGURED"
            elif inspected:
                status = "ACTIVE"
                service_health = "HEALTHY"
            else:
                status = "VPN_API_HEALTHY"
                service_health = "VPN_API_HEALTHY"

            active_states = [k for k, v in state_flags.items() if v]
            if routing_issues and "VPN_ROUTING_MISCONFIGURED" not in active_states:
                active_states.append("VPN_ROUTING_MISCONFIGURED")

            runtime_wg_cidr = str(runtime_probe.get("runtime_wg_cidr") or "")
            effective_subnet = runtime_wg_cidr or persisted_cidr or self.vpn_subnet

            return {
                "provider": self.name,
                "service_health": service_health,
                "routing_state": "VPN_ROUTING_MISCONFIGURED" if routing_issues else ("HEALTHY" if inspected else "VPN_API_HEALTHY"),
                "states": active_states,
                "state_flags": state_flags,
                "routing_issues": routing_issues,
                "configured_cidr": self.vpn_subnet,
                "persisted_cidr": persisted_cidr,
                "runtime_wg_cidr": runtime_wg_cidr,
                "nat_subnets": runtime_probe.get("nat_subnets", []),
                "runtime_inspection_status": "VERIFIED" if inspected else str(runtime_probe.get("reason") or "UNVERIFIED"),
                "status": status,
                "container_running": True,
                "public_endpoint": live_endpoint,
                "vpn_subnet": effective_subnet,
                "interface_name": f"{iface_name} (wg-easy)",
                "server_public_key": server_pub,
                "total_clients": len(clients),
                "active_handshakes": active_handshakes,
                "ever_connected_clients": ever_connected_clients,
                "handshake_ttl_seconds": self.online_ttl_seconds,
                "tunnel_traffic_verified": state_flags["VPN_DATA_PATH_VERIFIED"],
                "total_rx_bytes": total_rx,
                "total_tx_bytes": total_tx,
                "version": "15.x",
                "web_ui_url": self.base_url,
                "last_checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        except Exception as e:
            return {
                "provider": self.name,
                "service_health": "UNAVAILABLE",
                "status": "UNAVAILABLE",
                "states": [],
                "state_flags": {
                    "VPN_API_HEALTHY": False,
                    "VPN_INTERFACE_MISMATCH": False,
                    "VPN_ROUTE_MISMATCH": False,
                    "VPN_NAT_MISMATCH": False,
                    "VPN_PEER_REGISTERED": False,
                    "VPN_HANDSHAKE_VERIFIED": False,
                    "VPN_DATA_PATH_VERIFIED": False,
                    "VPN_SSH_REACHABLE": False,
                },
                "routing_issues": [],
                "container_running": False,
                "public_endpoint": self.public_endpoint,
                "vpn_subnet": self.vpn_subnet,
                "error": str(e),
                "total_clients": 0,
                "active_handshakes": 0,
                "ever_connected_clients": 0,
                "handshake_ttl_seconds": self.online_ttl_seconds,
                "total_rx_bytes": 0,
                "total_tx_bytes": 0,
                "last_checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }

    def get_traffic_statistics(self) -> Dict[str, Any]:
        status = self.get_connection_status()
        return {
            "total_rx_bytes": status.get("total_rx_bytes", 0),
            "total_tx_bytes": status.get("total_tx_bytes", 0),
            "total_clients": status.get("total_clients", 0),
            "active_handshakes": status.get("active_handshakes", 0),
        }

    def reconcile_state(self) -> Dict[str, Any]:
        """Đối soát danh sách client trên wg-easy."""
        clients = self.list_devices()
        return {
            "provider": self.name,
            "status": "RECONCILED",
            "remote_clients_count": len(clients),
            "clients": clients,
        }

