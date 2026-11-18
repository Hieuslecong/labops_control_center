# ĐẶC TẢ BỘ PHÂN GIẢI LỘ TRÌNH ĐỘNG THEO MÁY CHỦ (VPN ROUTE RESOLVER)
## 04_DYNAMIC_HOST_ROUTE_RESOLVER.md

### 1. Vấn đề giải quyết

Trong mô hình phòng Lab phân tán:
- Máy chủ Rack (`track`, `192.168.167.250`) chứa 3 GPU A30.
- Máy chủ Workstation (`workstation`, `192.168.167.251`) chứa 1 GPU A30 và là Primary Control Plane.

Nếu cấu hình VPN trả về dải `AllowedIPs` tĩnh hoặc sai máy chủ:
1. Client cố gắng SSH tới `192.168.167.251` nhưng AllowedIPs chỉ mở `192.168.167.250/32` -> Gói tin bị kernel client drop, SSH timeout.
2. Cho phép client mở toàn bộ dải mạng LAN (`192.168.167.0/24`) vi phạm nguyên tắc **Zero Trust Least Privilege**, tạo nguy cơ quét cổng ngang giữa các workspace.

### 2. Thuật toán phân giải định tuyến (Resolution Logic)

`VPNRouteResolver` (`backend/app/services/vpn_route_resolver.py`) thực hiện quy trình 4 bước:

```text
[Yêu cầu cấp cấu hình]
         │
         ▼
[Kiểm tra trạng thái Member] ──► (SUSPENDED / ARCHIVED) ──► Ném lỗi MEMBER_INACTIVE (403)
         │
         ▼
[Phân định vai trò (Role)]
   ├── Role = ADMIN
   │      └── AllowedIPs = 10.88.40.0/24, 192.168.167.250/32, 192.168.167.251/32
   │          SSH Host = Host IP chỉ định (hoặc Workstation mặc định), Port = 1911/1912 (Host Admin SSH)
   │
   └── Role = RESEARCHER
          ├── Truy vấn Workspace trong WorkspaceController
          │      ├── Nếu gán Workstation:
          │      │      AllowedIPs = 10.88.40.0/24, 192.168.167.251/32
          │      │      SSH Host = 192.168.167.251, Port = Workspace Port (22010 - 29999)
          │      │
          │      └── Nếu gán Rack:
          │             AllowedIPs = 10.88.40.0/24, 192.168.167.250/32
          │             SSH Host = 192.168.167.250, Port = Workspace Port (22010 - 29999)
          │
          └── Yêu cầu truy cập Host trái phép ──► Ném lỗi UNAUTHORIZED_HOST_ROUTE (403)
```

### 3. Ma trận kết quả phân giải mẫu

| Nhóm người dùng | Máy chủ gán | `AllowedIPs` được sinh ra | SSH Host | SSH Port | Cổng cấm xuất hiện |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Researcher A** | `workstation` | `10.88.40.0/24, 192.168.167.251/32` | `192.168.167.251` | `22011` | `1911, 1912, 192.168.167.250` |
| **Researcher B** | `track` (Rack) | `10.88.40.0/24, 192.168.167.250/32` | `192.168.167.250` | `22012` | `1911, 1912, 192.168.167.251` |
| **Admin** | Đa máy chủ | `10.88.40.0/24, 192.168.167.250/32, 192.168.167.251/32` | `192.168.167.251` | `1912` | Không hạn chế |

### 4. Dynamic IP qua `HostRegistry`

Khi địa chỉ IP của một máy chủ thay đổi trong hạ tầng phòng Lab:
- Quản trị viên chỉ cần cập nhật trong `HostRegistry` (tệp `hosts.json` hoặc database).
- Toàn bộ cấu hình VPN được sinh ra sau đó sẽ tự động phản ánh địa chỉ IP mới mà không cần chỉnh sửa bất kỳ dòng mã nguồn nào.
