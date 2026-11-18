# PHÂN TÍCH NHẤT QUÁN CỔNG UDP VÀ ĐỊNH TUYẾN NAT MIKROTIK
## 07_PORT_AND_NAT_CONSISTENCY.md

### 1. Hiện trạng xung đột cổng UDP 2204 vs 2205

Lịch sử phát triển để lại sự không nhất quán giữa các cổng:
- `2204`: Cổng truyền thống của `legacy-wg0` trên host kernel.
- `2205`: Cổng được phân bổ cho container Docker `wg-easy`.
- Tệp `compose.yaml` cũ có dòng:
  ```yaml
  ports:
    - "2204:2204/udp"
    - "2205:2204/udp"
  ```
  Nếu dịch vụ `wg-quick@wg0` trên host đang lắng nghe trên `0.0.0.0:2204`, Docker daemon sẽ báo lỗi:
  `Error response from daemon: driver failed programming external connectivity on endpoint labops-wg-easy: Bind for 0.0.0.0:2204 failed: port is already allocated`.

### 2. Chuẩn hóa trong LabOps V2

Trong `feature/wireguard-unification`, tệp `infrastructure/wg-easy/compose.yaml` đã được tái cấu trúc:
```yaml
services:
  wg-easy:
    environment:
      - WG_PORT=${WG_EASY_UDP_PORT:-2205}
      - INIT_PORT=${WG_EASY_UDP_PORT:-2205}
    ports:
      - "${WG_EASY_UDP_PORT:-2205}:${WG_EASY_UDP_PORT:-2205}/udp"
      - "${WG_EASY_WEB_PORT:-51821}:51821/tcp"
```
Quy ước:
- Mặc định sử dụng cổng `2205` cho `wg-easy`.
- Không bind kép cổng `2204` và `2205`.

### 3. Cấu hình NAT trên MikroTik Router

Để gói tin WireGuard từ Internet đi tới đúng VPN Gateway:

```routeros
# Kiểm tra rule hiện tại
/ip firewall nat print where comment~"WireGuard"

# Thêm/Cập nhật rule NAT cho wg-easy (Cổng 2205)
/ip firewall nat add chain=dstnat action=dst-nat to-addresses=192.168.167.250 to-ports=2205 protocol=udp dst-port=2205 comment="LabOps V2 wg-easy Gateway"

# Rule mở firewall forward
/ip firewall filter add chain=forward action=accept protocol=udp dst-port=2205 dst-address=192.168.167.250 comment="Allow wg-easy traffic"
```

Khi legacy host `wg0` được decommission hoàn toàn, nếu muốn quay lại dùng cổng `2204`, chỉ cần đặt `WG_EASY_UDP_PORT=2204` trong `.env` và đổi port trên MikroTik.
