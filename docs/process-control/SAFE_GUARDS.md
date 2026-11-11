# PROCESS CONTROL SAFE GUARDS & WHITELIST SPECIFICATION

**Tài liệu kỹ thuật:** Quy chuẩn bảo vệ an toàn hệ thống và danh sách trắng tiến trình bất khả xâm phạm  
**Phiên bản:** 2.0  
**Ngày phê duyệt:** 11/11/2026  

---

## 1. DANH SÁCH TRẮNG BẢO VỆ TUYỆT ĐỐI (PROTECTED SYSTEM PROCESSES)

Nghiêm cấm tuyệt đối mọi hành vi gửi tín hiệu dừng (`SIGTERM`, `SIGINT`, `SIGKILL`) từ Control Plane hoặc Agent tới các tiến trình và dịch vụ sau:

| Tên tiến trình / Dịch vụ | Đặc điểm nhận diện | Lý do bảo vệ an toàn |
| :--- | :--- | :--- |
| **Linux Init / Systemd** | `PID == 1`, tên `systemd`, `init` | Tiến trình khởi tạo gốc của hệ điều hành. Dừng PID 1 sẽ gây Kernel Panic |
| **Kernel Threads** | `PPID == 2` hoặc `PID == 2`, `kthreadd`, `kworker/*` | Luồng nhân hệ điều hành |
| **Host SSH Daemon** | Lắng nghe port `1911`, tiến trình `sshd: /usr/sbin/sshd -D` | Cổng kết nối duy nhất của quản trị viên tới máy chủ vật lý |
| **LabOps Go Node Agent** | Tên `labops-agent`, port `9100`, cmdline chứa `bin/labops-agent` | Daemon giám sát và điều phối phần cứng / GPU / cgroups |
| **LabOps Python Backend** | Lắng nghe port `8000`, cmdline chứa `uvicorn`, `app.main:app` | Control Plane phục vụ toàn bộ API hệ thống |
| **Next.js Web Server** | Lắng nghe port `3000`, cmdline chứa `next-server`, `next dev`, `next start` | Giao diện điều khiển Web của người dùng |
| **Docker & Containerd** | `dockerd`, `containerd`, `docker-proxy`, `/usr/sbin/dockerd` | Hạ tầng container của toàn bộ lab |
| **WireGuard VPN** | Container `labops-wg-easy`, port `2204/udp`, `51821/tcp` | Kênh VPN nội bộ để các thành viên truy cập máy chủ |
| **Cơ sở dữ liệu & Lưu trữ** | File locks `.db`, SQLite/PostgreSQL, ZFS/NFS daemons | Bảo đảm toàn vẹn dữ liệu |

---

## 2. CƠ CHẾ THẨM ĐỊNH TỰ ĐỘNG (MULTI-LAYER VALIDATION)

Mọi yêu cầu dừng tiến trình phải vượt qua 4 tầng kiểm tra liên tiếp trước khi một lệnh `syscall.Kill` được gửi:

```
[Admin UI / API Request]
          │
          ▼
┌─────────────────────────────────────────────────────────────┐
│ TẦNG 1: XÁC THỰC VÀ PHÂN QUYỀN (Auth & RBAC)                 │
│ - Actor phải có quyền role: ADMIN                           │
│ - Có Bearer Token hoặc Session hợp lệ                       │
│ - Có confirmation token hoặc xác nhận rủi ro dữ liệu        │
└─────────────────────────────────────────────────────────────┘
          │ (Hợp lệ)
          ▼
┌─────────────────────────────────────────────────────────────┐
│ TẦNG 2: THẨM ĐỊNH PHẠM VI TIẾN TRÌNH (Ownership Validation) │
│ - Tiến trình phải thuộc MEMBER_PROCESS hoặc RESEARCH_JOB   │
│ - Tiến trình PHẢI nằm trong cgroup của Workspace Container  │
│   HOẶC do thành viên lab sở hữu                             │
│ - Từ chối nếu classification == SYSTEM_PROCESS              │
└─────────────────────────────────────────────────────────────┘
          │ (Hợp lệ)
          ▼
┌─────────────────────────────────────────────────────────────┐
│ TẦNG 3: BỘ LỌC DANH SÁCH TRẮNG (Whitelist Enforcement)     │
│ - Kiểm tra PID != 1, PPID != 2                             │
│ - Đối chiếu tên file thực thi và port lắng nghe             │
│ - Từ chối nếu cmdline khớp danh sách dịch vụ cốt lõi        │
└─────────────────────────────────────────────────────────────┘
          │ (Hợp lệ)
          ▼
┌─────────────────────────────────────────────────────────────┐
│ TẦNG 4: CHỐNG TÁI SỬ DỤNG PID (PID Reuse Verification)      │
│ - Đọc lại /proc/{pid}/stat tại thời điểm microsecond thực thi│
│ - Đối chiếu start_time (jiffies) khớp 100% với ProcessRef   │
│ - Nếu sai lệch: HỦY NGAY LẬP TỨC (Fail-Closed)             │
└─────────────────────────────────────────────────────────────┘
          │ (Hợp lệ)
          ▼
[Gửi tín hiệu SIGTERM (grace period 10s) -> SIGKILL nếu cần]
```

---

## 3. QUY TRÌNH DỪNG CÂY TIẾN TRÌNH (PROCESS TREE TERMINATION)

1. Khi dừng một tác vụ nghiên cứu gồm nhiều worker (ví dụ: `torchrun` sinh ra 4 sub-processes huấn luyện):
   - Đọc cây quan hệ cha - con từ `/proc` (hoặc thông qua PGID - Process Group ID).
   - Gửi `SIGTERM` tới toàn bộ nhóm tiến trình (`syscall.Kill(-pgid, syscall.SIGTERM)` hoặc từng child PID).
   - Đợi tối đa **10 giây** (Grace Period).
   - Sau thời gian chờ, quét lại nếu còn tiến trình con chưa kết thúc $\rightarrow$ gửi `SIGKILL` dứt điểm.
2. Không cho phép gửi `SIGKILL` tới nhóm tiến trình nếu trong cây tiến trình có chứa bất kỳ tiến trình hệ thống nào.
