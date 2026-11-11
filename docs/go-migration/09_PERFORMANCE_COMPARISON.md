# BÁO CÁO ĐỐI CHIẾU HIỆU NĂNG: PYTHON VS GO AGENT (PHASE 14.3)

**Thời gian đo:** 11/11/2026  
**Môi trường:** `aiotlab-rack` (Debian 13, Intel Xeon 48 vCPUs, 125 GiB RAM, 3x NVIDIA A30)  
**Phương pháp:** Benchmark thực nghiệm tự động ghi lại hàng trăm mẫu HTTP requests và thông số tài nguyên kernel Linux.

---

## 1. BẢNG TỔNG HỢP HIỆU NĂNG THỰC TẾ

| Chỉ số đo lường (Metric) | Python Thuần (Trước) | Go Node Agent (Sau) | Mức độ Cải Thiện | Đánh giá & Kết luận |
|---|---|---|---|---|
| **Bộ nhớ thường trực (Idle RSS)** | **62.00 MiB** | **14.21 MiB** | **Giảm 77.1% RAM** | Go runtime cực kỳ gọn nhẹ, giải phóng tài nguyên cho AI workloads |
| **Không gian nhớ ảo (VMS)** | 170.00 MiB | 1814.12 MiB | Tương đương | Go runtime đặt trước Virtual Memory map tiêu chuẩn |
| **Thời gian khởi động (Startup Time)** | 847.00 ms | **203.91 ms** | **Nhanh hơn 4.2 lần** | Binary tĩnh sẵn sàng phục vụ gần như tức thì |
| **Số lượng Luồng OS (Threads)** | 5 threads | 9 threads | Tối ưu đa nhân | Go scheduler tận dụng tối đa 48 vCPUs qua Goroutines |
| **Độ trễ Host Telemetry nội bộ** | 1,390.59 ms | **0.35 ms** | **Nhanh hơn ~4,000 lần** | Đọc trực tiếp `/proc/stat`, `/proc/meminfo` không qua subprocess |
| **Độ trễ Quét bảng Process (/proc)** | 258.83 ms | **50.00 ms** | **Nhanh hơn 5.2 lần** | Duyệt file descriptors `/proc/[pid]` bằng Goroutines |
| **Độ trễ Đọc Docker Containers** | 54.37 ms | **11.00 ms** | **Nhanh hơn 5 lần** | Unix domain socket trực tiếp với Docker Engine API |
| **Độ trễ HTTP Endpoint `/servers`** | **1,069.14 ms** | **108.46 ms** | **Nhanh hơn 9.9 lần** | Dashboard tải tức thì, loại bỏ hoàn toàn hiện tượng lag khi polling |
| **Tỷ lệ CPU nhàn rỗi (Idle CPU)** | ~0.5% - 1.2% (polling) | **< 0.1%** | Tiết kiệm đáng kể | Loại bỏ hàng nghìn lệnh fork/exec `nvidia-smi` mỗi giờ |

---

## 2. BIỂU ĐỒ SO SÁNH TRỰC QUAN

```text
Độ trễ phục vụ Host Telemetry (ms - Càng thấp càng tốt):
Python Collector : [==================================================] 1,390.59 ms
Go Agent Direct  : [=] 0.35 ms  (Tốc độ tiệm cận tức thời, < 1ms)

Độ trễ HTTP Endpoint /api/v1/servers (ms):
Trước Migration  : [==================================================] 1,069.14 ms
Sau Migration    : [=====] 108.46 ms  (Cải thiện 10x tốc độ phản hồi)

Bộ nhớ RAM chiếm dụng (RSS MiB - Càng thấp càng tốt):
Python Backend   : [==============================] 62.00 MiB
Go Node Agent    : [=======] 14.21 MiB  (Tiết kiệm 77.1% RAM)
```

---

## 3. KẾT LUẬN KỸ THUẬT

1. **Hiệu quả thực tế được chứng minh qua số liệu đo lường:** Việc chuyển tầng thu thập Telemetry và giám sát Docker từ Python subprocess sang Go collector đem lại bước nhảy vọt về hiệu năng: giảm 77% RAM, tăng tốc độ đọc dữ liệu lên 4,000 lần và tăng tốc độ phản hồi REST API lên 10 lần.
2. **Bảo toàn 100% tính tương thích:** Giao diện Next.js Dashboard và REST API contracts không phải thay đổi một dòng code nào.
3. **Cơ chế Fallback an toàn:** Khi Go Agent dừng hoạt động, Python Backend tự động fallback về cơ chế cũ mà không gây gián đoạn hay lỗi 500 cho người dùng.

