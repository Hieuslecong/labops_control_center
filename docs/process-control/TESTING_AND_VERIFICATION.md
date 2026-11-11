# LABOPS PROCESS CONTROL — TESTING & VERIFICATION PLAN

**Kế hoạch kiểm thử:** Quy trình kiểm thử tự động và thủ công cho tính năng giám sát & điều khiển tiến trình  
**Mục tiêu:** Đảm bảo 100% tính an toàn hệ thống, không hồi quy lỗi, không gián đoạn dịch vụ  
**Phiên bản:** 2.0  
**Ngày phê duyệt:** 11/11/2026  

---

## 1. CÁC KỊCH BẢN KIỂM THỬ TỰ ĐỘNG (AUTOMATED TEST SUITE)

Tập trung tại file kiểm thử mới `backend/tests/test_process_control.py`:

| Mã kịch bản | Mô tả kịch bản kiểm thử | Kỳ vọng đạt được |
| :--- | :--- | :--- |
| `TC-PROC-01` | Yêu cầu dừng tiến trình không có xác thực (Unauthorized) | Trả về `401 Unauthorized` |
| `TC-PROC-02` | Người dùng thường (Member) cố dừng tiến trình của thành viên khác | Trả về `403 Forbidden` |
| `TC-PROC-03` | Admin cố gửi lệnh dừng tiến trình hệ thống (host sshd 1911, agent 9100, dockerd) | Bị chặn bởi Safe Guards, trả về `403 Forbidden: PROTECTED_PROCESS_DENIED` |
| `TC-PROC-04` | Kiểm tra cơ chế chống PID reuse (start_time không khớp) | Bị chặn, trả về `409 Conflict: PID_RECYCLED` |
| `TC-PROC-05` | Khởi tạo tiến trình thử nghiệm trong container workspace, Admin gửi SIGTERM | Tiến trình nhận SIGTERM và kết thúc an toàn, ghi nhận audit log |
| `TC-PROC-06` | Tiến trình giả lập bỏ qua SIGTERM (ignore SIGTERM), Admin gửi SIGKILL | Sau thời gian chờ, SIGKILL dứt điểm tiến trình |
| `TC-PROC-07` | Kiểm toán phân loại tiến trình: Uvicorn và Next.js không bị coi là RESEARCH_JOB | Đếm chính xác số lượng tác vụ nghiên cứu thật |

---

## 2. KIỂM THỬ THỰC TẾ TRÊN MÔI TRƯỜNG DEBIAN
1. **Kiểm tra Host SSH:** Chạy script kết nối liên tục vào port `1911` trong suốt quá trình thử nghiệm điều khiển tiến trình để đảm bảo không rớt kết nối.
2. **Kiểm tra WireGuard VPN:** Kiểm tra tunnel UDP `2204` của `labops-wg-easy` không bị ảnh hưởng.
3. **Kiểm tra Docker Engine:** Kiểm tra daemon `dockerd` không bị restart ngoài ý muốn.
4. **Đo hiệu năng Go Agent:**
   - Thời gian phản hồi API `/api/v1/agent/processes` < 5ms.
   - CPU của `labops-agent` < 0.5%.
   - RAM RSS của `labops-agent` < 25MB.
