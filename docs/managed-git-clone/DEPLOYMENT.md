# Triển khai trong staging

1. Chuẩn bị một Docker daemon staging riêng, tài nguyên CPU-only, dữ liệu giả lập và storage test. Chạy `LABOPS_STAGING_DOCKER_HOST=unix:///path/to/staging/docker.sock bash infrastructure/workspace-image/test-managed-git-image.sh` từ repo root.
2. Build từ repo root với `docker build -f infrastructure/workspace-image/Dockerfile -t labops-workspace:managed-git .` trên daemon staging. Dùng Go builder đúng toolchain trong `go.mod`. Release có thể truyền `--build-arg GO_BUILDER_IMAGE=golang:1.26.11-trixie@sha256:<digest>`. Ghi lại image ID từ Docker inspect hoặc digest khi đẩy vào registry. Cấu hình `LABOPS_WORKSPACE_IMAGE_REF` và `LABOPS_WORKSPACE_IMAGE_DIGEST` bằng giá trị immutable đã kiểm tra.
3. Trên Backend staging, cấu hình `LABOPS_REPOSITORY_API_URL=https://<broker>/api/v1/repositories`. Endpoint HTTPS phải truy cập được từ workspace và dùng certificate được image tin cậy. Backend gọi Agent nội bộ qua `LABOPS_AGENT_URL` hoặc fallback loopback hiện có. Đồng bộ `LABOPS_AGENT_SECRET` và `LABOPS_BROKER_VERIFY_SECRET` theo cơ chế secret hiện có. Căn khớp Backend local host ID, Agent server ID và Docker registry. Agent chỉ cần listen nội bộ.
4. Cài Trivy và xác minh database trên staging. Đặt `LABOPS_REPO_GUARD_DATA_DIR` trên filesystem staging có dung lượng giới hạn riêng để lịch sử Git và quarantine không chiếm ổ hệ thống. Cấu hình `LABOPS_REPOSITORY_DELIVERY_STAGING` ở một thư mục do service Agent sở hữu, mode `0700`, bên ngoài các mount của người dùng và trên cùng filesystem với workspace staging. Agent cần quyền đi qua và ghi thư mục cha trong storage test và quyền gán UID/GID cho tệp của chính nó. Với Agent non-root, cấu hình quyền này trong môi trường staging bằng `CAP_CHOWN` dành riêng cho service và quyền filesystem giới hạn theo thư mục. Workspace tiếp tục chạy non-root, drop toàn bộ capability.
5. Tạo **workspace staging mới** với identity thử nghiệm. Xác minh mount credential read-only và mode token mà không in nội dung. Từ network namespace của workspace, kiểm tra DNS, TLS và truy cập Broker, sau đó chạy `git clone` với repository test, `git status` tại repo đã bàn giao, `git log`, `git remote -v` và nhánh đúng theo yêu cầu. Chạy cả trường hợp xét duyệt thủ công và từ chối. Dùng repo thử có nhiều commit để xác minh lịch sử.
6. Khi token hết hạn, dùng phiên Admin gọi `POST /api/v1/repositories/credentials/{workspace_id}` với member ID và JTI cũ nếu cần thu hồi khi cấp lại. Giữ response trong tệp tạm riêng mode `0600`. Ghi token mới vào tệp tạm **trong thư mục credential phía host của workspace**, đặt mode `0600` và owner đúng UID/GID lấy từ trusted registry, rồi rename thay `repository-token`. Directory bind mount sẽ thấy inode mới. Thực hiện qua quy trình quản trị đã phê duyệt và không in token lên terminal hoặc đưa vào command line. 
7. Rollback dành cho các lần provisioning tiếp theo bằng cách bỏ cấu hình Managed Git ở Backend và chọn lại image digest cũ. Thu hồi JTI đã cấp khi cần. Các workspace đã tạo giữ image hiện tại cho đến khi có kế hoạch thay thế được phê duyệt.

Các lệnh build và kiểm chứng chỉ chạy trên staging. Việc cấu hình hoặc triển khai production là bước thay đổi riêng của Admin.

## Ví dụ override cho service Agent của staging

Dùng socket và cây lưu trữ của staging trong override tương ứng với service thử nghiệm.

```ini
[Service]
Environment=LABOPS_REPO_GUARD_DATA_DIR=/srv/labops-stage/guard
Environment=LABOPS_REPOSITORY_DELIVERY_STAGING=/srv/labops-stage/storage/.delivery
Environment=DOCKER_HOST=/run/labops-stage/docker.sock
AmbientCapabilities=CAP_CHOWN
CapabilityBoundingSet=CAP_CHOWN
ReadWritePaths=/srv/labops-stage/guard /srv/labops-stage/storage
```

Tạo `.delivery` mode `0700` thuộc Agent. Cấp quyền traverse và ghi cho Agent theo ACL trên các parent staging cần publish. Kiểm chứng mount và ownership từ một workspace staging mới trước khi phát hành. Giữ daemon, database và workspace runtime directory của staging tách khỏi cấu hình đang phục vụ nghiên cứu.
