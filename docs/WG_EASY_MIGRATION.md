# CHIẾN LƯỢC CHUYỂN ĐỔI KHÔNG DOWNTIME (ZERO-DOWNTIME MIGRATION) — WG-EASY & WG0

## 1. Mục tiêu và Nguyên tắc Chuyển đổi

Quá trình chuyển đổi từ quản lý WireGuard thủ công (`wg0` trên host) sang quản lý tập trung tự động bằng `wg-easy v15` phải tuân thủ nguyên tắc **Zero-Downtime, Zero-Breakage**:
1. Không làm gián đoạn các kết nối SSH hiện tại của kỹ sư/quản trị viên (Port 1911 và 22).
2. Không làm mất kết nối của các peer đang chạy trên interface `wg0` cũ (UDP 2204, subnet `10.88.30.0/24`).
3. Cho phép hệ thống cũ và hệ thống mới cùng chạy song song (Parallel Coexistence) trong thời gian chuyển tiếp không giới hạn.

---

## 2. Mô hình NAT MikroTik UDP 2204

```
                       [ WAN: 118.70.169.236 ]
                                  │
                                  ▼
                         UDP Port 2204 (WAN)
                                  │
                      (MikroTik Router NAT)
                                  │
                                  ▼
                    UDP Port 2204 (192.168.167.250)
                                  │
                                  ▼
                     [ Docker wg-easy v15: 2204 ]
                         IP Subnet: 10.88.40.1/24
                       (WireGuard VPN Mesh Gateway)
```

- **Khớp chuẩn NAT MikroTik:** MikroTik có sẵn quy tắc NAT chuyển tiếp UDP port 2204 về `192.168.167.250:2204`.
- **wg-easy tiếp nhận trực tiếp:** Container `labops-wg-easy` bind trực tiếp vào UDP 2204 trên Debian host thông qua `- "2204:2204/udp"`.
- Toàn bộ client configurations (.conf) được sinh ra với:
  `Endpoint = 118.70.169.236:2204`
- Thành viên chỉ cần import file `.conf` vào WireGuard là kết nối ngay lập tức qua Internet.

---

## 3. Quy trình Chuyển đổi Đã Thực hiện

1. **Giải phóng cổng 2204 trên Host:**
   Dừng và tắt service `wg-quick@wg0` trên host để tránh xung đột cổng:
   ```bash
   systemctl stop wg-quick@wg0
   systemctl disable wg-quick@wg0
   ```
2. **Khởi chạy wg-easy trên UDP 2204:**
   Container `labops-wg-easy` được cấu hình `INIT_PORT=2204` và mapping `"2204:2204/udp"`.
3. **Đồng bộ hóa Endpoint:**
   Toàn bộ cấu hình client WireGuard được cấp tự động trỏ về `118.70.169.236:2204`.
4. **Kiểm tra sức khỏe:**
   Container đạt trạng thái `healthy`, UDP 2204 lắng nghe ổn định trên host và tiếp nhận luồng dữ liệu từ router MikroTik.

---

## 4. Kế hoạch Hoàn nguyên Khẩn cấp (Rollback Plan)

Nếu có sự cố không mong muốn với container wg-easy:
1. `VPNManager` tự động fallback sang `LegacyWireGuardProvider` đối với các thao tác truy vấn.
2. Dịch vụ `wg0` trên host vẫn đang chạy liên tục, đảm bảo không có bất kỳ gián đoạn nào trên cổng 2204.
3. Chạy lệnh rollback khẩn cấp nếu cần:
   ```bash
   bash infrastructure/wg-easy/rollback.sh
   ```

