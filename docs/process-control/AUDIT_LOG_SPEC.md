# LABOPS PROCESS CONTROL — AUDIT LOG SPECIFICATION

**Tài liệu kỹ thuật:** Quy chuẩn lưu vết kiểm toán (Audit Trail) cho các thao tác điều khiển tiến trình  
**Phiên bản:** 2.0  
**Ngày phê duyệt:** 11/11/2026  

---

## 1. NGUYÊN TẮC LƯU VẾT BẢO MẬT
Mọi hành vi gửi tín hiệu can thiệp vào tiến trình của thành viên (dừng an toàn, buộc dừng, dừng toàn bộ cây) đều bắt buộc phải được ghi nhật ký kiểm toán không thể xóa bỏ (Append-Only Audit Log) trên hệ thống.

Mỗi bản ghi kiểm toán ghi nhận tối thiểu:
1. `timestamp`: Thời điểm chính xác theo chuẩn UTC ISO 8601.
2. `actor`: Định danh người thực hiện (Admin username, IP nguồn, User-Agent).
3. `action`: Hành động (`PROCESS_SIGTERM`, `PROCESS_SIGKILL`, `PROCESS_TREE_TERMINATE`).
4. `target`: Thông tin định danh chi tiết của tiến trình (`ref`, `pid`, `pgid`, `command_line`, `start_time`).
5. `owner`: Thành viên sở hữu tiến trình (`username`, `member_id`, `workspace`).
6. `server`: Máy chủ nơi tiến trình đang thực thi (`server_id`, `hostname`).
7. `reason`: Lý do quản trị viên nhập khi thực hiện hành động.
8. `result`: Kết quả xử lý (`SUCCESS`, `FAILED`, `REJECTED_PROTECTED`, `PID_RECYCLED_ABORT`).

---

## 2. CẤU TRÚC BẢN GHI JSON SCHEMA

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "title": "ProcessControlAuditEvent",
  "type": "object",
  "required": [
    "id",
    "timestamp",
    "actor",
    "action",
    "target_ref",
    "target_pid",
    "target_user",
    "server_id",
    "result"
  ],
  "properties": {
    "id": { "type": "string", "description": "UUID của sự kiện audit" },
    "timestamp": { "type": "string", "format": "date-time" },
    "actor": {
      "type": "object",
      "properties": {
        "username": { "type": "string" },
        "role": { "type": "string" },
        "ip_address": { "type": "string" }
      }
    },
    "action": {
      "type": "string",
      "enum": ["PROCESS_SIGTERM", "PROCESS_SIGKILL", "PROCESS_TREE_TERMINATE", "PROCESS_KILL_REJECTED"]
    },
    "target_ref": { "type": "string" },
    "target_pid": { "type": "integer" },
    "target_user": { "type": "string" },
    "target_cmdline": { "type": "string" },
    "target_workspace": { "type": "string" },
    "server_id": { "type": "string" },
    "reason": { "type": "string" },
    "result": { "type": "string" },
    "error_details": { "type": ["string", "null"] }
  }
}
```

---

## 3. LƯU TRỮ VÀ XUẤT BÁO CÁO
- Các sự kiện kiểm toán được ghi tức thì vào cơ sở dữ liệu `data/audit_events.json` đồng thời xuất log cấu trúc ra `syslog` / standard error của service với tiền tố `[LABOPS-AUDIT-PROCESS]`.
- Quản trị viên có thể xem lịch sử thao tác này tại màn hình "Nhật ký kiểm toán" (Audit Logs) trên giao diện Dashboard.
