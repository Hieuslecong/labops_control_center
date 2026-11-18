# 10 — BÁO CÁO ZALO BOT AI ASSISTANT & BÁO CÁO TUẦN

- **Branch**: `hardening/labops-v2`
- **Thành phần liên quan**:
  - `backend/app/services/zalo_assistant/{ai_gateway_client,bot_core,quota_ledger,report_generator,service,worker}.py`
  - `backend/app/api/v1/endpoints/ai_assistant.py`
  - `backend/app/db/models.py` (`ZaloOutboxRecord`)
  - `backend/tests/{test_zalo_ai_assistant,test_gpu_allocations_grouped,test_milestone2_hardening}.py`

---

## 1. Các Vấn đề Đã Khắc phục

1. **Sửa triệt để lỗi 9Router `'NoneType' object has no attribute 'strip'` (`ai_gateway_client.py`)**:
   - Khi 9Router trả về cấu trúc phản hồi có `"content": None` (hoặc phản hồi tool-call / lỗi rỗng), trước đây logic `.strip()` trực tiếp gây crash khi xử lý `/ask`.
   - Đã bổ sung chuẩn hóa kiểu an toàn trước khi gọi `.strip()`, lọc chuỗi qua `_sanitize_ai_content` và ràng buộc URL 9Router mặc định ở localhost (`127.0.0.1:20128`) chống SSRF.
2. **Bảo mật Webhook & Single-Consumer Polling (`worker.py`, `bot_core.py`)**:
   - `ZaloPollingWorker` dùng file-lock (`fcntl`) đảm bảo tại một thời điểm chỉ có một worker tiêu thụ hàng đợi lệnh Zalo, tránh trùng lặp phản hồi.
   - `/ask`, `/jobs`, `/status`, `/alloc`, `/report` dùng dữ liệu định danh phần cứng `workstation` / `WS-GPU-01` và bộ lọc UID >= 1000 kèm gom nhóm worker PyTorch DataLoader (`bot_core.py`).
3. **Kiểm soát Quota Zalo Basic & Outbox Bền vững (`quota_ledger.py`)**:
   - Áp dụng `ZaloOutboxRecord` trong DB kết hợp sổ cái tháng `QuotaLedger`: fail-closed (`PersistenceCorruptionError`) nếu dữ liệu JSON hỏng, giữ ngân sách dự phòng cho cảnh báo critical (`test_quota_ledger_critical_reserve`), chống gửi trùng bằng `idempotency_scope`.
4. **Báo cáo Tuần Deterministic (`report_generator.py`)**:
   - Sinh báo cáo tuần dựa trên số liệu thật (không dùng prompt AI bịa thông số), và tách riêng quyền read-only tool cho trợ lý (`test_read_only_tools_restriction`).

---

## 2. Kết quả Kiểm thử

| Suite | Kết quả | Nội dung xác minh |
|---|---|---|
| `pytest backend/tests/test_zalo_ai_assistant.py` | **5/5 PASS** | `test_quota_ledger_limits`, `test_quota_ledger_critical_reserve`, `test_bot_core_commands`, `test_report_generator`, `test_read_only_tools_restriction` |
| `pytest backend/tests/test_gpu_allocations_grouped.py` | **5/5 PASS** | Lệnh `/alloc` định dạng nhóm theo GPU và lọc bỏ tài khoản hệ thống |
| `pytest backend/tests/test_milestone2_hardening.py` (`TestMilestone2ZaloAnd9RouterHardening`) | **PASS** | Outbox fail-closed, 9Router SSRF guard, null-content safety không ném `AttributeError` |

---

## 3. Hạn chế Hiện tại
- Kết nối thực tới API Zalo OA bên ngoài và phản hồi từ 9Router (`:20128`) với model LLM live chưa được gọi trong suite tự động (được kiểm thử bằng fake response / contract test để đảm bảo tái lập deterministically và không đốt quota Zalo Basic thật).
