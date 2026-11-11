"""
VPN Provider Abstraction Layer for LabOps Control Center.
Hỗ trợ nhiều cơ chế VPN (wg-easy v15 containerized và WireGuard host legacy).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class VPNProviderError(RuntimeError):
    """Lỗi phát sinh khi thao tác với VPN Provider."""
    pass


class VPNProvider(ABC):
    """
    Interface chuẩn cho mọi nhà cung cấp VPN trong hệ thống LabOps Control Center.
    Đảm bảo tính độc lập và khả năng thay thế linh hoạt (wg-easy, legacy wg0, tailscale/headscale nếu mở rộng).
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Tên định danh của provider (ví dụ: 'wg-easy', 'legacy-wg0')."""
        pass

    @abstractmethod
    def list_devices(self) -> List[Dict[str, Any]]:
        """Lấy danh sách thiết bị/peers đang được quản lý bởi provider này."""
        pass

    @abstractmethod
    def create_device(
        self,
        name: str,
        expires_at: Optional[str] = None,
        public_key: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Tạo mới một thiết bị VPN client."""
        pass

    @abstractmethod
    def get_device(self, device_id: str) -> Optional[Dict[str, Any]]:
        """Lấy chi tiết một thiết bị theo ID."""
        pass

    @abstractmethod
    def disable_device(self, device_id: str) -> bool:
        """Tạm vô hiệu hóa kết nối của thiết bị."""
        pass

    @abstractmethod
    def enable_device(self, device_id: str) -> bool:
        """Kích hoạt lại kết nối của thiết bị."""
        pass

    @abstractmethod
    def revoke_device(self, device_id: str) -> bool:
        """Thu hồi và xóa vĩnh viễn thiết bị khỏi VPN server."""
        pass

    @abstractmethod
    def get_connection_status(self) -> Dict[str, Any]:
        """Lấy thông tin tổng quan trạng thái máy chủ VPN, endpoint, subnet và sức khỏe dịch vụ."""
        pass

    @abstractmethod
    def get_traffic_statistics(self) -> Dict[str, Any]:
        """Lấy thống kê lưu lượng mạng Rx/Tx và số lượng phiên kết nối."""
        pass

    @abstractmethod
    def get_client_configuration(self, device_id: str) -> str:
        """Lấy nội dung tệp cấu hình WireGuard (.conf) hoàn chỉnh để cấp phát cho thành viên."""
        pass

    @abstractmethod
    def reconcile_state(self) -> Dict[str, Any]:
        """Đối soát và đồng bộ trạng thái thực tế giữa VPN server và cơ sở dữ liệu LabOps."""
        pass

