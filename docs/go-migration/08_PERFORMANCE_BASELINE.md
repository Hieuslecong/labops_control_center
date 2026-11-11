# BÁO CÁO ĐO LƯỜNG HIỆU NĂNG GỐC — PYTHON BASELINE (PHASE 14.1)

**Ngày đo:** 11/11/2026  
**Môi trường thử nghiệm:** `aiotlab-rack` (Debian 13, Intel Xeon 48 vCPUs, 125 GiB RAM, 3x NVIDIA A30)  
**Tình trạng tải máy chủ khi đo:** Bình thường, không có job AI nặng, tải nền ổn định.

---

## 1. THÔNG SỐ RUNTIME CỦA PYTHON BACKEND (FASTAPI / UVICORN)

| Chỉ số đo lường | Giá trị thực tế đo được | Ghi chú & Đơn vị |
|---|---|---|
| **Thời gian khởi động (Startup Time)** | **0.847 giây** | Từ lúc gọi lệnh `python` tới khi sẵn sàng nhận HTTP traffic |
| **Bộ nhớ thực tế sử dụng (RSS - Resident Set Size)** | **62.00 MiB** | Trạng thái nhàn rỗi (idle), chưa có tải kết nối |
| **Bộ nhớ ảo (VMS - Virtual Memory Size)** | **170.00 MiB** | Địa chỉ không gian bộ nhớ ảo |
| **Số lượng Luồng hệ thống (OS Threads)** | **5 threads** | Main thread + async worker threads |
| **Số lượng File Descriptors mở (Open FDs)** | **14 FDs** | Sockets, pipes, log files |
| **Tỷ lệ CPU nhàn rỗi (Idle CPU)** | **0.00%** | Đo trung bình trong khoảng thời gian 3-5 giây |

---

## 2. THỜI GIAN THU THẬP NỘI BỘ (INTERNAL TELEMETRY LATENCY)

Các module Python hiện tại thu thập dữ liệu bằng cách đọc `/proc` kết hợp chạy subprocess:

| Tác vụ thu thập | Thời gian đo được | Chi tiết cơ chế thực thi |
|---|---|---|
| **Host Metrics (`HostMonitorAdapter`)** | **1,390.59 ms** (~1.39 giây) | Gọi 4 lệnh shell con: `nvidia-smi`, `nvidia-smi --query-gpu`, `docker --version`, đọc `/proc/mounts` |
| **Process Metrics (`ProcessMonitorAdapter`)** | **258.83 ms** | Duyệt qua toàn bộ PID trên máy qua `psutil.process_iter()` |
| **GPU Telemetry (`NvmlAdapter`)** | **97.12 ms** | Phân tích output từ `nvidia-smi` cho 3 GPU A30 |
| **Docker Container List (`subprocess docker ps`)** | **54.37 ms** | Fork/exec binary `docker` và parse chuỗi JSON output |

---

## 3. ĐỘ TRỄ HTTP ENDPOINT (HTTP LATENCY BENCHMARK)

Đo lường trên 100 requests tuần tự qua HTTP nội bộ `127.0.0.1:8000`:

### A. Endpoint Telemetry Máy chủ (`GET /api/v1/servers`) — Gọi subprocess
- **Mean (Trung bình):** **1,069.14 ms**
- **Median (p50):** **1,028.96 ms**
- **p95:** **1,392.40 ms**
- **p99:** **1,532.77 ms**
- **Min:** **981.14 ms**
- **Max:** **1,532.77 ms**

> **Nhận xét quan trọng:** Endpoint này mất hơn 1 giây cho mỗi lần request vì backend Python phải spawn nhiều tiến trình con. Khi Dashboard kích hoạt polling định kỳ 5 giây, CPU liên tục phải context switch cho fork/exec.

### B. Endpoint Đọc Dữ liệu Bộ nhớ (`GET /api/v1/users`) — Đọc Store trong RAM
- **Mean (Trung bình):** **2.37 ms**
- **Median (p50):** **2.21 ms**
- **Min:** **1.52 ms**
- **Max:** **7.46 ms**

> **Nhận xét:** Khi dữ liệu đã nằm trong bộ nhớ RAM và không phải gọi subprocess, Python xử lý rất nhanh (~2.3 ms). Do đó, **không cần thiết phải viết lại toàn bộ tầng REST CRUD của Python sang Go**, mà trọng tâm tối ưu phải đặt vào **tầng thu thập Telemetry, Quản lý Process, Giám sát Docker và GPU**.

---

## 4. MỤC TIÊU CẢI THIỆN HIỆU NĂNG KHI CHUYỂN SANG GO AGENT

| Tiêu chí | Python Hiện Tại | Mục tiêu Go Agent | Mức độ cải thiện kỳ vọng |
|---|---|---|---|
| **Bộ nhớ RAM thường trực (RSS)** | 62.0 MiB | **< 15.0 MiB** | Giảm **75%** RAM sử dụng |
| **Thời gian thu thập Host Telemetry** | 1,390 ms | **< 2.0 ms** | Nhanh hơn **> 600 lần** |
| **Thời gian quét Process Table** | 258 ms | **< 15.0 ms** | Nhanh hơn **> 15 lần** |
| **Thời gian đọc Docker Containers** | 54 ms | **< 5.0 ms** (qua Docker Go SDK Unix Socket) | Nhanh hơn **> 10 lần** |
| **Độ trễ phục vụ Telemetry HTTP** | 1,069 ms | **< 10.0 ms** | Cải thiện độ phản hồi Dashboard ngay lập tức |

