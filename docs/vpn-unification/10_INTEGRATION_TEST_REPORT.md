# BÁO CÁO THỰC THI KIỂM THỬ TÍCH HỢP TOÀN DIỆN (T01 - T12)
## 10_INTEGRATION_TEST_REPORT.md

### 1. Môi trường kiểm thử

- **Hệ điều hành**: Linux x86_64 (Workstation `aiot-ws1`)
- **Python**: 3.13.5 (Virtual Environment `.venv`)
- **Pytest**: 8.4.2 (với asyncio 0.26.0)
- **Tệp kiểm thử**: `backend/tests/test_vpn_unification.py` và `backend/tests/test_v2_vpn_wgeasy.py`
- **Mô hình**: Isolated Unit & Integration Tests với TempDir và Isolated In-Memory Mocks (không xâm phạm host daemon/socket production).

### 2. Bảng kết quả thực thi chi tiết (Execution Results)

| STT | Mã kiểm thử | Tên kiểm thử | Nội dung xác minh | Kết quả | Thời gian |
| :---: | :--- | :--- | :--- | :---: | :---: |
| 1 | **T01** | `test_t01_workstation_route_allowed_ips_and_ssh_port` | AllowedIPs chứa WS IP (`192.168.167.251/32`), KHÔNG chứa Rack IP (`192.168.167.250/32`). SSH Host 192.168.167.251, port workspace, cấm port 1912/1911. | **PASSED** | 1.34s |
| 2 | **T02** | `test_t02_rack_route_allowed_ips_and_ssh_port` | AllowedIPs chứa Rack IP (`192.168.167.250/32`), KHÔNG chứa WS IP (`192.168.167.251/32`). SSH Host 192.168.167.250, port workspace, cấm port 1911/1912. | **PASSED** | 1.28s |
| 3 | **T03** | `test_t03_admin_multi_host_allowed_ips` | Admin nhận đủ dải VPN subnet, Rack IP và Workstation IP (`10.88.40.0/24`, `192.168.167.250/32`, `192.168.167.251/32`). | **PASSED** | 1.35s |
| 4 | **T04** | `test_t04_dynamic_host_ip_resolution_via_registry` | Thay đổi LAN IP máy chủ trong HostRegistry tự động phản ánh vào profile WireGuard mà không cần sửa code. | **PASSED** | 1.25s |
| 5 | **T05** | `test_t05_gateway_public_key_missing_blocks_config` | Gateway thiếu public key -> fail-closed ngay lập tức (`GATEWAY_KEY_UNVERIFIED` hoặc `VPN_PROVIDER_UNAVAILABLE`), cấm fallback hardcode. | **PASSED** | 1.30s |
| 6 | **T06** | `test_t06_gateway_unavailable_rejects_legacy_fallback` | wg-easy offline -> hệ thống báo `UNAVAILABLE`, từ chối fallback về `legacy-wg0` (`LEGACY_PROVIDER_REMOVED`). | **PASSED** | 1.38s |
| 7 | **T07** | `test_t07_ssh_port_isolation_and_workspace_mapping` | Cổng SSH workspace trỏ đúng dải cấp phát (22010-29999), tuyệt đối không gán cổng Admin hệ thống (1911, 1912, 22). | **PASSED** | 1.29s |
| 8 | **T08** | `test_t08_researcher_requesting_unauthorized_host_route_blocked` | Researcher chỉ có workspace trên WS yêu cầu route sang Rack bị ném lỗi `UNAUTHORIZED_HOST_ROUTE` (403). | **PASSED** | 1.36s |
| 9 | **T09** | `test_t09_cross_user_config_download_rejected` | Researcher B tải cấu hình của Researcher A bị chặn `FORBIDDEN_CROSS_USER_ACCESS` (403). | **PASSED** | 1.31s |
| 10 | **T10** | `test_t10_duplicate_device_management` | Cấp nhiều thiết bị cho thành viên sinh ra các IP VPN độc lập, không trùng lặp (`dev1["vpn_ip"] != dev2["vpn_ip"]`). | **PASSED** | 1.41s |
| 11 | **T11** | `test_t11_docker_compose_port_consistency` | File `compose.yaml` không khai báo xung đột đồng thời 2 port 2204 và 2205 trên host. Sử dụng `WG_EASY_UDP_PORT`. | **PASSED** | 0.05s |
| 12 | **T12** | `test_t12_legacy_migration_reconciliation` | Quá trình cấp phát chỉ dùng provider `wg-easy`. Không còn thuộc tính `wireguard_adapter` trong `LabOpsDataService`. | **PASSED** | 1.29s |

### 3. Kết quả bổ sung (Regression Suite `test_v2_vpn_wgeasy.py`)

- `test_01_vpn_manager_overview_and_provider_resolution`: **PASSED**
- `test_02_member_multi_device_management_lifecycle`: **PASSED**
- `test_03_http_endpoints_vpn_and_devices`: **PASSED**
- `test_04_delete_member_permanently_lifecycle`: **PASSED**

**Tổng cộng: 16/16 TESTS ĐẠT 100% PASS.**
