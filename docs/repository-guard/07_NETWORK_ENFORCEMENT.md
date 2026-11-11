# LABOPS REPOSITORY GUARD — NETWORK EGRESS ENFORCEMENT PILOT (PHASE 9 & 19)
**Scope:** Pilot Docker Workspace Container Only (`ws-pilot`)  
**Safety Protocol:** Strict Zero-Interruption on Production GPU Jobs & VPN  

---

## 1. NGUYÊN TẮC AN TOÀN

1. **Tuyệt đối không sửa đổi tường lửa toàn cục của Host**: Không chặn các cổng của hệ điều hành Debian host hoặc interface `wg0` của WireGuard `wg-easy`.
2. **Không ảnh hưởng các workspace khác**: Chỉ áp dụng cho đúng địa chỉ IP của container thí điểm thông qua chain cô lập `LABOPS-PILOT-EGRESS` được gắn vào hook `DOCKER-USER`.
3. **Rollback 100% trong 1 lệnh**: Mọi thay đổi đều được dọn dẹp sạch sẽ khi chạy kịch bản `scripts/pilot_workspace_rollback.sh`.

---

## 2. CHÍNH SÁCH KIỂM SOÁT MẠNG THÍ ĐIỂM

| Luồng Mạng (Traffic Flow) | Đích Đến / Giao Thức | Hành Động | Giải Thích |
|---|---|---|---|
| **Internal DNS** | UDP/TCP Cổng 53 | **ACCEPT** | Cho phép container phân giải tên miền cần thiết. |
| **LabOps Gateway Agent** | TCP Cổng 9100, 8000 | **ACCEPT** | Cho phép container giao tiếp với CLI `labops clone` và BFF Control Plane. |
| **Direct Git SSH** | TCP Cổng 22 | **REJECT** | Chặn hoàn toàn thao tác clone/push qua SSH trực tiếp từ workspace. |
| **Direct GitHub HTTPS** | TCP Cổng 443 đến GitHub CIDRs | **REJECT** | Chặn thao tác `git clone https://github.com/...` trực tiếp bỏ qua cổng kiểm duyệt. |

---

## 3. KỊCH BẢN THỰC THI & ROLLBACK

- **Kích hoạt chính sách thí điểm**:
  ```bash
  sudo ./scripts/pilot_workspace_egress.sh ws-pilot
  ```
- **Hủy bỏ và khôi phục trạng thái ban đầu**:
  ```bash
  sudo ./scripts/pilot_workspace_rollback.sh
  ```
