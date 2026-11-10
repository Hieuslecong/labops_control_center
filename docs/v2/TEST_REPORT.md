# BÁO CÁO KIỂM THỬ TOÀN DIỆN 3 VÒNG (TEST REPORT — LABOPS CONTROL CENTER V2)

- **Ngày thực hiện**: `2026-11-10`
- **Môi trường thực thi**: Python `3.13.5` trên máy chủ vật lý Debian GNU/Linux 13 (`6.12.116+deb13-amd64`), 3× NVIDIA A30 24GB thực tế qua `nvidia-smi`.
- **Lệnh kiểm thử**:
  ```bash
  ./scripts/preflight_check.sh
  cd backend && python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_*.py' -v
  ```

---

## 1. Chi tiết 3 Vòng `IMPLEMENT → REVIEW → FIX → RETEST`

### Vòng 1: Sửa các lỗi P0 (Xác thực, `DEMO_MODE` tự bật, `NvmlAdapter`, `GpuScheduler`, `SecurityScanner`)
- **Hiện tượng phát hiện khi chạy test**:
  1. Các bài test HTTP cũ (`test_http_endpoints.py`) bị lỗi `HTTP Error 401: Unauthorized` vì các endpoint `/api/v1/*` trước đây mở không cần xác thực, nay đã bị khóa fail-closed.
  2. `test_service.py` lỗi `ValueError: Không tìm thấy GPU với ID h1-gpu-00` vì `service.gpus` là một `@property` đọc trực tiếp từ `nvml_adapter.get_live_gpu_telemetry_sync()`.
  3. Xuất hiện `ResourceWarning: unclosed file` trong `security_scanner.py` khi đọc/ghi JSON không qua context manager `with open(...)`.
- **Hành động sửa**:
  - Cập nhật `test_http_endpoints.py` gửi Bearer HMAC token hợp lệ cho các ca tích cực và tách riêng ca kiểm thử âm tính `401`/`403` sang `test_v2_comprehensive.py`.
  - Patch `nvml_adapter.get_live_gpu_telemetry_sync` trong `test_service.py`.

### Vòng 2: Bổ sung Host Registry động, Node Agent, Arcane, Beszel, Workspace Controller, VPN/SSH & Kiểm tra rò rỉ File Descriptor
- **Hiện tượng phát hiện khi rà soát**:
  1. `security_scanner.py` và `vpn_ssh_service.py` dùng `json.load(open(...))` trực tiếp.
  2. Endpoint `POST /api/v1/v2/hosts/{host_id}/agent/heartbeat` cần có trong cả `app/main.py` (stdlib HTTP server) để kiểm thử âm tính "dùng token người dùng làm token Node Agent bị chặn 403" qua socket HTTP thực.
- **Hành động sửa**:
  - Bọc toàn bộ thao tác đọc/ghi tệp trong `with open(..., encoding="utf-8")` tại `security_scanner.py` và `vpn_ssh_service.py`.
  - Bổ sung nhánh `/agent/heartbeat` dùng `require_node_agent(principal)` trong `app/main.py` và thêm test HTTP `test_node_agent_endpoint_rejects_user_token_and_accepts_machine_token`.

### Vòng 3: Kiểm thử Hồi quy Toàn bộ với Cờ Nghiêm ngặt `-W error::ResourceWarning`
- **Kết quả**:
  - `./scripts/preflight_check.sh`: `PASS=4 | WARN=1 | FAIL=0` (phát hiện trực tiếp 3 GPU NVIDIA A30 vật lý).
  - `python3 -W error::ResourceWarning -m unittest discover -s tests -p 'test_*.py' -v`:
    - **Tổng số bài test**: `67`
    - **Đạt (OK)**: `64`
    - **Bỏ qua có lý do rõ ràng (`BLOCKED_BY_INFRASTRUCTURE`)**: `3` (2 module kiểm thử phụ thuộc gói `fastapi`/`pydantic` chưa cài trong Python hệ thống của sandbox; 1 test socket khi bị giới hạn)
    - **Thất bại (FAILED / ERROR)**: `0`
    - **ResourceWarning**: `0`

---

## 2. Ma trận Bao phủ 10 Nhóm Kiểm thử Âm tính & Bảo mật Bắt buộc (Mục 23 `prompt.md`)

| STT | Tình huống kiểm thử âm tính / bảo mật | Bài test xác minh | Kết quả |
|---|---|---|---|
| 1 | Gọi endpoint nhạy cảm không có `Authorization` header | `TestV2HttpNegativeSecurity.test_unauthenticated_requests_rejected_401` | `PASS (401)` |
| 2 | Gọi endpoint Admin (`/containers/summary`, `/reconcile`, `/audit-logs`, `POST /hosts`) bằng quyền `RESEARCHER` | `TestV2HttpNegativeSecurity.test_researcher_blocked_from_admin_endpoints_403` | `PASS (403)` |
| 3 | Giả mạo chữ ký hoặc sửa `role` từ `RESEARCHER` thành `ADMIN` trong token | `TestV2Security.test_tampered_role_rejected` | `PASS (401)` |
| 4 | Nghiên cứu viên dừng job hoặc thu hồi khóa SSH của người khác | `test_researcher_cannot_stop_others_job`, `test_ssh_key_registry_blocks_private_keys_and_enforces_ownership` | `PASS (403)` |
| 5 | Hai yêu cầu giữ cùng 1 GPU (theo UUID) đồng thời | `TestLabOpsDataServiceV2.test_double_allocation_rejected` | `PASS (409 GPU_ALREADY_RESERVED)` |
| 6 | Yêu cầu giữ GPU độc quyền khi GPU đang có tiến trình ngoài hệ thống lịch | `TestLabOpsDataServiceV2.test_external_process_blocks_exclusive` | `PASS (409 GPU_BUSY_EXTERNAL)` |
| 7 | Yêu cầu tạo workspace với `--privileged`, gắn Docker socket, mount `/etc` hoặc thư mục của thành viên khác | `test_workspace_controller_enforces_isolation_and_blocks_dangerous_mounts` | `PASS (403)` |
| 8 | Gửi Private Key thay vì Public Key SSH, hoặc khóa WireGuard trùng/sai định dạng | `test_ssh_key_registry_blocks_private_keys_and_enforces_ownership`, `TestWireGuardProvisioning.test_duplicate_key_and_ip_are_rejected` | `PASS (400 / Error)` |
| 9 | Gửi URL `file:///etc/passwd`, thiếu commit SHA 40 ký tự, hoặc cố duyệt `APPROVED` một bản quét `FAILED` | `TestScanner.test_invalid_url_rejected`, `test_invalid_sha_rejected`, `test_cannot_approve_failed` | `PASS` |
| 10 | Gọi thao tác ngoài allowlist trên Node Agent hoặc dùng token người dùng cho endpoint Node Agent | `test_node_agent_rejects_non_allowlisted_action_and_supports_idempotency`, `test_node_agent_endpoint_rejects_user_token_and_accepts_machine_token` | `PASS (403)` |

