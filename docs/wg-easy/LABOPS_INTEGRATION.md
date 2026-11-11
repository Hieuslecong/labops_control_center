# TÍCH HỢP LABOPS CONTROL CENTER VỚI WG-EASY REST API
**Module kết nối**: `backend/app/adapters/wg_easy_adapter.py` & `backend/app/services/vpn_manager.py`

---

## 1. NGUYÊN TẮC THIẾT KẾ (DESIGN PRINCIPLES)

1. **Single Source of Truth**: Mọi thay đổi liên quan đến VPN (tạo thiết bị, vô hiệu hóa, kích hoạt lại, xóa) đều ủy quyền cho `wg-easy`.
2. **Loại bỏ hoàn toàn Shell Execution**: Không chạy các lệnh shell `wg set`, `wg-quick` từ Python process.
3. **Quản lý phiên xác thực an toàn**: `WgEasyAdapter` tự động đăng nhập lấy Session Cookie từ endpoint `/api/session` của `wg-easy` v15, tự động refresh khi hết hạn.
4. **Không phụ thuộc vào Provider cũ**: `LegacyWireGuardProvider` bị khóa ở chế độ READ-ONLY.

---

## 2. ÁNH XẠ CÁC ENDPOINT REST API

| Thao tác LabOps | Phương thức HTTP | Endpoint wg-easy | Chức năng |
| :--- | :--- | :--- | :--- |
| **Đăng nhập session** | `POST` | `/api/session` | Gửi JSON `{"password": "..."}` để nhận Session Cookie |
| **Liệt kê client** | `GET` | `/api/client` | Lấy danh sách client, IP, public key, handshake, Rx/Tx |
| **Tạo client mới** | `POST` | `/api/client` | Body `{"name": "username-device"}` -> Nhận Client ID & IP |
| **Lấy cấu hình .conf** | `GET` | `/api/client/{id}/configuration` | Tải nội dung WireGuard client profile dạng text |
| **Tạm dừng client** | `POST` | `/api/client/{id}/disable` | Vô hiệu hóa peer ngay lập tức trên interface |
| **Kích hoạt lại client** | `POST` | `/api/client/{id}/enable` | Cho phép peer tiếp tục kết nối |
| **Xóa / Thu hồi** | `DELETE` | `/api/client/{id}` | Xóa vĩnh viễn peer khỏi WireGuard |
| **Thông tin Interface** | `GET` | `/api/admin/interface` | Lấy Public Key của server và thông số subnet |

---

## 3. CẤU HÌNH TƯƠNG THÍCH MÔI TRƯỜNG (`backend/app/core/config.py`)

```python
WG_EASY_ENABLED: bool = True
WG_EASY_API_URL: str = "http://127.0.0.1:51821"
WG_EASY_ADMIN_PASSWORD: str = "LabOpsSecureVPN2026!"
WG_EASY_VPN_SUBNET: str = "10.88.40.0/24"
WG_EASY_PUBLIC_ENDPOINT: str = "118.70.169.236:2205"  # hoặc :2204 sau cutover
```

