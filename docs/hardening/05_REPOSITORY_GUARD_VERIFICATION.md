# 05 — XÁC MINH REPOSITORY GUARD & `gitaiot` CLI

- **Branch**: `hardening/labops-v2`
- **Thành phần liên quan**:
  - Go Repository Guard: `go/internal/repositoryguard/{security,github,downloader,scanner,service,delivery,cache,handler,models,policy}.go`
  - Go CLI: `go/internal/gitaiotcli/cli.go`, `go/cmd/gitaiot/main.go`
  - FastAPI Gateway API: `backend/app/api/v1/endpoints/repositories.py`, `backend/app/services/security_scanner.py`
  - Frontend UI: `src/features/security/components/security-management-view.tsx`, `src/features/overview/components/{security-summary-panel,member-repo-history-panel}.tsx`, `src/features/researcher/components/researcher-portal-view.tsx`

---

## 1. Kiến trúc Luồng Tải Mã Nguồn Cách Ly

1. **Chuẩn hóa CLI `gitaiot`**:
   - Loại bỏ toàn bộ chuỗi lệnh cũ (`gitlabops clone`, `labops clone`) ở Frontend (`0` matches còn lại trong `src/` theo kiểm tra của `milestone3-frontend-hardening.test.ts`).
   - `gitaiot` fail-closed khi thiếu token hoặc quyền file cấu hình không an toàn (`TestResolveToken_FailClosedAndPermCheck`).
2. **Kiểm tra URL đa lớp chống SSRF**:
   - **Lớp 1 (FastAPI)**: `_validate_repo_url` trong `backend/app/api/v1/endpoints/repositories.py` chạy ngay đầu `POST /api/v1/repositories/requests` trước `_agent_call`: chỉ cho phép `https://`, hostname thuộc `{"github.com", "gitlab.com"}`, cấm embedded credentials (`user:pass@`), bắt buộc định dạng `owner/repo` sạch không có `..`.
   - **Lớp 2 (Go Repository Guard)**: `NormalizeAndValidateURL` trong `go/internal/repositoryguard/github.go` kiểm tra lại độc lập và chuẩn hóa canonical URL.
3. **Quarantine Git An Toàn**:
   - `SafeGitConfigArgs()` & `SafeGitEnv()` trong `go/internal/repositoryguard/security.go` ép `-c core.hooksPath=/dev/null`, `-c protocol.file.allow=never`, `-c submodule.recurse=false`, tắt LFS smudge/process filters, và đặt `GIT_TERMINAL_PROMPT=0`, `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`.
4. **Đánh giá Trivy & Phân phối Toàn vẹn**:
   - `evaluate_policy` (`security_scanner.py` & `policy.go`) fail-closed thành `REVIEW_REQUIRED` nếu thiếu độ phủ quét (`overall_coverage != "POLICY_COMPLETE"`), và `REJECTED` khi phát hiện secret hoặc lỗ hổng `CRITICAL`.
   - Bundle lưu kèm `RepoManifest` (`commit_sha`, `bundle_sha256`, `policy_version`); bất kỳ thay đổi trái phép nào trong bundle bị từ chối khi delivery (`TestGuardService_StateTransitionsAndTamperDetection`, `TestDeliverArtifact_IntegrityCheck`).
   - `_validate_target_directory` trên FastAPI + danh sách `forbiddenDestPrefixes` trong Go chặn phân phối tới `/etc`, `/root`, `/.ssh`, traversal `..`, hoặc thư mục thuộc member khác.

---

## 2. Kết quả Kiểm thử Xác minh

| Bài test / Nhóm kiểm thử | Kết quả | Chi tiết xác minh |
|---|---|---|
| `go test -race -v ./internal/repositoryguard ./internal/gitaiotcli` | **12/12 PASS** | `TestNormalizeAndValidateURL`, `TestEvaluatePolicy`, `TestApprovedArtifactStore`, `TestDeliverArtifact_IntegrityCheck`, `TestGuardService_CrashRecovery`, `TestArchiveAndDeliverySecurityHardening`, `TestGuardService_StateTransitionsAndTamperDetection`, `TestHTTPHandler_HMACAndRBAC`, `TestGuardService_FullGitBundleE2E`, `TestResolveToken_FailClosedAndPermCheck`, `TestCLI_CloneStatusDeliver_Contract` |
| `TestMilestone4EndToEndAndSecurity::test_repository_guard_ssrf_and_traversal_negative_matrix` | **PASS** | `http://127.0.0.1/...`, `http://169.254.169.254/...`, `http://192.168.167.250/...`, `file:///etc/passwd`, `https://user:pass@github.com/...`, `https://evil.example.com/...` đều trả `400`; `/etc/passwd`, `/root/.ssh`, `/workspaces/bob/repo` trả `403`; traversal `/workspaces/alice/../../etc/cron.d` trả `400` |
| `backend/tests/test_milestone2_hardening.py` (Repository Guard delegation HMAC tests) | **PASS** | Thiếu `LABOPS_AGENT_SECRET` trả `503` fail-closed; yêu cầu hợp lệ gắn đủ header `X-LabOps-Verified-Actor`, `Role`, chữ ký HMAC 64 ký tự hex |
| `backend/tests/test_v2_scanner.py::test_real_trivy_binary_executes_on_aiotlab_rack` | **SKIPPED** | `aiotlab-rack` offline |

---

## 3. Hạn chế Còn lại
- Thực thi `trivy` live trên container/host của node Rack (`aiotlab-rack`) chưa được kiểm tra trực tiếp do node này đang ngoại tuyến.
- Để bảo toàn môi trường nghiên cứu đang chạy, chưa cài đặt chặn egress mức kernel/iptables trên Workstation; kiểm soát được xác minh ở mức BFF, FastAPI, CLI và Go Repository Guard agent.
