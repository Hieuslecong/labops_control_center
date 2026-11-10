# LabOps Secure Access V2 — Audit hạ tầng và code

**Cập nhật:** 2026-11-04 (Asia/Ho_Chi_Minh)
**Trạng thái:** Audit mã nguồn hoàn tất; kiểm toán host/router/Gateway chưa xác minh.

> Báo cáo này thay thế các số liệu `VERIFIED_LIVE` trong bản cũ. Phiên thực thi hiện tại là sandbox giới hạn quyền, không phải phiên shell quản trị trên máy Workstation. Những số liệu CPU/GPU/IP/phiên SSH trong báo cáo cũ không được dùng làm bằng chứng triển khai.

## Phạm vi và bằng chứng

Đã đọc backend FastAPI, adapters và JSON data layer; các API user/SSH; frontend User Management, Overview và server detail; provisioning scripts; Compose/env; Teleport example. Kiểm tra trực tiếp host bằng `systemctl`, `wg`, `ss`, `teleport/tctl` và Docker bị giới hạn bởi sandbox: system bus/netlink không cho phép, `ss` không cho thấy listener trong namespace này, Teleport tools không có trong PATH và Docker socket bị từ chối. Không truy cập được MikroTik, public gateway, hai máy chủ vật lý, DNS/TLS hoặc thiết bị researcher.

Người dùng đã cung cấp trước đó địa chỉ SSH `118.70.169.236` và output `ssh.service` đang chạy. Thông tin đó xác nhận đường SSH quản trị mà người dùng đang sử dụng tại thời điểm cung cấp; nó không chứng minh UDP forwarding, WireGuard handshake, firewall policy hay trạng thái hiện tại của host.

## Kiến trúc code hiện có

- **Frontend:** Next.js/React, Zustand; production User Management được chuyển sang backend qua same-origin BFF và Clerk admin check.
- **Backend:** FastAPI với service layer `LabOpsDataService`. User/policy/audit/workspace mapping hiện lưu trong JSON ở `backend/data/`; dù Compose có PostgreSQL, service này chưa chuyển sang PostgreSQL.
- **WireGuard:** adapter chỉ cấu hình từ `WIREGUARD_SERVERS_JSON` và gọi `wg` cục bộ; nó không thể cấu hình router/Gateway từ xa. Đăng ký yêu cầu workspace và VPN policy đã xác minh.
- **Teleport:** có adapter `tctl` và file cấu hình mẫu Community `18.11.8` (patch 18.x hiện hành ngày 2026-11-04 theo changelog chính thức); chưa có cluster đã cài, kết nối hoặc kiểm tra phiên bản thực tế.
- **Workspace agents:** Incus adapter chưa nối Teleport node/service vào boundary của workspace. Không có bằng chứng RBAC Linux/container được triển khai ở hai máy.

## Root cause tìm thấy trong source

| Khu vực | Vấn đề trong code/dữ liệu cũ | Xử lý hiện tại |
|---|---|---|
| WireGuard | Adapter/profile cũ có fallback endpoint/key và tạo config phía server; sample `.conf` chứa private key với quyền đọc quá rộng. | Xóa sample chứa private key; retire script tạo profile; adapter mới chỉ nhận public key client, yêu cầu server identity cấu hình thật và xác minh peer qua `wg`. Legacy profile không còn báo `ACTIVE` nếu không có device mapping. |
| User/SSH provisioning | Luồng cũ ghi mật khẩu Linux, SSH port/key và kết quả xác minh giả vào record; UI có trạng thái thao tác cục bộ. | API legacy trả `410/409`; user mới chỉ là `PENDING_PROVISIONING`; production record ACTIVE cũ được trả như legacy chưa xác minh. Không thay đổi sshd, router hay session trên host. |
| Admin authorization | Các route cũ không dùng một lớp xác thực backend nhất quán. | Route user/SSH/access yêu cầu Bearer admin token; BFF kiểm tra Clerk admin và chỉ gửi token phía server. Chưa xác minh thiết lập token trên deployment thật. |
| Thu hồi | Xóa member/khóa cục bộ không chứng minh đã kết thúc session ở Teleport, WireGuard và SSH đồng thời. | Thu hồi cố gắng xử lý WireGuard và Teleport, giữ dữ liệu workspace, trả `PARTIAL_FAILURE` khi không xác minh được; chưa có session collector/termination đã kiểm thử. |
| Teleport | Trước thay đổi chưa có Teleport integration. | Có adapter/config mẫu và chính sách role; service cố ý chặn mời khi workspace/network policy/WireGuard chưa xác minh. |
| Dashboard | Có node health `online` hardcoded, GPU-hours/cảnh báo cố định, fallback GPU 24 GB/A30, dữ liệu chart/SSH giả lập trong production. | Health lấy collector status; bỏ số liệu hardcoded khỏi KPI production; chart giả chỉ ở demo; lỗi telemetry xóa snapshot cũ và trạng thái khởi tạo là `unknown`. |
| Security scanner | API cũ trả `APPROVED` cho mọi URL mà không chạy scanner. | Production trả `503 Security scanner is not configured`; dashboard production nói rõ chưa có collector. |

## Hạ tầng production còn chưa xác minh

- WireGuard interface, server key, peer list, IPAM đang dùng, UDP port và handshake thực.
- MikroTik NAT/firewall, public IP reachability, route/NAT và chặn peer-to-peer/direct SSH.
- SSH listener/port, firewall hiện tại, workspace/container, GPU runtime và hai host đang chạy gì.
- Teleport Community service/version, TLS/DNS, WebAuthn, SMTP invitation, role thực tế, SSH node, logs/session recordings.
- Docker/Incus isolation, volume ownership và đường truy cập chéo workspace.
- Trạng thái các phiên SSH cũ, tài khoản Linux và training job tại thời điểm migration.

## Hệ quả

Mã hiện fail-closed khi thiếu Gateway/Teleport/workspace verification; do vậy thao tác đăng ký peer, Teleport invite và đánh dấu account active chưa thể hoàn tất trên deployment này. Không được dùng báo cáo cũ như bằng chứng rằng có 1 GPU/3 GPU, một phiên SSH cụ thể, storage pool ZFS, Incus, hoặc VPN đã hoạt động. Cần chạy audit có quyền trên từng host và router trước khi canary.

## Tài liệu tham chiếu

- [WireGuard Quick Start](https://www.wireguard.com/quickstart/)
- [Teleport Community deployment](https://goteleport.com/docs/get-started/deploy-community/)
- [Teleport roles](https://goteleport.com/docs/reference/access-controls/roles/)
- [Teleport authentication](https://goteleport.com/docs/reference/access-controls/authentication/)
