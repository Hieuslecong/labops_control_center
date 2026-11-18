# BÁO CÁO KIỂM THỬ BẢO MẬT VÀ KIỂM TRA TIÊU CỰC (NEGATIVE TESTS)
## 09_SECURITY_TEST_REPORT.md

### 1. Mục tiêu kiểm thử bảo mật

Xác nhận hệ thống không bị rò rỉ quyền hạn, không cho phép truy cập trái phép vào các cổng quản trị hệ thống của host, không cho phép tải chéo cấu hình giữa các thành viên, và tuân thủ chặt chẽ chính sách **Fail-Closed**.

### 2. Chi tiết các ca kiểm thử bảo mật (Negative Security Tests)

| Mã test | Mô tả ca kiểm thử | Kết quả mong đợi | Kết quả thực tế | Trạng thái |
| :--- | :--- | :--- | :--- | :--- |
| **SEC-01** | Thành viên bị đình chỉ (`SUSPENDED`) hoặc thu hồi (`ARCHIVED`) yêu cầu cấp VPN config. | Ném lỗi `MEMBER_INACTIVE` (HTTP 403), từ chối sinh cấu hình. | Bị chặn ngay từ bước đầu của `VPNRouteResolver`. | **PASSED** |
| **SEC-02** | Researcher tải cấu hình thiết bị của người dùng khác (`user_beta` tải của `user_alpha`). | Ném lỗi `FORBIDDEN_CROSS_USER_ACCESS` (HTTP 403). | Bị chặn bởi `WireGuardConfigBuilder`. | **PASSED** |
| **SEC-03** | Researcher chỉ có workspace trên Workstation nhưng yêu cầu route tới máy chủ Rack (`track`). | Ném lỗi `UNAUTHORIZED_HOST_ROUTE` (HTTP 403). | Bị chặn bởi `VPNRouteResolver`. | **PASSED** |
| **SEC-04** | Kiểm tra cấu hình sinh ra cho Researcher không chứa cổng SSH Admin host (`1911`, `1912`, `22`). | Tuyệt đối không xuất hiện cổng Admin trong cấu hình SSH. | Cổng sinh ra nằm trong dải `22010-29999`. | **PASSED** |
| **SEC-05** | Gateway wg-easy mất khóa công khai (`server_pubkey == ""`). | Ném lỗi `GATEWAY_KEY_UNVERIFIED`, từ chối dùng khóa giả/hardcode. | Fail-closed chính xác, chặn sinh cấu hình. | **PASSED** |
| **SEC-06** | Gateway wg-easy mất kết nối mạng (`UNAVAILABLE`). | Ném lỗi `VPN_PROVIDER_UNAVAILABLE`, từ chối fallback về `legacy-wg0`. | Trạng thái UNAVAILABLE, gọi legacy bị chặn `LEGACY_PROVIDER_REMOVED`. | **PASSED** |
| **SEC-07** | Client cố gắng gọi provider `"legacy-wg0"`. | Ném ngoại lệ `VPNProviderError` với code `LEGACY_PROVIDER_REMOVED`. | Chặn tuyệt đối, legacy provider đã bị gỡ bỏ. | **PASSED** |

### 3. Kết luận an ninh

Kiến trúc mới đạt chuẩn an ninh phòng Lab:
- Không tồn tại đường dẫn nào cho phép người dùng thông thường leo thang quyền hạn (Privilege Escalation) lên host SSH port.
- Cơ chế kiểm soát quyền (RBAC) được thi hành nghiêm ngặt ở cả tầng service lẫn tầng endpoint.
- Không có bất kỳ bí mật hay khóa giả lập nào bị rò rỉ.
