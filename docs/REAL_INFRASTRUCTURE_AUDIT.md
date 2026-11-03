# BÁO CÁO KIỂM TOÁN HẠ TẦNG THỰC TẾ (REAL INFRASTRUCTURE AUDIT)
## LabOps Control Center — Hệ thống Quản trị Cụm Máy chủ AI Research
**Thời điểm thực hiện kiểm toán:** 2026-11-03 23:55:00 UTC+7  
**Môi trường thực thi:** Ubuntu Linux 22.04.5 LTS (Jammy Jellyfish)  
**Tài khoản thực hiện:** `hieulc` (UID 1051, GID 1055)

---

## 1. XÁC ĐỊNH DANH TÍNH MÁY CHỦ THỰC TẾ (HOST IDENTITY)

Căn cứ theo tài liệu kiến trúc phòng Lab:
- **Server 01 (Track):** Trang bị 3 × NVIDIA A30 24 GB (Tổng 72 GB VRAM).
- **Server 02 (Workstation):** Trang bị 1 × NVIDIA A30 24 GB (Tổng 24 GB VRAM).

Kết quả kiểm toán phần cứng thực tế qua `nvidia-smi -L` và `lscpu`:
- Hệ thống máy chủ đang chạy ứng dụng phát hiện **chính xác 1 GPU vật lý NVIDIA A30 24 GB**.
- **Kết luận định danh:** Máy chủ thực tế tại chỗ là **Server 02 — Workstation**.
- Máy chủ **Server 01 — Track** là máy chủ từ xa kết nối qua WireGuard VPN.

---

## 2. KIỂM TOÁN PHẦN CỨNG & HỆ ĐIỀU HÀNH THỰC TẾ

### 2.1. Hệ điều hành & Nhân Kernel
- **Hệ điều hành:** Ubuntu 22.04.5 LTS (Jammy Jellyfish)
- **Kernel version:** `5.15.0-126-generic` (x86_64)
- **Hostname:** `sandbox` (Local Environment)
- **Quyền hạn tiến trình:** Non-root (`uid=1051(hieulc)`, `gid=1055(hieulc)`)

### 2.2. Năng lực Vi xử lý (CPU)
- **Model:** 2 × Intel(R) Xeon(R) Silver 4210R CPU @ 2.40GHz
- **Số socket:** 2 sockets vật lý
- **Số lõi vật lý:** 20 cores (10 cores / socket)
- **Số luồng xử lý (vCPUs):** 40 vCPUs (Hyper-Threading enabled)
- **NUMA Nodes:** 2 nodes (Node 0: CPUs 0-9, 20-29; Node 1: CPUs 10-19, 30-39)
- **Tần số cơ bản:** 2.40 GHz (Turbo tối đa 3.20 GHz)

### 2.3. Bộ nhớ RAM & Swap
- **Tổng dung lượng RAM vật lý:** 125 GiB (134,768,082,944 bytes)
- **RAM đang sử dụng:** ~47 - 51 GiB
- **RAM khả dụng (Available):** ~73 - 79 GiB (~58% khả dụng)
- **Bộ nhớ đệm (Buff/Cache):** ~68 - 70 GiB
- **Dung lượng Swap:** 8.0 GiB (Đã dùng 8.0 GiB)

### 2.4. Ổ đĩa & Hệ thống tệp (Storage & Filesystems)
- **Phân vùng hệ thống gốc (`/`):** 
  - Thiết bị: `/dev/mapper/ubuntu--vg-ubuntu--lv` (LVM Ext4)
  - Tổng dung lượng: 877 GB
  - Đã sử dụng: 748 GB (85%)
  - Khả dụng: 84 GB
- **Phân vùng lưu trữ phụ:**
  - Thiết bị: `/dev/sdb` (Ext4)
  - Điểm gắn kết: `/hdd1/hieulc/tmp`
  - Tổng dung lượng: 916 GB
  - Đã sử dụng: 403 GB (44%)
  - Khả dụng: 467 GB
- **Thiết bị khối khác (Block Devices):** `sdc`, `sdd`, `sde` hiện diện nhưng chưa mount điểm lưu trữ riêng.
- **Trạng thái ZFS:** Gói `zfs` và công cụ `zpool` **chưa được cài đặt** trên máy chủ này.

---

## 3. KIỂM TOÁN TĂNG TỐC ĐỒ HỌA NVIDIA GPU

### 3.1. Thông số Phần cứng GPU
- **Số lượng GPU phát hiện:** 1 GPU
- **Model:** NVIDIA A30 Tensor Core GPU (HBM2)
- **GPU Index:** `0`
- **PCI Bus ID:** `00000000:D8:00.0`
- **GPU UUID:** `GPU-1f6ba03b-45e9-8d99-1d7d-7c76412f3cfc`
- **Tổng VRAM vật lý:** 24,576 MiB (24 GB HBM2)
- **NVIDIA Driver Version:** `595.71.05`
- **CUDA Version:** `13.2`
- **NVIDIA Container Toolkit:** Đã cài đặt phiên bản `1.19.1-1`

### 3.2. Trạng thái Vận hành & Tải thực tế
- **VRAM đang sử dụng thực tế:** 13,410 MiB (~54.6%)
- **VRAM còn trống:** 11,166 MiB (~45.4%)
- **Mức độ tải GPU (Utilization):** ~34%
- **Nhiệt độ GPU (Temperature):** 70°C - 73°C
- **Công suất tiêu thụ (Power Draw):** 69.36 W (Giới hạn trần: 165.00 W)
- **Chế độ MIG (Multi-Instance GPU):** Đang tắt (`Disabled`)

### 3.3. Các tiến trình Nghiên cứu Đang chạy (Compute Applications)
Truy vấn trực tiếp qua `nvidia-smi --query-compute-apps`:
| PID | Process Name | VRAM sử dụng | Phân loại quyền sở hữu |
|---|---|---|---|
| `3505602` | `[External / Namespace]` | 2,002 MiB | **Chưa xác định** (Tác vụ nghiên cứu bên ngoài) |
| `73683` | `[External / Namespace]` | 2,002 MiB | **Chưa xác định** (Tác vụ nghiên cứu bên ngoài) |
| `1833592` | `[External / Namespace]` | 9,386 MiB | **Chưa xác định** (Tác vụ nghiên cứu bên ngoài) |
- **Tổng VRAM chiếm dụng:** 13,390 MiB (~13.4 GB)
- **Chính sách an toàn tuyệt đối:** Các tiến trình này là workload nghiên cứu AI đang chạy thực tế trên host. Hệ thống LabOps Control Center **tuyệt đối không can thiệp, không dừng (kill) hoặc thay đổi cấu hình CUDA của các tiến trình này**.

---

## 4. KIỂM TOÁN VIRTUALIZATION & MẠNG (INCUS & WIREGUARD)

### 4.1. Incus Container Runtime
- Lệnh `incus` và dịch vụ Incus daemon: **Chưa cài đặt trên máy chủ Workstation**.
- Thư mục dữ liệu: Tồn tại vết `/var/snap/lxd`, nhưng snap/incus chưa kích hoạt.
- **Xử lý trong Chế độ Sản xuất (Production Mode):**
  - Trạng thái Incus trên Workstation phải hiển thị chính xác: **"Chưa cấu hình"** hoặc **"Chưa cài đặt"**.
  - Không được hiển thị số container ảo giả lập (như ws-hieulc, ws-namnv).

### 4.2. WireGuard VPN & Kết nối Cụm Server Track
- Công cụ `/usr/bin/wg` đã có sẵn trong hệ thống (`wireguard-tools` v1.0.20210914).
- Giao diện mạng `wg0` hiện chưa được cấu hình hoạt động trong namespace hiện tại.
- **Server 01 (Track):** Do chưa có tunnel WireGuard hoạt động đến dải IP `10.200.0.1`, máy chủ Track phải được đánh dấu: **"Mất kết nối" (Chưa kết nối WireGuard VPN)**. Tuyệt đối không sinh dữ liệu giả định 3 GPU cho Server Track.

### 4.3. Cơ sở dữ liệu PostgreSQL
- Gói `postgresql-14` đã được cài đặt trên máy chủ Ubuntu.
- Cổng 5432 cục bộ hiện chưa lắng nghe kết nối (`localhost:5432 - no response`).
- **Chiến lược bền vững:** Dữ liệu cấu hình tài khoản, chính sách bảo mật SSH và nhật ký kiểm toán sẽ được quản lý bền vững qua tệp dữ liệu lưu trữ cấu trúc (Durable Structured Store) của dịch vụ backend, đồng thời sẵn sàng kết nối PostgreSQL khi dịch vụ được kích hoạt.

---

## 5. RỦI RO TRIỂN KHAI & NGUYÊN TẮC BẢO VỆ HẠ TẦNG

1. **Rủi ro gián đoạn nghiên cứu AI:**
   - Đang có ít nhất 3 tiến trình CUDA tiêu thụ 13.4 GB VRAM trên GPU A30 duy nhất của Workstation.
   - **Cam kết:** Không khởi động lại máy chủ (`reboot`), không nâng cấp driver NVIDIA, không bật MIG làm reset GPU state, không kill bất kỳ PID nào.
2. **Rủi ro dữ liệu rác (False Metrics):**
   - Đóng hoàn toàn Demo Mode trong chế độ sản xuất.
   - Khi thiếu dịch vụ Incus hoặc ZFS, hệ thống trả về mã trạng thái rõ ràng, chặn các thao tác can thiệp ổ đĩa nguy hiểm.
3. **Rủi ro phân quyền:**
   - Mọi tài khoản cấp phát phải áp dụng chính sách nghiêm ngặt: Non-root, không có quyền sudo (`sudoPrivilege: DENIED`), bắt buộc OpenSSH Dual-Auth (`AuthenticationMethods publickey,password`).

---

## 6. LỘ TRÌNH KÍCH HOẠT SẢN XUẤT (PRODUCTION ACTIVATION ROADMAP)

1. **Cấu hình môi trường:** Kích hoạt `LABOPS_MODE=production`, `LABOPS_ALLOW_MOCK_DATA=false`, `NEXT_PUBLIC_DEMO_MODE=false`.
2. **Bộ điều hợp Giám sát Máy chủ (Host Monitor Adapter):** Triển khai adapter đọc dữ liệu trực tiếp từ `psutil` và `/proc`.
3. **Bộ điều hợp Giám sát GPU (Real NVML Adapter):** Triển khai adapter đọc trực tiếp từ `nvidia-smi` với UUID `GPU-1f6ba03b-45e9-8d99-1d7d-7c76412f3cfc`, cập nhật dung lượng VRAM thực tế và các tiến trình đang chạy.
4. **Bộ điều hợp Incus Fail-Closed:** Khi Incus chưa khả dụng, báo lỗi chính xác `Chưa cấu hình Incus` thay vì sinh dữ liệu container giả lập.
5. **Đồng bộ Giao diện Dashboard:** Chuyển đổi toàn bộ UI frontend đọc dữ liệu thực từ backend API, loại bỏ huy hiệu Demo, hiển thị cảnh báo trung thực cho Server Track (Mất kết nối).

