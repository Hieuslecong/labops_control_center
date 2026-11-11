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
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.adapters.vpn_provider import VPNProvider, VPNProviderError
from app.core.config import settings

logger = logging.getLogger(__name__)


class WgEasyAdapter(VPNProvider):
    def __init__(
        self,
        base_url: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        public_endpoint: Optional[str] = None,
        vpn_subnet: Optional[str] = None,
        timeout: int = 5,
    ) -> None:
        self.base_url = (base_url or getattr(settings, "WG_EASY_API_URL", "http://127.0.0.1:51821")).rstrip("/")
        self.username = username or getattr(settings, "WG_EASY_ADMIN_USER", "admin")
        self.password = password or getattr(settings, "WG_EASY_ADMIN_PASSWORD", "LabOpsSecureVPN2026!")
        self.public_endpoint = public_endpoint or getattr(settings, "WG_EASY_PUBLIC_ENDPOINT", "118.70.169.236:2204")
        self.vpn_subnet = vpn_subnet or getattr(settings, "WG_EASY_VPN_SUBNET", "10.88.40.0/24")
        self.timeout = timeout
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

    def ensure_subnet_configured(self) -> None:
        """Đảm bảo CIDR mạng WireGuard trong wg-easy khớp với 10.88.40.0/24."""
        try:
            iface = self._request("api/admin/interface", method="GET")
            if isinstance(iface, dict):
                current_cidr = iface.get("ipv4Cidr", "")
                if current_cidr != self.vpn_subnet:
                    logger.info(f"Cập nhật subnet WireGuard wg-easy từ {current_cidr} sang {self.vpn_subnet}")
                    self._request(
                        "api/admin/interface/cidr",
                        method="POST",
                        data={"ipv4Cidr": self.vpn_subnet, "ipv6Cidr": "fdcc:ad94:bacf:61a4::cafe:0/112"},
                    )
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
            devices.append({
                "id": str(c.get("id")),
                "provider": self.name,
                "external_id": c.get("id"),
                "name": str(c.get("name") or "Device"),
                "vpn_ip": str(c.get("ipv4Address") or "").split("/")[0],
                "public_key": str(c.get("publicKey") or ""),
                "enabled": bool(c.get("enabled", True)),
                "status": "ACTIVE" if c.get("enabled", True) else "DISABLED",
                "last_handshake": c.get("latestHandshakeAt"),
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
        self.ensure_subnet_configured()
        payload = {"name": name, "expiresAt": expires_at}
        res = self._request("api/client", method="POST", data=payload)
        client_id = res.get("clientId")
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
                "status": "ACTIVE",
                "enabled": true,
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

    def get_connection_status(self) -> Dict[str, Any]:
        try:
            iface = self._request("api/admin/interface", method="GET")
            clients = self.list_devices()
            active_handshakes = sum(
                1 for c in clients if c.get("last_handshake") and c.get("status") == "ACTIVE"
            )
            total_rx = sum(int(c.get("transfer_rx", 0)) for c in clients)
            total_tx = sum(int(c.get("transfer_tx", 0)) for c in clients)
            server_pub = iface.get("publicKey") if isinstance(iface, dict) else ""
            return {
                "provider": self.name,
                "service_health": "HEALTHY",
                "status": "ACTIVE",
                "container_running": True,
                "public_endpoint": self.public_endpoint,
                "vpn_subnet": iface.get("ipv4Cidr", self.vpn_subnet) if isinstance(iface, dict) else self.vpn_subnet,
                "interface_name": "wg0 (wg-easy)",
                "server_public_key": server_pub,
                "total_clients": len(clients),
                "active_handshakes": active_handshakes,
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
                "container_running": False,
                "public_endpoint": self.public_endpoint,
                "vpn_subnet": self.vpn_subnet,
                "error": str(e),
                "total_clients": 0,
                "active_handshakes": 0,
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

