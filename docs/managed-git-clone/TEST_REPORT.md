# Báo cáo kiểm thử

Baseline tại `3cf9bf6` trên nhánh `feature/storage-resource-allocation`. Kiểm thử sử dụng thư mục tạm, loopback và dữ liệu giả lập.

| Trạng thái | Kiểm tra | Kết quả |
|---|---|---|
| CODE_IMPLEMENTED | CLI, wrapper, workspace image, Broker, provisioning và Guard delivery | Source và tài liệu có trong working tree |
| UNIT_TESTED | `go -C go test -count=1 ./...` | Exit 0 |
| UNIT_TESTED | Pytest các test Managed Git, Milestone 2, Milestone 4 và P0 | Exit 0, 41 passed |
| INTEGRATION_TESTED | Go với Git HTTP loopback, scanner fixture, approved cache và workspace filesystem tạm | Exit 0. Kiểm tra commit bất biến, Git history hai commit, source mang thuộc tính export, branch, upstream và reuse request |
| INTEGRATION_TESTED | FastAPI test client và Agent giả lập | Exit 0. Kiểm tra scope, audience, hết hạn, thu hồi, sai member/workspace, decision của Admin và đích tương đối |
| UNIT_TESTED | Native build tĩnh của CLI với lệnh build dùng trong Dockerfile | Exit 0. Cả `aiotgit --version` và `gitaiot --version` trả v2.1.0 |
| BLOCKED_SAFETY | `infrastructure/workspace-image/test-managed-git-image.sh` | Exit 77. Chưa có Docker socket staging riêng. BuildKit, SSH, Trivy thật và luồng HTTPS từ namespace Docker còn cần xác minh |
| RUNTIME_NOT_DEPLOYED | Production | Chưa triển khai |

Test CLI và wrapper bao gồm clone theo branch/đích, global flags, chuyển nguyên tham số và mã thoát lệnh Git thường, review, rejection, unauthorized, timeout, API offline, JSON lỗi và delivery chưa được xác nhận. Test Guard bao gồm traversal, symlink, đích đã tồn tại, sai owner, sai workspace, artifact sai digest và bundle sai commit.

Lần chạy Python đầy đủ trước khi thêm hai test cuối có 215 passed, 3 skipped, 3 failed. Checkout tạm tạo bằng `git archive HEAD` tái hiện đủ ba lỗi baseline gồm một test Gateway thiếu cấu hình Agent/token và hai test startup chỉ sao chép `start.sh` mà thiếu `start_all.sh`.

Go integration dùng UID/GID của test runner. Quyền `CAP_CHOWN` của Agent staging và việc gán UID/GID container thật cần được xác minh trong lần chạy Docker.
