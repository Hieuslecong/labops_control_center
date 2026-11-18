# MA TRẬN ÁNH XẠ CỔNG SSH VÀ PHÂN VÙNG BẢO MẬT
## 06_SSH_ENDPOINT_MAPPING.md

### 1. Phân loại cổng SSH trong LabOps V2

Hệ thống phân tách rạch ròi giữa 2 lớp cổng SSH:
1. **Host Admin SSH Port (Cổng quản trị máy chủ vật lý)**:
   - Dành riêng cho Quản trị viên hệ thống có tài khoản root/sudo trên host.
   - Rack Server (`aiotlab-rack`): Cổng `1911`.
   - Workstation (`aiot-ws1`): Cổng `1912`.
   - **Quy tắc an ninh tối cao**: Tuyệt đối không bao giờ cấp cổng 1911 hoặc 1912 cho thành viên nghiên cứu (Researcher).
2. **Workspace Container SSH Port (Cổng môi trường người dùng)**:
   - Dành cho các container/sandbox chạy môi trường PyTorch, CUDA của Researcher.
   - Dải cổng phân bổ: `22010` đến `29999`.
   - Mỗi thành viên có một cổng SSH riêng trỏ trực tiếp vào Docker container cô lập của họ.

### 2. Bảng ánh xạ tổng thể (Mapping Table)

| Đối tượng | Máy chủ mục tiêu | IP truy cập | SSH Port | Loại quyền hạn | Cơ chế xác thực |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Admin Host Access** | Workstation | `192.168.167.251` | `1912` | Full Root / Sudo | Ed25519 Host Keypair |
| **Admin Host Access** | Rack | `192.168.167.250` | `1911` | Full Root / Sudo | Ed25519 Host Keypair |
| **Researcher (ws-hieulc)** | Workstation | `192.168.167.251` | `22011` | Non-privileged container | User Ed25519 Key |
| **Researcher (ws-namnv)** | Rack | `192.168.167.250` | `22012` | Non-privileged container | User Ed25519 Key |
| **Researcher (ws-tuanpm)** | Rack | `192.168.167.250` | `22013` | Non-privileged container | User Ed25519 Key |
| **Researcher (ws-anhlt)** | Workstation | `192.168.167.251` | `22014` | Non-privileged container | User Ed25519 Key |

### 3. Cơ chế cấp phát cổng tự động (Collision Avoidance)

`WorkspaceController.request_workspace` tự động quét các cổng đang được sử dụng trong `workspaces.json` trên cùng host:
```python
used_ports = {w.get("ssh_host_port") for w in state["workspaces"] if w.get("host_id") == host_id}
ssh_port = next(p for p in range(22010, 29999) if p not in used_ports)
```
Điều này đảm bảo không có bất kỳ xung đột cổng (port collision) nào xảy ra giữa các workspace container.
