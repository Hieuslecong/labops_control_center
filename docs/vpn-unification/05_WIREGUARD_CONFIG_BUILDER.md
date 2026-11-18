# THIẾT KẾ BỘ TẠO CẤU HÌNH WIREGUARD VÀ COMPANION SSH CONFIG
## 05_WIREGUARD_CONFIG_BUILDER.md

### 1. Vai trò của `WireGuardConfigBuilder`

`WireGuardConfigBuilder` (`backend/app/services/wireguard_config_builder.py`) đảm nhiệm việc:
1. Xác thực quyền tải cấu hình: Kiểm tra `actor_username == member_username` hoặc `actor_role == ADMIN`. Chặn đứng hành vi tải chéo cấu hình giữa các thành viên (`FORBIDDEN_CROSS_USER_ACCESS`).
2. Lấy cấu hình thô an toàn từ Docker `wg-easy`.
3. Kiểm tra tính toàn vẹn của PublicKey và Public Endpoint từ Gateway runtime.
4. Ghi đè `AllowedIPs` và `Endpoint` chuẩn xác theo kết quả của `VPNRouteResolver`.
5. Sinh companion OpenSSH config định dạng chuẩn (~/.ssh/config) và đính kèm vào cuối file `.conf` dưới dạng comment.

### 2. Cấu trúc tệp cấu hình WireGuard (.conf) đầu ra

Dưới đây là một tệp `.conf` hoàn chỉnh được sinh cho Researcher gán trên máy chủ Workstation:

```ini
[Interface]
PrivateKey = <Client_Private_Key_Hidden>
Address = 10.88.40.5/32
DNS = 1.1.1.1

[Peer]
PublicKey = <Gateway_Server_Public_Key_From_WgEasy>
Endpoint = 118.70.169.236:2205
AllowedIPs = 10.88.40.0/24, 192.168.167.251/32

# ==============================================================================
# LabOps V2 Companion SSH Connection Info
# HostName 192.168.167.251
# Port 22011
# User hieulc
# ==============================================================================
```

### 3. Cấu trúc Companion SSH Config đi kèm

Ngoài ra, builder sinh ra snippet OpenSSH cấu hình riêng cho `~/.ssh/config`:

```sshconfig
# OpenSSH Client Configuration — LabOps V2
# Lưu vào ~/.ssh/config trên máy cá nhân
Host labops-hieulc
    HostName 192.168.167.251
    Port 22011
    User hieulc
    IdentityFile ~/.ssh/id_ed25519_hieulc
    IdentitiesOnly yes
    ServerAliveInterval 60
    ServerAliveCountMax 3
```

Khi đó, thành viên chỉ cần bật WireGuard client và gõ:
```bash
ssh labops-hieulc
```
để kết nối trực tiếp vào workspace của mình mà không cần nhớ IP hoặc Port phức tạp.
