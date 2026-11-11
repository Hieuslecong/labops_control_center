# LABOPS PROCESS CONTROL — PRODUCTION RUNBOOK

**Sổ tay vận hành:** Hướng dẫn quản trị và xử lý sự cố điều khiển tiến trình trên máy chủ Production  
**Phiên bản:** 2.0  
**Ngày phê duyệt:** 11/11/2026  

---

## 1. QUY TRÌNH DỪNG TIẾN TRÌNH TREO HOẶC CHIẾM DỤNG TÀI NGUYÊN

Khi một thành viên lab chạy script bị tràn RAM (OOM) hoặc chiếm 100% VRAM GPU mà không giải phóng:

1. **Bước 1 — Xác định tiến trình:**
   - Truy cập Dashboard LabOps tại mục **"Tác vụ & Tiến trình"**.
   - Sử dụng bộ lọc: Chọn Máy chủ (`Server 01 — Rack`), chọn GPU bị chiếm dụng hoặc tìm theo username thành viên.
   - Nhấp vào **"Xem cây tiến trình"** để xác nhận xem script có chạy multi-process (`torchrun` / worker) hay không.
2. **Bước 2 — Thực hiện Dừng an toàn (Graceful Stop):**
   - Nhấp vào nút **"Dừng"** (màu cam).
   - Nhập lý do (ví dụ: *"Tiến trình chiếm VRAM không hoạt động > 4 giờ, giải phóng tài nguyên"*).
   - Bấm xác nhận. Hệ thống sẽ gửi tín hiệu `SIGTERM` và chờ 10 giây.
3. **Bước 3 — Buộc dừng cưỡng chế (Force Kill nếu cần):**
   - Nếu sau 15 giây tiến trình vẫn không thoát (trạng thái hiển thị `Không phản hồi`), nút **"Buộc dừng"** (màu đỏ) sẽ được kích hoạt.
   - Bấm **"Buộc dừng"**, tích chọn hộp kiểm xác nhận rủi ro và bấm thực thi `SIGKILL`.
   - Hệ thống sẽ tiêu diệt tiến trình gốc và toàn bộ cây tiến trình con trong container.

---

## 2. QUY TRÌNH KHẨN CẤP KHI AGENT KHÔNG PHẢN HỒI

Nếu Go Node Agent trên port 9100 bị treo hoặc không thể kết nối:
1. SSH trực tiếp vào máy chủ qua port quản trị `1911`:
   ```bash
   ssh -p 1911 aiot-1@192.168.167.250
   ```
2. Kiểm tra log của Go Agent:
   ```bash
   ps aux | grep labops-agent
   tail -n 50 /var/log/labops-agent.log # hoặc kiểm tra terminal chạy agent
   ```
3. Khởi động lại Go Agent:
   ```bash
   kill $(pgrep labops-agent)
   ./bin/labops-agent &
   ```
4. Xác nhận trạng thái hoạt động:
   ```bash
   curl -s http://127.0.0.1:9100/health
   ```

---

## 3. ROLLBACK VÀ KHÔI PHỤC DỊCH VỤ

Nếu phiên bản mới phát sinh lỗi kết nối:
- Feature flag trong Python backend cho phép chuyển đổi chế độ giám sát từ Go Agent sang chế độ direct `/proc` fallback:
  Thiết lập biến môi trường `LABOPS_USE_GO_AGENT=false` và restart uvicorn backend.
