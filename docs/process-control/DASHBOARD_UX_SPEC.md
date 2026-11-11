# LABOPS PROCESS CONTROL — DASHBOARD UX SPECIFICATION

**Tài liệu thiết kế:** Đặc tả giao diện người dùng (UX/UI) Quản trị Tiến trình LabOps  
**Phong cách:** Tiếng Việt chuẩn mực, giao diện sáng (Light Theme / Nền trắng), thanh lịch, trực quan và an toàn  
**Phiên bản:** 2.0  
**Ngày phê duyệt:** 11/11/2026  

---

## 1. NGUYÊN TẮC THIẾT KẾ & TÁI CẤU TRÚC

1. **Gộp chức năng trùng lặp:**
   - Hợp nhất màn hình Quản lý Tác vụ (Jobs) và Giám sát Tiến trình (Processes) thành một trung tâm điều khiển duy nhất: **"Tác vụ & Tiến trình"** (Jobs & Processes).
   - Component bảng tiến trình này có thể được nhúng lại (reusable) trong màn hình **Chi tiết Thành viên** (lọc theo member) và **Chi tiết GPU** (lọc theo GPU).
2. **Loại bỏ Mock Data & Trung thực tuyệt đối:**
   - Không giả lập tiến trình nghiên cứu khi máy chủ chỉ chạy dịch vụ backend.
   - Nếu máy chủ Workstation chưa kết nối agent, hiển thị trạng thái `Chưa kết nối` hoặc `Cần xác minh` màu xám/vàng thay vì `Online` màu xanh giả tạo.
3. **An toàn tối đa trong thao tác (Safety-First UX):**
   - Nút **Dừng (SIGTERM)** hiển thị màu cam/vàng cảnh báo nhẹ, mở Modal yêu cầu xác nhận.
   - Nút **Buộc dừng (SIGKILL)** hiển thị màu đỏ thẫm, kèm biểu tượng cảnh báo nguy hiểm, mở Modal cảnh báo rủi ro mất mát checkpoint / dữ liệu đang huấn luyện.
   - Đối với các tiến trình hệ thống thuộc danh sách bảo vệ (`SYSTEM_PROCESS`), nút thao tác bị vô hiệu hóa (disabled) kèm tooltip: *"Tiến trình hệ thống được bảo vệ — Không thể dừng"*.

---

## 2. CẤU TRÚC BẢNG TIẾN TRÌNH TRUNG TÂM

| Cột | Dữ liệu hiển thị | Tính năng tương tác |
| :--- | :--- | :--- |
| **Thành viên** | Tên thành viên + avatar + username (`@testacc`) | Bấm vào để lọc theo thành viên này |
| **Máy chủ** | Badge: `Server 01 — Rack` / `Server 02 — Workstation` | Badge màu xám viền nét mảnh |
| **Workspace / Container** | Tên workspace (`ws-testacc`) | Hiển thị icon Docker container |
| **Lệnh & Tác vụ** | Tên script (`python train.py`), PID (`102450`), tooltip xem đầy đủ cmdline | Nút copy cmdline nhanh |
| **CPU %** | Tỷ lệ % CPU thời gian thực kèm thanh progress bar nhỏ | Cảnh báo đỏ nếu CPU > 90% |
| **RAM (RSS)** | Dung lượng RAM thực tế (ví dụ: `4.2 GB`) | Đổi đơn vị thông minh (MB/GB) |
| **GPU & VRAM** | Badge GPU (`GPU 0 (A30)`) và VRAM (`8.2 / 24 GB`) | Nếu không dùng GPU: hiển thị `-` |
| **Thời gian chạy** | Thời lượng hoạt động (`02h 15m 30s`) | Start time chi tiết trong tooltip |
| **Phân loại** | Badge: `Tác vụ AI` / `Tiến trình Thành viên` / `GPU Compute` / `Hệ thống` | Phân loại màu sắc rõ ràng |
| **Thao tác** | Nhóm nút: `Xem cây tiến trình`, `Dừng (SIGTERM)`, `Buộc dừng (SIGKILL)` | Disabled nếu là tiến trình hệ thống |

---

## 3. MODAL XÁC NHẬN THAO TÁC

### 3.1. Modal Dừng an toàn (SIGTERM Confirmation)
- **Tiêu đề:** Dừng tác vụ đang chạy
- **Nội dung:**  
  *Bạn có chắc chắn muốn gửi tín hiệu dừng an toàn (SIGTERM) tới tiến trình **[Tên script]** (PID: **[pid]**) của thành viên **[Tên thành viên]**?*  
  *Tiến trình sẽ có 10 giây để lưu trạng thái trước khi kết thúc hoàn toàn.*
- **Trường nhập lý do:** `Lý do dừng (Bắt buộc)`
- **Nút hành động:** `Hủy bỏ` | `Xác nhận dừng (Cam)`

### 3.2. Modal Buộc dừng cưỡng chế (Force Kill Warning Modal)
- **Tiêu đề:** CẢNH BÁO: Buộc dừng tiến trình (SIGKILL)
- **Nội dung:**  
  ⚠️ **CẢNH BÁO NGUY HIỂM:** *Hành động này sẽ gửi tín hiệu SIGKILL lập tức tới tiến trình **[pid]** và toàn bộ tiến trình con. Mọi dữ liệu đang huấn luyện hoặc checkpoint chưa lưu trên đĩa sẽ bị mất vĩnh viễn.*
- **Hộp kiểm xác nhận:**  
  `[x] Tôi hiểu rõ rủi ro và xác nhận buộc dừng tiến trình này.`
- **Nút hành động:** `Hủy bỏ` | `Buộc dừng ngay lập tức (Đỏ nguy hiểm)`
