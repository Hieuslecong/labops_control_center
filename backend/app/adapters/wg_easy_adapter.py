"""
WgEasyAdapter: Bộ điều hợp giao tiếp REST API với wg-easy v15.
Cung cấp toàn bộ tính năng quản trị WireGuard VPN tự động:
- Khởi tạo và cấp phát Client
- Lấy tệp cấu hình .conf dùng ngay
- Quản lý kích hoạt/vô hiệu hóa/thu hồi peer
- Giám sát lưu lượng Rx/Tx và thời điểm Handshake thực tế
- Kiểm tra subnet mà không thay đổi cấu hình mạng
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
        self.username = username if username is not None else getattr(settings, "WG_EASY_ADMIN_USER", "")
        self.password = password or getattr(settings, "WG_EASY_ADMIN_PASSWORD", "")
        self.public_endpoint = public_endpoint or getattr(settings, "WG_EASY_PUBLIC_ENDPOINT", "118.70.169.236:2204")
        self.vpn_subnet = vpn_subnet or getattr(settings, "WG_EASY_VPN_SUBNET", "10.88.40.0/24")
        self.enabled = bool(getattr(settings, "WG_EASY_ENABLED", True))
        self.timeout = timeout
        self._circuit_open_until = 0.0
        self._failure_count = 0
        self._cached_interface_info: Optional[Dict[str, Any]] = None

    @property
    def name(self) -> str:
        return "wg-easy"

    def _auth_header(self) -> str:
        if not self.username or not self.password:
            raise VPNProviderError("Chưa cấu hình thông tin xác thực wg-easy")
        raw = f"{self.username}:{self.password}".encode("utf-8")
        return f"Basic {base64.b64encode(raw).decode('ascii')}"

    def _request(
        self,
        path: str,
        method: str = "GET",
        data: Optional[Dict[str, Any]] = None,
        is_text_response: bool = False,
    ) -> Any:
        if not self.enabled:
            raise VPNProviderError("wg-easy integration is disabled")
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
            raise VPNProviderError(f"wg-easy API trả về mã lỗi HTTP {e.code}") from e
        except (URLError, TimeoutError, OSError) as e:
            self._failure_count += 1
            if self._failure_count >= 3:
                self._circuit_open_until = time.time() + 15.0
            raise VPNProviderError("Không thể kết nối tới wg-easy API đã cấu hình") from e

    def ensure_subnet_configured(self) -> None:
        """Cảnh báo khi CIDR khác cấu hình; không thay đổi mạng đang hoạt động."""
        iface = self._request("api/admin/interface", method="GET")
        if not isinstance(iface, dict) or not isinstance(iface.get("ipv4Cidr"), str):
            raise VPNProviderError("wg-easy không trả về CIDR hợp lệ")
        if iface["ipv4Cidr"] != self.vpn_subnet:
            logger.warning("CIDR wg-easy khác cấu hình LabOps: thực tế %s, cấu hình %s", iface["ipv4Cidr"], self.vpn_subnet)

    def list_devices(self) -> List[Dict[str, Any]]:
        raw_list = self._request("api/client", method="GET")
        if not isinstance(raw_list, list):
            raise VPNProviderError("wg-easy trả về danh sách thiết bị không hợp lệ")
        devices = []
        seen_ids = set()
        for c in raw_list:
            if not isinstance(c, dict):
                raise VPNProviderError("wg-easy trả về phần tử thiết bị không hợp lệ")
            external_id = c.get("id")
            enabled = c.get("enabled")
            if not isinstance(external_id, (str, int)) or isinstance(external_id, bool) or not str(external_id).strip():
                raise VPNProviderError("wg-easy trả về thiết bị không có ID hợp lệ")
            external_id = str(external_id)
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", external_id) or external_id in seen_ids:
                raise VPNProviderError("wg-easy trả về ID thiết bị không hợp lệ hoặc trùng lặp")
            seen_ids.add(external_id)
            if type(enabled) is not bool:
                raise VPNProviderError("wg-easy trả về trạng thái enabled không hợp lệ")
            counters = []
            for field in ("transferRx", "transferTx"):
                value = c.get(field, 0)
                if type(value) is not int or value < 0:
                    raise VPNProviderError("wg-easy trả về bộ đếm lưu lượng không hợp lệ")
                counters.append(value)
            name = c.get("name", "Device")
            if not isinstance(name, str):
                raise VPNProviderError("wg-easy trả về tên thiết bị không hợp lệ")
            devices.append({
                "id": external_id,
                "provider": self.name,
                "external_id": external_id,
                "name": name or "Device",
                "vpn_ip": str(c.get("ipv4Address") or "").split("/")[0],
                "public_key": str(c.get("publicKey") or ""),
                "enabled": enabled,
                "status": "ACTIVE" if enabled else "DISABLED",
                "last_handshake": c.get("latestHandshakeAt"),
                "transfer_rx": counters[0],
                "transfer_tx": counters[1],
                "created_at": c.get("createdAt"),
                "updated_at": c.get("updatedAt"),
                "expires_at": c.get("expiresAt"),
            })
        return devices

    @staticmethod
    def _client_path(device_id: str, suffix: str = "") -> str:
        if not isinstance(device_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", device_id):
            raise VPNProviderError("wg-easy device ID không hợp lệ")
        return f"api/client/{device_id}{suffix}"

    @staticmethod
    def _check_action_response(response: Any, action: str) -> None:
        if not isinstance(response, dict):
            raise VPNProviderError(f"wg-easy trả về phản hồi {action} không hợp lệ")
        if "success" in response and response.get("success") is not True:
            raise VPNProviderError(f"wg-easy không xác nhận {action} thành công")

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
        client_id = res.get("clientId") if isinstance(res, dict) else None
        if not client_id:
            raise VPNProviderError("wg-easy không trả về clientId hợp lệ")
        # Lấy thông tin chi tiết client vừa tạo
        device = self.get_device(str(client_id))
        if not device:
            raise VPNProviderError("Đã yêu cầu tạo client nhưng chưa xác minh được client trên wg-easy")
        if device.get("enabled") is not True:
            raise VPNProviderError("Client mới tạo chưa được xác minh ở trạng thái enabled")
        return device

    def get_device(self, device_id: str) -> Optional[Dict[str, Any]]:
        all_devs = self.list_devices()
        for d in all_devs:
            if str(d.get("id")) == str(device_id) or str(d.get("external_id")) == str(device_id):
                return d
        return None

    def disable_device(self, device_id: str) -> bool:
        response = self._request(self._client_path(device_id, "/disable"), method="POST", data={})
        self._check_action_response(response, "disable")
        device = self.get_device(device_id)
        if not device or device.get("enabled") is not False:
            raise VPNProviderError("Không xác minh được trạng thái disabled của thiết bị")
        return True

    def enable_device(self, device_id: str) -> bool:
        response = self._request(self._client_path(device_id, "/enable"), method="POST", data={})
        self._check_action_response(response, "enable")
        device = self.get_device(device_id)
        if not device or device.get("enabled") is not True:
            raise VPNProviderError("Không xác minh được trạng thái enabled của thiết bị")
        return True

    def revoke_device(self, device_id: str) -> bool:
        response = self._request(self._client_path(device_id), method="DELETE")
        self._check_action_response(response, "revoke")
        if self.get_device(device_id) is not None:
            raise VPNProviderError("wg-easy vẫn trả về thiết bị sau yêu cầu revoke")
        return True

    def get_client_configuration(self, device_id: str) -> str:
        """Tải file .conf từ wg-easy để đóng gói trực tiếp vào ZIP."""
        conf_text = self._request(self._client_path(device_id, "/configuration"), method="GET", is_text_response=True)
        if not conf_text or not isinstance(conf_text, str) or "[Interface]" not in conf_text:
            raise VPNProviderError(f"Không thể lấy tệp cấu hình hợp lệ cho thiết bị {device_id}")
        return conf_text

    def get_connection_status(self) -> Dict[str, Any]:
        try:
            iface = self._request("api/admin/interface", method="GET")
            if not isinstance(iface, dict) or not isinstance(iface.get("ipv4Cidr"), str) or not iface["ipv4Cidr"].strip():
                raise VPNProviderError("wg-easy không trả về interface schema hợp lệ")
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
                "status": "API_RESPONSIVE",
                "container_running": None,
                "public_endpoint": self.public_endpoint,
                "public_endpoint_source": "configuration",
                "vpn_subnet": iface["ipv4Cidr"],
                "interface_name": iface.get("name"),
                "server_public_key": server_pub,
                "total_clients": len(clients),
                "active_handshakes": active_handshakes,
                "total_rx_bytes": total_rx,
                "total_tx_bytes": total_tx,
                "web_ui_url": self.base_url,
                "last_checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        except Exception as e:
            return {
                "provider": self.name,
                "service_health": "UNAVAILABLE",
                "status": "UNAVAILABLE",
                "container_running": None,
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

