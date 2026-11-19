# BÁO CÁO BÀN GIAO: HARDENING & SỬA LỖI BẢO MẬT LABOPS CONTROL CENTER

- **Host mục tiêu**: Workstation `aiot-ws1` (`192.168.167.251`)
- **Nhánh Git**: `feature/storage-resource-allocation` (base trước khi sửa: `76024f9`)
- **Bản sao lưu dữ liệu trước sửa**: `/tmp/labops-hardening-backup-20261119/` (`chmod 700`, gồm `members.json`, `v2_workspaces.json`, `v2_ssh_keys.json`, `v2_ssh_accounts.json`, `storage_allocations.json`, `ssh_endpoints.json`, `ssh_policies.json`, `inventory_before.txt`)
- **Phạm vi bảo toàn**: WireGuard `labops-wg-easy` (`127.0.0.1:51821`, UDP `2204`, peer `testacc2`), Admin SSH (`:1912`, `:22`), và Rack `192.168.167.250` **được giữ nguyên 100%, không can thiệp**.
- **Trạng thái nghiệm thu hệ thống thực tế (Final Verdict)**: **`NOT_PRODUCTION_READY`** *(xem Mục 5 & Mục 6 về các thao tác `sudo` bắt buộc trên host chưa chạy vì giới hạn đặc quyền sandbox)*.

---

## 1. Phân tích Nguyên nhân gốc rễ (Root Cause Analysis)

1. **P0-1 — Mạo danh tài khoản quản trị host (`aiot-ws1`) & Host `canary_sshd` Fallback**:
   - Trước khi sửa, `backend/app/services/labops_service.py` dùng `getpass.getuser()` trong `_resolve_effective_ssh_login_user()` và gọi `_ensure_local_workspace_sshd_running()` / `start_canary_sshd_for_workspace()`, khởi chạy tiến trình `sshd` trực tiếp trên hệ điều hành host dưới UID `1000` (`aiot-ws1`, thành viên `sudo`).
   - Kiểm tra thực tế trong Phase 0 (`inventory_before.txt` và `ss -tlnp`) ghi nhận 3 tiến trình `sshd` host không qua container còn đang mở: PID `3677306` (`:22003`, `testaccws1`), PID `3824760` (`:22004`, `testacc2`), PID `4092758` (`:22005`, `testaccv3`). Riêng `testacc2` vẫn mở cổng dù `v2_ssh_accounts.json` và 7 khóa trong `v2_ssh_keys.json` đã ở trạng thái `REVOKED` / `PENDING_REVOKE`.
2. **P0-2 — Rời rạc luồng cấp phát & thiếu cô lập Docker Container**:
   - `NodeAgentClient.dispatch` trước đây thiếu luồng gọi HTTP agent cục bộ có xác thực và thiếu hợp đồng `ProvisionWorkspaceSpec` đồng nhất với Go Node Agent.
   - `labops-agent` đang chạy live (PID `57189`, nhị phân cũ trước Phase 2) chưa chạy bằng unit systemd kèm `SupplementaryGroups=docker` và chưa có hành động `SYNC_SSH_AUTHORIZED_KEYS` / `VERIFY_DOCKER_WORKSPACE`.
3. **P0-3 — Thiếu cổng kiểm soát kích hoạt lần đầu, mật khẩu tạm & lộ secret trong ZIP**:
   - Khóa công khai được ghi vào `authorized_keys` ngay lúc admin bấm tạo, không chờ đổi mật khẩu tạm lần đầu; mật khẩu đổi lần đầu thiếu khóa chống brute-force (`locked_until`); `rotate-key` mặc định `require_passphrase=False`; file ZIP có đường dẫn nhúng plaintext `key_passphrase` / `temporary_password`.
   - Khóa do client tự tạo có nguy cơ bị gắn nhầm nhãn `PASSPHRASE_VERIFIED`.
4. **P0-4 — Giả mạo Storage Pool & báo trạng thái `VERIFIED` / `READY` sai sự thật**:
   - `go/internal/storage/provisioner.go` và `inventory.go` mặc định khởi tạo `NewSystemQuotaManager(true)` (chế độ mô phỏng); `CheckSupport` mặc định trả `true` cho XFS không kiểm `/proc/mounts`; `VerifyEnforcement` tin theo `mockStore` ngay cả khi `!m.isSimulated`; `provisioner.go` để `StatusVerified` khi `!pool.QuotaSupported`.
   - `backend/app/services/storage_inventory_service.py` chứa pool giả lập hardcoded cho `track` (`pool-track-zfs-tank`) và fallback `/media/LabOps-Data` (`/dev/sdb1`) cho `workstation`.
   - Kiểm tra thực tế trên `aiot-ws1` cho thấy `/dev/sda2` (`/`), `/dev/sdb`, `/dev/sdc` (`/mnt/HDD3`), `/dev/sdd` đều là `ext4` **chưa bật `prjquota`**.
   - `verify_workspace_and_runtime()` từng đánh dấu `privilege_isolation = "VERIFIED"` chỉ dựa trên kiểm tra thư mục cục bộ và coi cổng TCP host bất kỳ là `ssh_runtime = "VERIFIED"`.

---

## 2. Bảng Thay đổi Mã nguồn & Cấu hình (Code & Config Changes)

> **Lưu ý Git**: File `infrastructure/wg-easy/healthcheck.sh` có thay đổi local có sẵn từ trước khi audit và **không** được stage/commit trong suốt quá trình sửa chữa. Không chạy `git push`.

| Phase / Commit SHA | File sửa đổi / tạo mới | Nội dung chính đã sửa |
| --- | --- | --- |
| Phase 1 (`c2fe428`) | [labops_service.py](file:///home/aiot-ws1/projects/labops_control_center/backend/app/services/labops_service.py), [ssh_access_manager.py](file:///home/aiot-ws1/projects/labops_control_center/backend/app/services/ssh_access_manager.py), [test_admin_ssh_lifecycle.py](file:///home/aiot-ws1/projects/labops_control_center/backend/tests/test_admin_ssh_lifecycle.py) | P0-1: Gỡ `getpass`, vô hiệu hóa `_ensure_local_workspace_sshd_running` (`return False`), khóa `start_canary_sshd_for_workspace` chỉ chạy trong test harness & cấm chạy vào `backend/data/workspaces`, fail-closed `AGENT_UNAVAILABLE` / `PROVISION_FAILED` + `SSH_ACCESS_DENIED`. |
| Phase 2 (`933b077`) | [controller.go](file:///home/aiot-ws1/projects/labops_control_center/go/internal/docker/controller.go), [registry.go](file:///home/aiot-ws1/projects/labops_control_center/go/internal/docker/registry.go), [controller_test.go](file:///home/aiot-ws1/projects/labops_control_center/go/internal/docker/controller_test.go), [executor.go](file:///home/aiot-ws1/projects/labops_control_center/go/internal/agent/executor.go), [tunnel_client.go](file:///home/aiot-ws1/projects/labops_control_center/go/internal/agent/tunnel_client.go), [server.go](file:///home/aiot-ws1/projects/labops_control_center/go/internal/transport/server.go), [delegation.go](file:///home/aiot-ws1/projects/labops_control_center/go/internal/gateway/delegation.go), [types.go](file:///home/aiot-ws1/projects/labops_control_center/go/internal/gateway/types.go), [config.py](file:///home/aiot-ws1/projects/labops_control_center/backend/app/core/config.py), [node_agent_adapter.py](file:///home/aiot-ws1/projects/labops_control_center/backend/app/adapters/node_agent_adapter.py), [workspace_controller.py](file:///home/aiot-ws1/projects/labops_control_center/backend/app/services/workspace_controller.py), [labops_service.py](file:///home/aiot-ws1/projects/labops_control_center/backend/app/services/labops_service.py), [Dockerfile](file:///home/aiot-ws1/projects/labops_control_center/infrastructure/workspace-image/Dockerfile), [entrypoint.sh](file:///home/aiot-ws1/projects/labops_control_center/infrastructure/workspace-image/entrypoint.sh), [labops-agent.service](file:///home/aiot-ws1/projects/labops_control_center/infrastructure/systemd/labops-agent.service), [install-workstation-agent.sh](file:///home/aiot-ws1/projects/labops_control_center/infrastructure/systemd/install-workstation-agent.sh) | P0-2: Chuẩn hóa Docker hardening (UID/GID `20000..59999`, `CapDrop=ALL`, `no-new-privileges`, `:ro` `ssh_ro` mounts, cổng container `2222`, cấm `IpcMode=host`/`Privileged`), hợp nhất `CommandExecutor` gRPC/HTTP, thêm `SYNC_SSH_AUTHORIZED_KEYS` & `VERIFY_DOCKER_WORKSPACE`, bỏ mock fallback trong `NodeAgentClient`, tạo hardened workspace image Debian 13 (purge `sudo`) & systemd installer. |
| Phase 3 (`7c5f0e5`) | [ssh_authorization_gate.py](file:///home/aiot-ws1/projects/labops_control_center/backend/app/services/ssh_authorization_gate.py), [ssh_access_manager.py](file:///home/aiot-ws1/projects/labops_control_center/backend/app/services/ssh_access_manager.py), [labops_service.py](file:///home/aiot-ws1/projects/labops_control_center/backend/app/services/labops_service.py), [v2_platform.py](file:///home/aiot-ws1/projects/labops_control_center/backend/app/api/v1/endpoints/v2_platform.py), [main.py](file:///home/aiot-ws1/projects/labops_control_center/backend/app/main.py), [test_admin_ssh_lifecycle.py](file:///home/aiot-ws1/projects/labops_control_center/backend/tests/test_admin_ssh_lifecycle.py) | P0-3: Thêm cổng tập trung 8-gate `evaluate_ssh_authorization_gate`, nâng cấp `scrypt`, khóa brute-force 5 lần / 15 phút khi đổi mật khẩu lần đầu, hỗ trợ client-enrolled pubkey (`CLIENT_ENROLLED_KEY`), loại bỏ hoàn toàn passphrase/temp password khỏi ZIP, giữ `authorized_keys` rỗng tới khi `ACTIVATED` + verified, mặc định `require_passphrase=True` cho `rotate-key`, thêm `POST /api/v1/v2/ssh-access/{member_id}/verify`. |
| Phase 4 (`33818b1`) | [quota.go](file:///home/aiot-ws1/projects/labops_control_center/go/internal/storage/quota.go), [provisioner.go](file:///home/aiot-ws1/projects/labops_control_center/go/internal/storage/provisioner.go), [inventory.go](file:///home/aiot-ws1/projects/labops_control_center/go/internal/storage/inventory.go), [storage_test.go](file:///home/aiot-ws1/projects/labops_control_center/go/internal/storage/storage_test.go), [storage_inventory_service.py](file:///home/aiot-ws1/projects/labops_control_center/backend/app/services/storage_inventory_service.py), [labops_service.py](file:///home/aiot-ws1/projects/labops_control_center/backend/app/services/labops_service.py) | P0-4: Tắt chế độ mô phỏng mặc định `NewSystemQuotaManager(false)`, yêu cầu xác minh `prjquota`/`pquota`/`project` từ `/proc/mounts` + `repquota -P` / `xfs_quota`, sửa bug `StatusVerified` khi `!pool.QuotaSupported`, xóa pool giả `track` ZFS và `/media/LabOps-Data` `/dev/sdb1`, chặn fallback `pool-root`. |
| Phase 5 (commit cuối) | [labops.py](file:///home/aiot-ws1/projects/labops_control_center/backend/app/schemas/labops.py), [main.py](file:///home/aiot-ws1/projects/labops_control_center/backend/app/main.py), [labops.ts](file:///home/aiot-ws1/projects/labops_control_center/src/types/labops.ts), [member-management-view.tsx](file:///home/aiot-ws1/projects/labops_control_center/src/features/users/components/member-management-view.tsx), [test_p0_security_hardening.py](file:///home/aiot-ws1/projects/labops_control_center/backend/tests/test_p0_security_hardening.py), [HARDENING_REPAIR_DELIVERABLES_REPORT.md](file:///home/aiot-ws1/projects/labops_control_center/docs/security-hardening/HARDENING_REPAIR_DELIVERABLES_REPORT.md) | Hiển thị trung thực ma trận 9 trạng thái trên Dashboard (gỡ badge `ACTIVE` cứng, gỡ chú thích `ForceCommand` user host, đặt rotation có passphrase làm mặc định, thêm nút `Kiểm chứng lại Runtime`), bổ sung suite kiểm thử P0-1..P0-5 (`test_p0_security_hardening.py`) & báo cáo bàn giao. |

---

## 3. Báo cáo Kiểm thử (Automated & Runtime Test Report)

| Nhóm kiểm thử | Lệnh chạy thực tế | Kết quả quan sát được |
| --- | --- | --- |
| **Go Node Agent, Docker, Storage, Gateway & Build** | `cd go && go test -count=1 -v ./internal/docker/... ./internal/storage/... ./internal/agent/... ./internal/transport/... ./internal/gateway/... && go build -o bin/labops-agent ./cmd/labops-agent` | **Exit 0**. `internal/storage`: **5/5 PASS**; `internal/agent`: **1/1 PASS**; `internal/transport`: **1/1 PASS**; `internal/gateway`: **7/7 PASS**; `internal/docker`: **3 PASS**, **1 SKIP** (`TestDockerController_ListRealWorkspaces` bỏ qua do không truy cập được `/var/run/docker.sock` trong sandbox). Binary `go/bin/labops-agent` build thành công. |
| **Python Backend Security & Regression Suites (10 files)** | `PYTHONPATH=backend backend/.venv/bin/pytest -q backend/tests/test_p0_security_hardening.py backend/tests/test_admin_ssh_lifecycle.py backend/tests/test_storage_and_shared_resources.py backend/tests/test_member_provisioning.py backend/tests/test_download_zip_and_safe_delete.py backend/tests/test_ssh_dual_auth.py backend/tests/test_v2_security.py backend/tests/test_p0_regressions.py backend/tests/test_milestone2_hardening.py backend/tests/test_vpn_routing_recovery.py` | **Exit 0 — `80 passed, 1 warning in 43.00s`**. Bao gồm kiểm thử thực tế canary `sshd` + `prlimit` (`test_07`/`test_08`), kiểm thử E2E HTTP Python -> `/api/v1/agent/execute` (mock Go Agent), và toàn bộ negative tests G4. |
| **Frontend Typecheck & Linting** | `bun run typecheck && bun run lint && ./node_modules/.bin/oxlint src/types/labops.ts src/features/users/components/member-management-view.tsx` | **Exit 0**. `tsc --noEmit`: 0 lỗi. `oxlint` (250 files): 0 lỗi (23 cảnh báo sẵn có ở các file không sửa). `lint:deprecated` (167 files): 0 cảnh báo, 0 lỗi. `oxlint` riêng trên 2 file TS/TSX đã sửa: **0 warnings, 0 errors**. |
| **Shell Syntax Check** | `bash -n infrastructure/workspace-image/entrypoint.sh infrastructure/systemd/install-workstation-agent.sh` | **Exit 0**. |
| **Read-only Live Service Safety Check** | `curl -sI http://127.0.0.1:51821/login`, `curl -s http://127.0.0.1:9100/health`, `ss -tlnp` | `51821/login`: `HTTP/1.1 200 OK`; `9100/health`: `{"agent":"labops-go-agent-v2","hostname":"aiot-ws1","server_id":"workstation","status":"HEALTHY",...}`; Admin SSH `:1912` & `:22` còn `LISTEN`; `labops-agent` cũ PID `57189` và 3 tiến trình `sshd` host cũ (`3677306`:22003, `3824760`:22004, `4092758`:22005) vẫn giữ nguyên chưa tác động. |

---

## 4. Báo cáo Triển khai, Runbook `sudo` cho Admin & Rollback

### 4.1. Thực trạng trên host tại thời điểm bàn giao
- Các bản vá mã nguồn và binary `go/bin/labops-agent` đã hoàn tất trên workstation.
- Tuy nhiên, agent dịch vụ nền (`systemd`, PID `57189`) đang chạy nhị phân cũ, chưa có nhóm `docker`, chưa build image `labops-workspace` trên Docker host, phân vùng `/dev/sdc` (`/mnt/HDD3`) chưa bật `prjquota`, và 3 daemon `sshd` host cũ vẫn đang lắng nghe cổng `22003`–`22005`. Do chạy trong môi trường không có mật khẩu `sudo` và có tác vụ nghiên cứu trên máy, các thao tác đặc quyền mức kernel/systemd dưới đây **chờ Admin trực tiếp thực thi**.

### 4.2. Operator `sudo` Runbook (Thực hiện tuần tự trong cửa sổ bảo trì)

1. **Build & ghim Workspace Image bất biến theo `@sha256:`**:
   ```bash
   cd /home/aiot-ws1/projects/labops_control_center
   sudo docker build -t labops-workspace:v2 infrastructure/workspace-image
   # Nếu dùng registry nội bộ, push rồi lấy RepoDigest; hoặc lấy config ID/digest thực tế của Docker host
   sudo docker inspect --format='{{.Id}} {{json .RepoDigests}}' labops-workspace:v2
   ```
2. **Cấu hình biến môi trường cho Backend & Node Agent**:
   - Đảm bảo `/etc/labops/agent.env` và môi trường backend có đồng bộ:
     - `LABOPS_LOCAL_AGENT_URL=http://127.0.0.1:9100`
     - `LABOPS_NODE_AGENT_TOKEN=<strong-shared-secret>`
     - `LABOPS_WORKSPACE_BIND_IP=192.168.167.251`
     - `LABOPS_WORKSPACE_IMAGE_REF=<image@sha256:64hex>`
     - `LABOPS_WORKSPACE_IMAGE_DIGEST=sha256:<64hex>`
3. **Cài đặt `labops-agent` mới + systemd unit (`SupplementaryGroups=docker`)**:
   ```bash
   sudo bash /home/aiot-ws1/projects/labops_control_center/infrastructure/systemd/install-workstation-agent.sh
   curl -s http://127.0.0.1:9100/health
   ```
4. **Bật `ext4 prjquota` cho `/mnt/HDD3` (`/dev/sdc`)** *(thực hiện khi không có job nghiên cứu đang ghi nặng lên `/mnt/HDD3`)*:
   ```bash
   # Kiểm tra fstab & mount options hiện tại trước khi sửa
   grep -n "HDD3" /etc/fstab /proc/mounts
   # Sau khi unmount an toàn hoặc trong maintenance window:
   # sudo umount /mnt/HDD3
   # sudo tune2fs -O project,quota /dev/sdc
   # Cập nhật thêm option 'prjquota' vào dòng /mnt/HDD3 trong /etc/fstab rồi:
   # sudo mount /mnt/HDD3 && sudo quotaon -P /mnt/HDD3
   # Xác minh: grep /mnt/HDD3 /proc/mounts | grep prjquota
   ```
5. **Kiểm tra Admin SSH `:1912` & WireGuard trước khi thu dọn daemon `sshd` host cũ**:
   ```bash
   # Mở sẵn 1 phiên SSH quản trị dự phòng tới 192.168.167.251:1912 trước khi thao tác!
   sudo wg show wg0
   # Sau khi xác nhận phiên SSH 1912 & wg-easy đều ổn định, dừng 3 tiến trình host sshd cũ (giữ nguyên PID 8984):
   sudo kill 3677306 3824760 4092758
   ss -tlnp | grep -E ':1912|:2200'
   ```

### 4.3. Quy trình Rollback
- Khôi phục binary và systemd unit của `labops-agent` từ bản sao lưu mà `install-workstation-agent.sh` tạo tại `/var/backups/labops-agent-<TIMESTAMP>/`:
  ```bash
  sudo cp /var/backups/labops-agent-<TS>/labops-agent.bak /usr/local/bin/labops-agent
  sudo cp /var/backups/labops-agent-<TS>/labops-agent.service.bak /etc/systemd/system/labops-agent.service
  sudo systemctl daemon-reload && sudo systemctl restart labops-agent.service
  ```
- Khôi phục dữ liệu JSON của backend (nếu cần) từ `/tmp/labops-hardening-backup-20261119/` (`chmod 600` sau khi copy vào `backend/data/`).

---

## 5. Security Acceptance Checklist

| Tiêu chí bảo mật P0 | Trạng thái | Ghi chú thực tế |
| --- | --- | --- |
| Loại bỏ host `canary_sshd` fallback & không mạo danh `aiot-ws1` UID `1000` | **PASS** (code + unit test) / **BLOCKED_ON_HOST_SUDO** (dọn 3 PID cũ) | Code backend đã khóa cứng; PID `3677306`/`3824760`/`4092758` cần `sudo kill` trong runbook. |
| Cô lập Docker workspace (`UID 20000..59999`, `CapDrop=ALL`, không `sudo`, không `docker.sock`, port `2222`) | **PASS** (Go/Python tests) / **BLOCKED_ON_HOST_SUDO** (live daemon) | `install-workstation-agent.sh` & `Dockerfile` sẵn sàng; cần `sudo` cài unit & build image. |
| Cổng kiểm soát SSH Authorization Gate & Activation lần đầu | **PASS** | `authorized_keys` rỗng cho tới khi `ACTIVATED` + verified; khóa brute-force 5 lần / 15 phút; revoke xóa sạch khóa. |
| Hashing `scrypt` & Separate-Channel Secrets (ZIP không chứa mật khẩu/passphrase) | **PASS** | Đã kiểm chứng cả API `claim-secrets` một lần và nội dung file ZIP thực tế. |
| Trung thực Storage Quota & Runtime Verification | **PASS** | Bỏ hoàn toàn pool giả; kiểm tra thật `/proc/mounts` `prjquota`; `/mnt/HDD3` cần bật `prjquota` ở mức OS (`sudo`). |
| Bảo toàn WireGuard (`2204`/`51821`), Admin SSH (`1912`), Rack (`192.168.167.250`) | **PASS** | Đã xác minh live `200 OK` trên `127.0.0.1:51821/login`, `:1912` `LISTEN`, chưa đụng Rack. |

---

## 6. Kết luận Nghiệm thu (Final Verdict)

**`NOT_PRODUCTION_READY`**

Hệ thống phần mềm (Backend FastAPI, Go Node Agent/Gateway, SSH Authorization Gate, Storage Inventory/Quota Verifier, Dashboard UI 9 trạng thái và bộ 80 Python + Go tests) đã được sửa chữa, vá lỗ hổng bảo mật P0-1..P0-5 và kiểm thử đạt chuẩn. Tuy nhiên, trạng thái vận hành thực tế trên `aiot-ws1` **chưa được phép gắn nhãn `PRODUCTION_READY`** cho đến khi Quản trị viên thực thi Runbook `sudo` tại Mục 4.2: (1) cài `labops-agent` mới có quyền nhóm `docker`, (2) build & ghim digest `@sha256:` cho Workspace Docker image, (3) bật `prjquota` trên `/mnt/HDD3`, và (4) dừng an toàn 3 tiến trình `sshd` host cũ (`3677306`, `3824760`, `4092758`).
