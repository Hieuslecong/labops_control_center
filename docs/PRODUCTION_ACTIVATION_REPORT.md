# LabOps Secure Access V2 — Kết quả triển khai trong codebase

**Cập nhật:** 2026-11-04
**Kết quả:** `PARTIAL_IMPLEMENTATION` · Production end-to-end `NOT VERIFIED`.

Không có lệnh cấu hình production host/router nào được chạy. Không restart `sshd`/WireGuard, không thêm peer thật, không tạo Teleport user, không đóng SSH port và không thay đổi container, volume, GPU hoặc training job. Sandbox không có quyền host/root, Teleport cluster hay Gateway.

## Kiến trúc trước/sau

**Trước:** UI/server-side JSON có thể sinh profile VPN/credentials và báo trạng thái hoạt động từ dữ liệu tĩnh; SSH provisioning gắn mật khẩu/port riêng cho user.

**Code sau thay đổi:**

```text
Admin UI -- Clerk admin + server-side API token --> LabOps FastAPI / JSON store
   |                                               |-- Workspace: NOT_VERIFIED
   |                                               |-- WireGuard: local wg adapter; Gateway absent
   |                                               |-- Teleport: tctl adapter; cluster absent
   |                                               `-- SSH: legacy routes disabled; host unchanged
   `-- User/device record remains PENDING until live dependencies and checks pass
```

Đây là fail-closed application workflow, chưa phải VPN Hub-and-Spoke đang hoạt động. Teleport Proxy/Auth/SSH node, reverse tunnel, WebAuthn enrollment, certificate issuance, VS Code và session recording chưa được triển khai/xác minh.

## Đã thay đổi

- **Backend access control:** Bearer admin token fails closed khi chưa cấu hình; user, legacy SSH và access mutations được bảo vệ. Legacy mật khẩu, SSH key/port, server-side WireGuard config và simulated dual-auth routes bị vô hiệu hóa.
- **Provisioning:** user mới được ghi là `PENDING_PROVISIONING`; không tạo Linux password/SSH port/private key, không tạo workspace/storage/GPU allocation và không tuyên bố workspace đã chạy. Duplicate username/workspace và giới hạn tài nguyên được kiểm tra.
- **WireGuard:** per-device public key validation, IPAM/duplicate checks, kiểm tra live peer và rollback khi verify/persist lỗi; client private key/config không được backend tạo hay trả. Cần cấu hình thật và quyền `wg` trên Gateway để dùng.
- **Teleport:** adapter Community được pin `18.11.8` (patch 18.x hiện hành ngày 2026-11-04), tạo role workspace-scoped cho researcher (12h) và role admin (2h, session MFA); local WebAuthn bắt buộc trong file mẫu. Không có `tctl`/cluster đã xác minh. Invitation cần HTTPS Proxy + SMTP; token chỉ gửi qua email, không trả API.
- **Thu hồi:** cố gắng xóa Teleport identity và WireGuard peer riêng rẽ; active session termination không được xem là thành công nếu chưa xác minh. Ghi `PARTIAL_FAILURE` khi còn bước chưa xác minh; không xóa workspace data.
- **UI:** User Management nhận dữ liệu backend, yêu cầu public key do client tạo, không dựng QR/profile/private key. Dashboard production không còn hiển thị GPU-hours/cảnh báo/chart giả làm số liệu thật; lỗi polling không giữ snapshot cũ như thể online.
- **Đăng nhập/admin:** production dùng Clerk sign-in và bảo vệ dashboard; BFF kiểm tra quyền admin trước khi chuyển yêu cầu kèm backend token. Nếu chưa cấu hình Clerk hoặc backend token, hệ thống từ chối truy cập/thao tác admin thay vì mở demo.
- **Các màn hình vận hành:** trạng thái server/workspace/process/storage chưa có collector hiện là chưa xác minh; trang Settings không báo kiểm tra thành công giả, và trang Workspace không còn hiển thị SSH port riêng.
- **Legacy migration:** record ACTIVE không có `access_verified_at` được backend trả là pending/unverified; profile WireGuard legacy không có device mapping không được đánh dấu ACTIVE. SSH service, mapping/router và kết nối hiện hữu không bị thay đổi.
- **Secrets:** xóa sample `.conf` chứa private key; bỏ `config_content` khỏi record hiện hành. Không chép giá trị secret vào báo cáo.

## API/workflow hiện có

Các route tương đương access health, user create/list/detail, device register/revoke, Teleport activation và user revoke đã nối với service/auth. Legacy routes liên quan password/port/key/config trả `410` hoặc `409`. Kết quả session/audit Teleport chưa có collector; UI ghi rõ chưa xác minh, không phát sinh bản ghi giả.

**Database:** chưa có migration PostgreSQL. Backend hiện dùng JSON. IPAM ghi atomically và bảo vệ trong process; toàn bộ provisioning chưa có transaction/idempotency xuyên process, vì vậy concurrent multi-worker provisioning chưa được chứng minh an toàn.

## Kiểm tra đã chạy

| Kiểm tra | Kết quả |
|---|---|
| `bun run typecheck` | PASS |
| `python -m unittest discover -s tests` trên bản copy cô lập | PASS — 40 tests; 2 skipped do cần hardware thật và loopback bind bị sandbox chặn |
| Shell syntax + fail-closed smoke check cho bốn script cũ | PASS — exit 2, thông báo bị vô hiệu hóa, không in `PrivateKey=` |
| API HTTP/E2E qua host, WireGuard handshake, Teleport MFA/SSH/RBAC/recording, VS Code | `NOT VERIFIED` — không có host/Gateway/Teleport và sandbox chặn listener |
| Router/NAT/firewall, không truy cập workspace khác/host trực tiếp, thu hồi session đang chạy | `NOT VERIFIED` |
| lint/build | Chưa chạy |

Unit tests chạy trên bản sao `/tmp`; không chạy trực tiếp trên JSON production của repo. Không có test nào chứng minh E2E hoặc live deployment.

Typecheck và unit tests chạy lại sau thay đổi cuối; unit suite có log `nvidia-smi` thất bại khi sandbox không cấp quyền GPU, nhưng các test liên quan xử lý trạng thái đó và suite vẫn qua.

## Việc cần có trước pilot

1. Gateway/VPS được duyệt, UDP ingress/NAT, static endpoint và VPN-only route/firewall; kiểm tra không peer-to-peer và không SSH trực tiếp.
2. Host admin xác minh WireGuard, SSH hiện hữu, workspace/container/runtime, owner/mount boundaries và backup; test pilot không chạm tài khoản/job đang chạy.
3. Teleport Community 18.11.8 thực, DNS/TLS, Auth private, Proxy chỉ từ VPN, WebAuthn, SMTP và admin identity. Cài SSH service tại đúng boundary workspace với reverse tunnel; không dùng privileged/Docker socket.
4. Bổ sung workspace agent/policy verifier và session/audit collector; dùng database/locking phù hợp nếu cần multi-worker.
5. Canary một tài khoản Workstation, chứng minh MFA, TTL/roles, SSH/VS Code, cách ly A/B/host, revoke peer/cert/session, audit/recording; chỉ sau đó đánh giá Track.

## Rollback và giới hạn

Không có production migration để rollback. SSH hiện hữu và hạ tầng không bị đụng tới. Working tree vốn có nhiều thay đổi chưa commit và dữ liệu JSON đã dirty trước phần code này; **không chạy `git reset`, checkout hàng loạt hoặc khôi phục toàn bộ data files**. Rollback code cần review/revert có chọn lọc theo patch đã duyệt. Danh sách server key, endpoint, port-forward và secrets không được đưa vào báo cáo.

`NOT VERIFIED`: quyền host/router, peer/handshake thật, Teleport MFA/certificate/session, workspace isolation, production backup/rollback. Rủi ro còn lại là các tài khoản/SSH cũ tiếp tục theo cấu hình hiện hành cho đến khi admin xác minh và thực hiện migration riêng; code không tự thu hồi chúng.
