# 06 — XÁC MINH DOCKER WORKSPACE & CÔ LẬP TÀI NGUYÊN

- **Branch**: `hardening/labops-v2`
- **Thành phần liên quan**:
  - Go Docker Controller & Trusted Registry: `go/internal/docker/registry.go`, `go/internal/docker/controller.go`, `go/internal/docker/controller_test.go`
  - Python Workspace Controller: `backend/app/services/workspace_controller.py`
  - Database Model: `WorkspaceRecord` (`backend/app/db/models.py`)

---

## 1. Biện pháp Bảo mật & Chuẩn hóa Container Workspace

1. **Trusted Workspace Registry (`go/internal/docker/registry.go`)**:
   - Quản lý trạng thái cấp phát thông qua `TrustedRegistry` (`NewTrustedRegistry`, `WorkspaceRecord`), ghi nhận định danh container chuẩn `IsCanonicalContainerID`.
   - Bắt buộc tham chiếu image bất biến (`IsImmutableImageRef` — yêu cầu digest `@sha256:...` hoặc tag phiên bản cụ thể đã khai báo, từ chối `:latest` hoặc image ngoài danh sách cho phép).
   - Giới hạn đường dẫn lưu trữ (`ValidateSafeStorageRoot`) nhằm chặn bind mount ra `/etc`, `/var/run/docker.sock`, `/root`, `/`.
2. **Kiểm định An toàn Runtime (`ValidateContainerSafety`)**:
   - Đọc kết quả `docker inspect` thực tế (`dockerInspectJSON`) để xác nhận:
     - Không mount host Docker socket (`docker_socket_mounted=false`).
     - Chạy với `non_privileged=true`, `--cap-drop=ALL`, `no-new-privileges`, UID không phải `root`.
     - Chỉ áp dụng giới hạn cgroups CPU, RAM, PIDs theo cấu hình; không tự gắn nhãn `ACTIVE` hoặc `RUNNING` chỉ dựa vào JSON nội bộ.
3. **Python Workspace Controller (`backend/app/services/workspace_controller.py`)**:
   - Kiểm tra tên image, dung lượng quota và cấm các bind mount nguy hiểm (`WorkspaceSecurityError`); cô lập danh sách workspace theo `owner` khi caller là `RESEARCHER`.

---

## 2. Kết quả Kiểm thử

| Suite / Test | Kết quả | Nội dung xác minh |
|---|---|---|
| `go test -race -v ./internal/docker` | **3/3 PASS** | `TestDockerController_ProtectionRules`, `TestDockerController_ListRealWorkspaces`, `TestTrustedRegistryAndDockerProvisioningMatrix` |
| `pytest backend/tests/test_v2_comprehensive.py` (`test_workspace_controller_enforces_isolation_and_blocks_dangerous_mounts`) | **PASS** | Chặn mount `/var/run/docker.sock`, `/etc`, `/`; từ chối cross-user workspace access |
| `pytest backend/tests/test_admin_ssh_lifecycle.py` (`test_02_*`, `test_08_*`) | **PASS** | Host offline hoặc thiếu container kiểm chứng không bao giờ giả mạo trạng thái `READY` |

---

## 3. Hạn chế Hiện tại
- Không khởi tạo hoặc xóa container Docker mới trên daemon production của `aiot-ws1` trong quá trình chạy benchmark/test để bảo vệ môi trường làm việc hiện hữu của các nhà nghiên cứu.
