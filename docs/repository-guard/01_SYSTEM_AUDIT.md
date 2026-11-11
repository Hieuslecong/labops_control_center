# LABOPS REPOSITORY GUARD — SYSTEM AUDIT REPORT (PHASE 1)
**Date:** 2026-11-11  
**Auditor:** Principal DevSecOps & Security Architect  
**Branch:** `feature/labops-control-center`  
**System:** Debian 13 Host (Control Plane & Node Agent)  

---

## 1. MỤC TIÊU VÀ PHẠM VI KIỂM TOÁN

Kiểm toán toàn diện các thành phần bảo mật, quét mã nguồn, quản lý workspace và luồng phân phối mã hiện có của LabOps Control Center để xác định:
1. Các thành phần đã có và có thể tái sử dụng.
2. Các chức năng còn thiếu để triển khai **Automated Repository Security Gateway (Auto Security Bot)**.
3. Các lỗ hổng và rủi ro kiến trúc đã phát hiện trong mã nguồn hiện hành.
4. Cơ sở hạ tầng runtime (Trivy, OpenSSF Scorecard, Docker, Go Agent).

---

## 2. HIỆN TRẠNG KIẾN TRÚC & MÃ NGUỒN

### 2.1. Backend API & Scanner Hiện Có
- **`backend/app/services/security_scanner.py` (V2 Scanner)**:
  - Sử dụng state machine: `SUBMITTED -> FETCHING -> SCANNING -> POLICY_EVALUATION -> (PASSED | REVIEW_REQUIRED | REJECTED | FAILED)`.
  - Validate repo URL qua Regex (`github.com` hoặc `gitlab.com`).
  - Yêu cầu commit SHA 40 hex ký tự.
  - Kiểm tra Trivy cục bộ (`trivy repo --scanners vuln,secret,misconfig`).
  - Cache key dựa trên `repo_url|commit_sha|scanner_version|db_version|policy_version`.
  - Lưu trạng thái vào file JSON có file locking (`fcntl.flock`).
- **`backend/app/api/v1/endpoints/security.py`**:
  - Endpoint cũ `/api/v1/security/reviews`: Tạo review giả lập trong demo mode (`verified_clean: True`), không thực hiện quét thực tế.
  - Endpoint V2 `/api/v1/security/scans`: Tương tác trực tiếp với `SecurityScanner` qua `list_scans`, `submit_scan`, `decide_scan`.
- **`backend/app/services/workspace_controller.py`**:
  - Quản lý Docker workspaces của Researcher với các ràng buộc bảo mật: cấm `--privileged`, cấm mount docker socket, cấm mount thư mục hệ thống hoặc mount chéo giữa các thành viên.
  - Chưa có cơ chế chặn outbound git clone đến internet công cộng hoặc tích hợp repository guard delivery.
- **`backend/app/adapters/node_agent_adapter.py`**:
  - Giao tiếp với Go Node Agent qua machine token (`X-LabOps-Agent-Secret`).
  - Sử dụng allowlist các actions: `INSPECT_HOST`, `INSPECT_GPUS`, `VERIFY_PROCESS_PID`, `PROVISION_DOCKER_WORKSPACE`, `STOP_DOCKER_WORKSPACE`, `SYNC_SSH_AUTHORIZED_KEYS`, `INSPECT_WIREGUARD_PEERS`.

### 2.2. Go Node Agent (`go/`)
- Agent viết bằng Go đang chạy trên port 9100 (`127.0.0.1:9100`).
- Đã có các collectors: `host`, `gpu` (NVML/nvidia-smi), `process` (procfs), `docker` controller.
- Chưa có module `repositoryguard` chuyên trách tải quarantine, đánh giá policy, cache artifact và phân phối mã vào workspace.

### 2.3. Frontend Dashboard (`src/app/dashboard/security/` & `src/features/security/`)
- Giao diện `SecurityManagementView` hiển thị các thẻ KPI và bảng reviews/scans.
- Hiện có modal gửi quét mã nguồn gửi đến `/api/v1/security/scans`.
- Chưa có giao diện chi tiết phân loại rủi ro chi tiết, OpenSSF Scorecard, audit log phân phối artifact và công cụ cho Researcher (`labops clone`).

### 2.4. Công Cụ Runtime Thực Tế Trên Máy Chủ
- **Trivy**: Đã cài đặt tại `/usr/bin/trivy` (Phiên bản `0.76.2`, Vulnerability DB đã tải cập nhật 2026-11-10).
- **OpenSSF Scorecard**: Chưa cài đặt trong `$PATH`.
- **Git**: Đã cài đặt tại `/usr/bin/git`.
- **Docker**: Hoạt động bình thường với NVIDIA Container Toolkit.

---

## 3. CÁC LỖ HỔNG & VẤN ĐỀ KIẾN TRÚC ĐÃ XÁC MINH

### Vấn đề A: Legacy Security Reviews tạo kết quả giả lập
- **Vị trí**: `backend/app/api/v1/endpoints/security.py` (`POST /reviews`).
- **Mô tả**: Khi gọi `/reviews`, backend tự động sinh một bản ghi review có `status="APPROVED"`, `risk_score=5`, `verified_clean=True` mà không chạy bất kỳ công cụ quét nào.
- **Khắc phục**: 
  - Vô hiệu hóa việc cấp quyền dựa trên legacy `/reviews`.
  - Giữ endpoint `/reviews` chỉ cho mục đích đọc lịch sử legacy nếu cần, nhưng không sử dụng dữ liệu này để cho phép tải hoặc phân phối mã vào workspace.
  - Chuyển toàn bộ luồng phê duyệt sang Repository Guard V2 engine dựa trên commit SHA thực tế.

### Vấn đề B: Trivy Scanner Fallback làm sai lệch Coverage
- **Vị trí**: `backend/app/services/security_scanner.py` (hàm `_run_trivy`, dòng 136-140).
- **Mô tả**: Khi lệnh `trivy repo --scanners vuln,secret,misconfig` gặp lỗi (`CalledProcessError`), code tự động fallback sang `trivy repo --scanners secret,misconfig` và sau đó `evaluate_policy` vẫn có thể đánh giá là `PASSED` nếu không có secret/misconfig, trong khi lỗ hổng dependency (`vuln`) hoàn toàn bị bỏ qua!
- **Khắc phục**:
  - Không bao giờ tự động chuyển thành `PASSED` khi một loại quét bắt buộc thất bại.
  - Phải có trường `scan_coverage` rõ ràng (ví dụ: `vulnerability_scan: FAILED/SKIPPED`).
  - Nếu `vuln` bị lỗi, trạng thái phải chuyển thành `REVIEW_REQUIRED` hoặc `FAILED`, ghi rõ lý do scan coverage không đầy đủ (Fail-Closed).

### Vấn đề C: Sử dụng Python Background Daemon Threads
- **Vị trí**: `backend/app/services/security_scanner.py` (dòng 169: `threading.Thread(target=self._process, daemon=True).start()`).
- **Mô tả**: Khi backend restart (hoặc worker crash), các thread daemon đang chạy bị hủy bỏ đột ngột, khiến các bản quét rơi vào trạng thái treo vô thời hạn (`SUBMITTED`, `FETCHING` hoặc `SCANNING`).
- **Khắc phục**:
  - Xây dựng Durable Job Queue lưu trạng thái xuống persistent store (JSON file có transactional atomic write hoặc SQLite queue).
  - Có cơ chế phục hồi khi khởi động (re-queue các job bị treo lúc restart).
  - Hỗ trợ lease, timeout, retry có kiểm soát và idempotency.

### Vấn đề D: Thiếu Cơ Chế Cưỡng Chế Git Clone (No Mandatory Clone Enforcement)
- **Vị trí**: Docker Workspace runtime.
- **Mô tả**: Mặc dù có security scanner, các container của Researcher hiện vẫn có thể tự do chạy `git clone https://github.com/...` trực tiếp nếu container có kết nối internet ra ngoài.
- **Khắc phục**:
  - Xây dựng CLI `labops clone` và cơ chế phân phối verified immutable source archive/directory vào workspace.
  - Thí điểm (Pilot) Network Egress Policy trên 1 workspace thử nghiệm để chặn trực tiếp các egress đến GitHub IP/port (443, 22) từ container, chỉ cho phép tiếp nhận mã qua LabOps Repository Guard.

---

## 4. MA TRẬN TÁI SỬ DỤNG VÀ KẾ HOẠCH NÂNG CẤP

| Thành phần | Hiện trạng | Hành động yêu cầu |
|---|---|---|
| **Trivy Static Scan** | Có trong Python (`security_scanner.py`), Trivy 0.76.2 có sẵn trên host | Sửa lỗi fallback fail-closed, bổ sung scan coverage manifest |
| **OpenSSF Scorecard** | Chưa có | Tích hợp module gọi Scorecard CLI / GitHub API có rate-limit handling & trạng thái `UNKNOWN` an toàn |
| **Quarantine Downloader** | Chưa có độc lập (Trivy tự clone) | Tạo quarantine isolated sandbox có quota dung lượng, no-exec, kiểm tra symlink traversal |
| **Go Repository Guard** | Chưa có module Go | Xây dựng `go/internal/repositoryguard/` tích hợp vào Go Agent / Control Plane |
| **Policy Engine** | Đơn giản trong Python (`evaluate_policy`) | Nâng cấp State Machine chuẩn: `AUTO_APPROVED`, `REVIEW_REQUIRED`, `REJECTED`, `FAILED` |
| **Approved Cache Store** | Cache key đơn giản trong JSON | Approved Content-Addressed Store lưu digest SHA-256 của source snapshot |
| **Durable Queue** | Python daemon thread | Durable queue có file-backed state, lease, retry, idempotency |
| **Researcher CLI** | Chưa có | Xây dựng CLI command `labops clone` hoặc API endpoint tương ứng |
| **Admin Dashboard UI** | Có trang `SecurityManagementView` cơ bản | Nâng cấp giao diện hiển thị KPI, danh sách yêu cầu, drawer chi tiết, nút duyệt ngoại lệ có RBAC |
| **Network Egress Enforcement** | Chưa có | Triển khai Pilot iptables / Docker network egress rule trên 1 container thí điểm |

---

## 5. KẾT LUẬN & BƯỚC TIẾP THEO

Hệ thống có nền tảng tốt (Trivy 0.76.2 đã sẵn sàng, Go Agent hiệu năng cao đang chạy, Next.js dashboard và Clerk RBAC đã hoạt động).
Trình tự thực hiện an toàn tiếp theo:
1. **Khắc phục lỗi bảo mật Scanner hiện có** (`security_scanner.py`, `security.py`).
2. **Thiết kế kiến trúc chi tiết** (`docs/repository-guard/02_ARCHITECTURE.md`).
3. **Phát triển module Go `repositoryguard` & Durable Queue**.
4. **Phát triển Quarantine Downloader & Policy Engine**.
5. **Cập nhật Admin UI & API & CLI**.
6. **Thử nghiệm Network Egress Pilot & Kiểm thử bảo mật**.
