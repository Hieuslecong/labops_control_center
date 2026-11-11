# BÁO CÁO KIỂM THỬ TÍCH HỢP WG-EASY & SSH VÒNG ĐỜI (TEST REPORT)
**Môi trường kiểm thử**: aiotlab-rack (Debian 13) | Python 3.13.5 | Pytest 8.4.2

---

## 1. TỔNG KẾT KẾT QUẢ KIỂM THỬ

- **Tổng số test cases**: 12
- **Số lượng Passed**: 12 (100%)
- **Số lượng Failed**: 0
- **Thời gian chạy**: 97.71 giây (bao gồm khởi tạo canary SSH server thực tế và kiểm chứng kết nối mạng)

---

## 2. DANH SÁCH CHI TIẾT CÁC CA KIỂM THỬ

### Module 1: `test_v2_vpn_wgeasy.py`
| STT | Tên Test Case | Mô tả kiểm thử | Kết quả |
| :--- | :--- | :--- | :--- |
| 1 | `test_wgeasy_adapter_crud_flow` | Tạo client, lấy config .conf, disable, enable, revoke qua wg-easy REST API | **PASSED** |
| 2 | `test_vpn_manager_multi_device_member` | Cấp phát multi-device cho thành viên, đồng bộ danh sách thiết bị và kiểm tra IP dải 10.88.40.x | **PASSED** |
| 3 | `test_legacy_provider_read_only_protection` | Đảm bảo LegacyWireGuardProvider ném VPNProviderError khi gọi thao tác ghi (bảo vệ tuyệt đối host wg0) | **PASSED** |

### Module 2: `test_admin_ssh_lifecycle.py`
| STT | Tên Test Case | Mô tả kiểm thử | Kết quả |
| :--- | :--- | :--- | :--- |
| 4 | `test_01_researcher_blocked_from_admin_ssh_endpoints` | Researcher bị chặn khỏi API quản trị Admin và chỉ giới hạn thao tác của chính mình | **PASSED** |
| 5 | `test_02_provision_member_ssh_access_schema_and_vault` | Cấp phát tài khoản SSH, sinh khóa Ed25519, lưu vào Vault bộ nhớ và sinh cấu hình WireGuard | **PASSED** |
| 6 | `test_03_member_device_multi_management` | Thêm nhiều thiết bị cho thành viên qua wg-easy, kiểm tra tính toàn vẹn cấu hình | **PASSED** |
| 7 | `test_04_claim_key_bundle_once_and_rotation` | Tải gói khóa chỉ 1 lần duy nhất (One-time claim) và cấp lại cặp khóa mới khi thu hồi khóa cũ | **PASSED** |
| 8 | `test_05_temporary_password_first_login_expiry_reissue` | Mật khẩu tạm hết hạn, bắt buộc đổi mật khẩu lần đầu, cấp lại mật khẩu và chống tái sử dụng | **PASSED** |
| 9 | `test_06_revoke_on_offline_host_stays_pending_revoke` | Thu hồi khóa khi host offline ở trạng thái PENDING_REVOKE, không bao giờ báo giả VERIFIED | **PASSED** |
| 10 | `test_07_real_canary_sshd_passphrase_key_connect_and_revoke` | Khởi chạy canary sshd thật, kết nối SSH bằng Ed25519 có passphrase -> Thu hồi -> Chặn kết nối mới | **PASSED** |
| 11 | `test_08_two_canary_workspaces_isolated_and_runtime_limits` | Kiểm chứng cách ly 0700 giữa 2 workspace và kiểm chứng áp dụng RLIMIT kernel tại runtime | **PASSED** |
| 12 | `test_09_zip_package_download_and_structure` | Tải file ZIP trọn gói `LabOps_Access_<user>.zip`, kiểm tra cấu trúc thư mục, script và config | **PASSED** |

---

## 3. KẾT LUẬN NGHIỆM THU
Hệ thống **LabOps Control Center V2** tích hợp **wg-easy** đã vượt qua toàn bộ các bài kiểm thử tự động và kiểm thử trực tiếp trên hạ tầng máy chủ thực tế aiotlab-rack, đáp ứng 100% các tiêu chí an toàn, không có dữ liệu giả lập (mock status) và sẵn sàng đưa vào vận hành.

