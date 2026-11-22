# Hợp đồng triển khai

| Thành phần | File và trách nhiệm |
|---|---|
| Workspace image | `infrastructure/workspace-image/Dockerfile` build Go đa stage và cài `aiotgit`, alias `gitaiot`, wrapper và Git Debian. `Dockerfile.dockerignore` áp dụng danh sách cho phép cho build context. |
| Wrapper và CLI | `git-wrapper.sh` phân luồng Git. `go/internal/gitaiotcli/cli.go` xử lý cú pháp, token, polling và xác nhận bàn giao. |
| Backend | `repositories.py` xác thực, áp dụng ownership và chuyển đích trong container thành đường dẫn tương đối. `repository_credentials.py` cấp và xác minh credential. `security.py` mở rộng principal. |
| Provisioning | `workspace_controller.py` cấp credential cho workspace mới trên node cục bộ. `node_agent_adapter.py` gửi lệnh provisioning có credential thẳng tới Agent cục bộ. `repository_agent_http.py` cố định đích transport cho các lệnh có credential. `executor.go` giữ idempotency khi token provisioning được cấp lại. `go/internal/docker/{controller,repository}.go` chuẩn bị mount read-only và xác minh Docker identity. |
| Guard | `go/internal/repositoryguard/{github,downloader,security,service,handler,models,policy,workspace_delivery}.go` resolve ref, tải và niêm phong artifact, gắn request với workspace, xác minh và bàn giao. `go/internal/transport/server.go` nối Guard với trusted Docker controller. |
| Build | Installer và `labops_admin_runbook.sh` dùng repo root làm build context. Script `test-managed-git-image.sh` yêu cầu socket staging riêng. |

Các route hiện có dưới `/api/v1/repositories` tiếp tục phục vụ request, list, status, decision và deliver. Managed credential truy cập được `POST /requests`, `GET /requests`, `GET /requests/{id}` và `POST /deliver` theo scope. `POST /credentials/{workspace_id}` phục vụ cấp lại credential bằng phiên Admin. Body chứa `member_id` và tùy chọn `previous_jti`. Response có `token`, `jti`, `expires_at` và `Cache-Control: no-store`. API quyết định review và audit yêu cầu phiên Admin.

CLI giữ tên biến `LABOPS_API_URL`, `LABOPS_TOKEN` và `LABOPS_TOKEN_FILE`. Trong phiên SSH, CLI có thể đọc cấu hình từ thư mục credential đã mount. CLI chạy từ workspace root hoặc thư mục con. Thư mục cha của đường dẫn đích phải tồn tại. Cú pháp nhận HTTPS, một đích tùy chọn, `-b` hoặc `--branch`, cùng `git -C DIR clone` và `git --no-pager clone`. Shallow, partial clone, đệ quy submodule và tùy chọn override cấu hình bị từ chối.

| Exit | Kết quả lệnh clone |
|---:|---|
| 0 | Broker xác nhận đã bàn giao đồng bộ |
| 1 | Lỗi cú pháp, API, xác thực, timeout, trạng thái thiếu xác nhận hoặc bàn giao lỗi |
| 2 | Rejected hoặc revoked |
| 3 | Review required. CLI in lệnh `aiotgit status REQUEST_ID` |
| 4 | Đã duyệt, người gọi CLI trực tiếp chọn `--no-deliver` |

Broker tái sử dụng yêu cầu còn hiệu lực của cùng người, workspace, URL chuẩn hóa và ref khi yêu cầu chưa hoàn tất bàn giao. Yêu cầu đã delivered, expired, failed hoặc revoked có thể tạo lần quét mới. Ad hoc delivery dùng `aiotgit deliver REQUEST_ID DESTINATION`.
