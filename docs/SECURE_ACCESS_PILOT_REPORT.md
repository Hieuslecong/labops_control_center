# LabOps Secure Access V2 — Phase 2 Pilot Report

**Ngày kiểm tra:** 2026-11-05  
**Quyết định:** `PARTIAL_IMPLEMENTATION` · Pilot `BLOCKED_BY_INFRASTRUCTURE`  
**Phạm vi:** Audit code và preflight chỉ đọc trong execution environment hiện tại; tách biệt Legacy SSH khỏi Secure Access v2. Không có thay đổi production.

## 1. Kết luận và kiến trúc quan sát được

Pilot end-to-end chưa được tạo. Không có bằng chứng về Gateway/Hub, Teleport cluster, container pilot, researcher test account hay macOS client. Legacy SSH được giữ ngoài kiến trúc mới; trạng thái host hiện tại chưa được xác minh.

```text
Legacy SSH (existing; unmanaged by v2) ──────────────── stays independent

macOS client ──?── WireGuard Hub ──?── Teleport Proxy ──?── New workspace container
                 không quan sát được  chưa cài/xác minh     không quan sát được
```

Luồng trong source code đã có điểm nối LabOps → WireGuard local adapter → Teleport adapter, nhưng đăng ký peer bị chặn khi workspace hoặc network policy chưa `VERIFIED`. Network policy hiện cố ý trả `NOT_VERIFIED`; adapter và file Teleport là cấu hình mẫu, không phải dịch vụ đã triển khai.

## 2. Preflight audit

| Hạng mục | Bằng chứng đọc được | Trạng thái |
|---|---|---|
| OS / danh tính | Execution environment báo Ubuntu 22.04.5, kernel 5.15, user `hieulc`; đây không chứng minh đầy đủ trạng thái host vật lý. | `PARTIAL` |
| IP / interface / route | `ip` và `ss` không mở được netlink: `Operation not permitted`; không lấy được địa chỉ/interface/route hoặc listener. | `BLOCKED_BY_INFRASTRUCTURE` |
| Firewall | `nft list ruleset` và `iptables -S` bị netlink từ chối; không sửa rule nào. | `BLOCKED_BY_INFRASTRUCTURE` |
| WireGuard | Có `/usr/bin/wg` và `wg-quick`; `wg show interfaces` và truy vấn `wg0` bị từ chối. Không xác minh interface, key, peer, subnet, handshake hay RX/TX. `WIREGUARD_SERVERS_JSON` không có trong môi trường tiến trình. | `BLOCKED_BY_INFRASTRUCTURE` |
| UDP 2203 / 51820 | Không thể đọc socket/listener; không thể kết luận cổng mở hay đóng. Không có NAT/Gateway config để kiểm tra. | `BLOCKED_BY_INFRASTRUCTURE` |
| Đường SSH quản trị | Người dùng từng cung cấp `ssh.service` ở trạng thái running và địa chỉ `118.70.xxx.xxx`; đó là bằng chứng do người dùng cung cấp tại thời điểm trước, không được kiểm tra lại trong preflight này và không chứng minh đây là Gateway. | `NOT_REVALIDATED` |
| Docker / workspace | Docker CLI tồn tại nhưng kết nối `/var/run/docker.sock` bị từ chối; không thấy container/workspace thực tế. | `BLOCKED_BY_INFRASTRUCTURE` |
| Teleport | Không tìm thấy `teleport`, `tctl` hoặc `tsh`; truy vấn systemd bị từ chối. Không có Teleport cluster/version/service health để xác minh. | `BLOCKED_BY_INFRASTRUCTURE` |
| Gateway / DNS / TLS / Public IP | `TELEPORT_PUBLIC_ADDR` và `SMTP_HOST` không được cấu hình trong môi trường; file mẫu dùng địa chỉ/domain ví dụ. Không có DNS, chứng thư TLS hoặc public IP Gateway đã xác minh. | `BLOCKED_BY_INFRASTRUCTURE` |
| Backup / recovery / rescue | Không truy cập được host, volume backup hay quy trình restore. Đường SSH cũ chỉ là thông tin đã cung cấp, chưa xác minh là rescue path độc lập. | `BLOCKED_BY_INFRASTRUCTURE` |
| Training jobs / SSH sessions | Process list chỉ phản ánh namespace này; không chứng minh tình trạng trên Workstation. Không thể xác nhận job/session hiện hữu. | `NOT_TESTED` |

Các lệnh service không có quyền kết nối system bus. Không dùng `sudo`, không gọi lệnh thay đổi route/firewall/WireGuard/SSH, không cài package, không restart service và không sửa container/job.

Một SSH probe read-only tới địa chỉ quản trị đã cung cấp bị chặn ngay trong sandbox (`socket: Operation not permitted`); không tạo được remote session. Vì vậy baseline Legacy SSH chưa được thu thập. Xem [Legacy SSH baseline](LEGACY_SSH_BASELINE.md); đây là gap record, không phải xác nhận trạng thái trước/sau.

## 3. Dịch vụ và cấu hình

| Thành phần | Trạng thái thực tế trong lần kiểm tra |
|---|---|
| WireGuard Hub | Chưa xác minh có Hub; không thấy interface qua netlink. Chưa có endpoint/peer thật để kiểm tra. |
| Teleport Community | Binary/service không quan sát được; `18.11.8` chỉ là version pin trong code/template, không phải version đã cài. Changelog chính thức liệt kê `18.11.8` trong nhánh 18.x ([Teleport changelog](https://goteleport.com/docs/changelog/)). |
| Workstation SSH | Không revalidate được `sshd`; SSH hiện tại không bị sửa hoặc đóng. |
| Workspace/container | Không truy cập được Docker daemon; không tạo workspace pilot. |
| macOS client | Không có thiết bị client trong môi trường; không tạo hay lưu private key. |
| TLS / invitations | Không có domain, certificate hoặc SMTP config đã xác minh; không gửi invitation. |

Trong Phase 2 không chỉnh file production config. Chỉ bổ sung [Legacy SSH baseline](LEGACY_SSH_BASELINE.md), báo cáo này và nhãn phân tách Legacy/Teleport trên trang Quản lý truy cập. Các artifact runtime từ Phase 1 là [WireGuard adapter](../backend/app/adapters/wireguard_adapter.py), [Teleport adapter](../backend/app/adapters/teleport_adapter.py), [Teleport config mẫu](../deploy/teleport/teleport.yaml.example), [access API](../backend/app/api/v1/endpoints/access.py), [admin guard](../backend/app/api/dependencies.py) và [LabOps service](../backend/app/services/labops_service.py). Config mẫu còn địa chỉ RFC 5737/domain ví dụ và không được áp dụng nguyên trạng.

Trong code, researcher role giới hạn `labops-workspace` label và TTL 12h; admin role TTL 2h, yêu cầu per-session MFA. Cấu hình mẫu yêu cầu local WebAuthn. Đây là policy được tạo/test ở mức code; chưa chứng minh role, WebAuthn, certificate hay isolation thực tế. `tctl` version ở client không tự chứng minh version của cluster/agent; cluster compatibility vẫn chưa được kiểm tra.

## 4. WireGuard, RBAC và SSH isolation

- **WireGuard:** không có public key, peer/IP allocation, handshake, counter hoặc route được xác minh. Không phát sinh key giả và không thêm peer.
- **Network policy:** không đọc được firewall; chưa kiểm tra peer-to-peer block, VPN-to-Proxy allowlist, public Proxy denial hoặc SSH denial.
- **Teleport/RBAC:** chưa có cluster, user, node label hay SSH node. Chỉ có role factory trong code; chưa có node identity tại workspace boundary.
- **Container isolation:** chưa kiểm tra sudo, privileged mode, Docker socket, host mounts hay khả năng truy cập container khác. Không cài SSH agent lên host hoặc container.
- **Revocation:** code báo trạng thái một phần nếu không xác minh được phiên; chưa thử disable user, xóa peer, ngắt session hoặc ngăn reconnect.
- **Audit/recording:** chưa có event/recording từ Teleport thật. Không có collector chứng minh login/session history trong Dashboard.

## 5. Nghiệm thu E2E

| Test | Kết quả | Bằng chứng / lý do |
|---|---|---|
| L01 — So sánh Legacy SSH với baseline trước/sau | `BLOCKED` | Baseline host không thu thập được; không có session SSH admin khả dụng từ sandbox để lấy checksum/trạng thái. |
| T01 — WireGuard handshake thật | `BLOCKED` | `wg show` bị netlink từ chối; không có peer/device pilot. |
| T02 — Proxy chỉ truy cập qua VPN | `BLOCKED` | Không có Gateway, Proxy, firewall visibility hoặc DNS/TLS. |
| T03 — WebAuthn login | `BLOCKED` | Không có Teleport service/account hoặc thiết bị MFA. |
| T04 — Certificate TTL 12 giờ | `NOT TESTED` | Role factory trong code đặt 12h; không cấp certificate thực. |
| T05 — SSH vào workspace được cấp | `BLOCKED` | Không có Teleport node/workspace/container pilot. |
| T06 — Từ chối workspace khác | `NOT TESTED` | Không có hai node/workspace để thực hiện negative test. |
| T07 — Không truy cập host | `NOT TESTED` | Không có Teleport/host policy để thử. |
| T08 — VS Code Remote SSH | `NOT TESTED` | Không có profile, account hoặc node thật. |
| T09 — Revoke ngăn reconnect | `NOT TESTED` | Không có identity/peer/session test. |
| T10 — Revoke ngắt session đang chạy | `NOT TESTED` | Không có session thật; code hiện không tuyên bố user deletion đã chấm dứt session. |
| T11 — Session recording và audit thật | `NOT TESTED` | Không có Teleport cluster/collector. |
| T12 — Training job không bị ảnh hưởng | `NOT TESTED` | Không đọc được process/container trên host. Không có workload nào bị chạm tới. |
| T13 — Restart và khôi phục kết nối | `NOT TESTED` | Không có service pilot; không restart production service. |
| T14 — Restore backup trong môi trường cô lập | `NOT TESTED` | Không có backup/state truy cập được. |

Unit tests trước đó xác nhận một số policy và fail-closed behavior ở mức code; không dùng chúng làm bằng chứng E2E.

Trong lượt Phase 2, `bun run typecheck` và `git diff --check` đều PASS sau khi bổ sung nhãn phân tách Legacy/Teleport và tài liệu. Không chạy lại backend suite vì lượt này không đổi backend code.

## 6. Các lỗi, blockers và việc chưa làm

Audit Phase 1 đã sửa các fallback credential/profile, luồng SSH/password legacy và trạng thái Dashboard không có telemetry. Phase 2 không phát hiện bằng chứng hạ tầng mới để mở khóa pilot. Trang Quản lý truy cập được bổ sung nhãn rõ ràng: Legacy SSH existing nhưng live status chưa xác minh, unmanaged by v2; Teleport SSH managed by LabOps nhưng chưa xác minh.

Lưu ý về separation: Phase 1 đã vô hiệu hóa các endpoint LabOps cấp/quản lý SSH/password theo từng user. Điều này không sửa OpenSSH hoặc credentials trên host, nhưng trạng thái các thao tác quản trị Legacy SSH qua UI/API chưa được xác minh là còn tương thích. Xem baseline gap record trước khi dựa vào Dashboard để quản lý Legacy SSH.

Blockers cần xử lý trước khi có thể làm canary:

1. Cấp đường quản trị được phép tới đúng Workstation/Gateway, với quyền đọc trạng thái service/network/firewall/container và phương án rescue độc lập.
2. Thu thập baseline thực tế Legacy SSH, bao gồm service/ports, checksum config, accounts/authorized_keys, sessions, firewall/NAT, containers và jobs. Không bắt đầu thay đổi host trước khi có baseline so sánh.
3. Chỉ định Gateway có public endpoint, domain/TLS và UDP ingress đã duyệt; cung cấp sơ đồ route/subnet, quy tắc peer isolation và bằng chứng backup.
4. Cài hoặc chỉ định Teleport Community 18.11.8 thật; xác minh Auth/Proxy reachability qua VPN, WebAuthn, cluster/agent version, session recording và backup/restore.
5. Chọn một container thử nghiệm có thể bỏ đi nhưng giữ dữ liệu an toàn; xác minh non-privileged boundary, login Linux, sudo/socket/mount isolation và node label do admin kiểm soát.
6. Cung cấp macOS test client ngoài mạng và người dùng thử nghiệm không trùng tài khoản production.
7. Xác định maintenance window/approval vận hành cho mọi thay đổi ảnh hưởng production và ghi rollback snapshot trước thay đổi.

Backend hiện dùng JSON; chưa chứng minh multi-process provisioning lock. Dashboard/API chỉ có thể báo health từ collector cục bộ khi được cấu hình; không chứng minh đường mạng end-to-end. Network policy đang `NOT_VERIFIED`, nên enrollment/activation bị khóa đúng như thiết kế.

## 7. Rollback

Không có thay đổi production trong đợt audit này nên không có rollback hạ tầng cần chạy. Nếu pilot được cấp phép sau này:

1. Giữ nguyên SSH quản trị hiện hữu và xác minh rescue path trước mọi cutover.
2. Dừng cấp invitation/thiết bị mới; vô hiệu hóa đúng tài khoản pilot trong Teleport.
3. Xác minh/ngắt session pilot bằng Teleport session controls; nếu không chứng minh được thì giữ trạng thái `PARTIAL_FAILURE` và cô lập qua policy đã được duyệt.
4. Gỡ đúng WireGuard peer pilot theo device ID/public key đã ghi trong change record, sau đó xác minh peer không còn và không xóa peer khác.
5. Gỡ node/agent và firewall rules riêng của pilot theo snapshot; không xóa volume/container data và không đụng workload hiện có.
6. Kiểm tra SSH rescue, route, firewall và health sau rollback; ghi nhận bằng chứng trước khi đóng change.

Các bước trên là thủ tục dự kiến, chưa được diễn tập trên hạ tầng thật.

## 8. Quyết định sẵn sàng

**Không đạt điều kiện `PILOT_VERIFIED`.** Trạng thái giữ nguyên `PARTIAL_IMPLEMENTATION`; pilot thực tế `BLOCKED_BY_INFRASTRUCTURE`. Không có service nào được báo online, không có tài khoản/peer thật được tạo, và không có kết luận production-ready.

**Chưa kiểm tra:** Legacy SSH baseline/checksums, host/router state, UDP reachability, TLS, backup restore, training workload, session termination và mọi T01–T14 runtime.  
**Rủi ro:** SSH quản trị/tài khoản cũ vẫn theo cấu hình trước đó; không đóng hoặc thu hồi chúng tự động.
