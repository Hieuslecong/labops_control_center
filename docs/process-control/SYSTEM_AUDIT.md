# SYSTEM AUDIT REPORT — LABOPS PROCESS MONITORING & CONTROL RUNTIME

**Ngày thực hiện:** 11/11/2026  
**Môi trường:** Production AIoT Lab Server (`aiotlab-rack`)  
**Kỹ sư thực hiện:** Principal Go & Linux Systems Architect  
**Trạng thái:** Hoàn tất kiểm toán thực tế  

---

## 1. TỔNG QUAN HẠ TẦNG VÀ DỊCH VỤ ĐANG HOẠT ĐỘNG

| Thành phần | Thông số / Trạng thái quan sát thực tế | Ghi chú & Đánh giá rủi ro |
| :--- | :--- | :--- |
| **Hệ điều hành Host** | Debian GNU/Linux 12 (bookworm), Kernel 6.12.43 | Host vật lý `aiotlab-rack`, IP LAN `192.168.167.250` |
| **CPU & RAM** | AMD EPYC / Multi-core, 125 GiB RAM vật lý | Dư dả tài nguyên cho tác vụ ML và container |
| **GPU Subsystem** | 3 × NVIDIA A30 24GB (Driver 550.120, CUDA 12.4) | Hiện tại không có tiến trình compute nào chiếm VRAM |
| **Host SSH Daemon** | `0.0.0.0:1911`, `[::]:1911` (PID 4249) | **CRITICAL:** Tuyệt đối không được can thiệp hoặc kill |
| **LabOps Go Node Agent** | `127.0.0.1:9100` (PID 983391, `bin/labops-agent`) | Chạy nền với CPU ~0.2%, RAM ~19MB |
| **LabOps Python Backend** | `0.0.0.0:8000` (PID 983411, 1002458) | FastAPI + Uvicorn service |
| **Next.js Web Frontend** | `*:3000` (PID 983461) | Node.js Next.js 16 App Router |
| **VPN WireGuard (wg-easy)** | UDP 2204, container `labops-wg-easy` | Quản lý tunnel VPN cho các thành viên lab |
| **Member Workspaces** | 11 containers Docker (`ws-testacc`, `ws-canary_a`...) | Cung cấp SSH trên dải port 22001-22011 |

---

## 2. NGUYÊN NHÂN CÁC LỖI QUAN SÁT TRƯỚC ĐÂY

### 2.1. Bug "4 tác vụ đang huấn luyện" dù chỉ có dịch vụ hệ thống
- **Hiện tượng:** Dashboard hiển thị KPI 4 tác vụ đang chạy (training), nhưng khi xem chi tiết danh sách thì gồm: `python -m uvicorn app.main:app`, tiến trình Next.js worker, hoặc script nội bộ.
- **Nguyên nhân kỹ thuật gốc rễ:**
  Trong file `backend/app/adapters/process_monitor_adapter.py`:
  ```python
  RESEARCH_NAME_KEYWORDS = {'python', 'python3', 'pt_data_worker', 'torchrun', ...}
  is_research = (
      is_gpu or
      name.lower() in RESEARCH_NAME_KEYWORDS or
      any(k in name.lower() for k in RESEARCH_NAME_KEYWORDS) or
      cmd_str.endswith(".py") or ".py " in cmd_str
  )
  ```
  Logic trên gộp tất cả tiến trình Python chạy trên máy chủ (kể cả backend FastAPI server `app.main:app`) thành `is_research = True`. Đồng thời gán `workspace = ws-{username}` (`ws-aiot-1`), dẫn đến việc backend tự đếm chính nó là tác vụ nghiên cứu AI của thành viên.
- **Biện pháp khắc phục:**
  1. Phân loại nghiêm ngặt: Một tiến trình chỉ là `RESEARCH_JOB` hoặc `MEMBER_PROCESS` khi nó thực sự chạy bên trong cgroup của container workspace thành viên (`docker/<container_id>` hoặc `/lxc.payload.ws-...`), HOẶC do một thành viên lab chạy trong session SSH của họ, HOẶC nằm trong bảng compute process của NVML.
  2. Bất kỳ tiến trình nào chạy dưới user hệ thống (`root`, `aiot-1`) ngoài workspace container với tên tiến trình như `uvicorn`, `python app.main`, `node`, `labops-agent`, `sshd`, `dockerd` phải được phân loại dứt khoát là `SYSTEM_PROCESS`.

### 2.2. Lỗi Workstation hiển thị Online nhưng thực tế chưa xác minh
- **Hiện tượng:** Host Workstation (Server 02) hiển thị nhãn ONLINE trên một số card KPI tổng hợp dù không có kết nối thật.
- **Nguyên nhân:** Dữ liệu fallback trong `_init_data()` hoặc logic mock trả về `online` thay vì `DISCONNECTED` / `NOT_CONFIGURED`.
- **Khắc phục:** Loại bỏ hoàn toàn mock data trong production mode. Nếu server không có agent telemetry phản hồi hợp lệ, hiển thị trung thực `CẦN_XÁC_MINH` hoặc `CHƯA_KẾT_NỐI`.

### 2.3. Rủi ro PID Reuse khi dừng tiến trình
- **Hiện tượng:** Nếu Admin gửi lệnh `kill(pid)` thuần túy theo PID của Linux, trong môi trường đa tiến trình (hoặc container khởi động lại), PID có thể bị tái cấp phát (PID recycle/reuse) cho một tiến trình khác của hệ thống hoặc của thành viên khác.
- **Khắc phục:** Bắt buộc áp dụng định danh an toàn tổng hợp:
  `ProcessRef = {server_id}:{container_id}:{pid}:{start_time}`
  Trước khi gửi bất kỳ tín hiệu `SIGTERM` hoặc `SIGKILL` nào, daemon Go Agent phải đọc lại `/proc/{pid}/stat`, đối chiếu `start_time` (jiffies/starttime) và kiểm tra cgroup/container namespace. Nếu thông tin không khớp, từ chối thực thi ngay lập tức.

---

## 3. KẾT LUẬN VÀ PHẠM VI TRIỂN KHAI
1. Go Agent trên port 9100 sẽ là thành phần duy nhất được cấp quyền tương tác với `/proc`, cgroups và hệ điều hành để đọc và kiểm soát tiến trình.
2. Thiết lập cơ chế bảo vệ danh sách trắng tuyệt đối (Safe Guards) để không một lệnh nào từ UI hay API có thể tắt nhầm các dịch vụ cốt lõi của máy chủ.
