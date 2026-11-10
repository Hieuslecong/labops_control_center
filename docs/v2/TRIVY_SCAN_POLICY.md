# CHÍNH SÁCH QUÉT AN TOÀN MÃ NGUỒN & CONTAINER VỚI TRIVY (TRIVY SCAN POLICY V2)

## 1. Máy Trạng thái Quét Mã nguồn (`backend/app/services/security_scanner.py`)
Mỗi yêu cầu quét đi qua đúng chuỗi trạng thái:
```
SUBMITTED -> FETCHING -> SCANNING -> POLICY_EVALUATION -> PASSED | REVIEW_REQUIRED | REJECTED | FAILED
```
- **Đầu vào bắt buộc**:
  - `repo_url`: Chỉ chấp nhận kho mã HTTPS từ `github.com` hoặc `gitlab.com` (`^https://(github\.com|gitlab\.com)/...`), chặn hoàn toàn `file://`, `ssh://` hoặc đường dẫn nội bộ.
  - `commit_sha`: Bắt buộc chuỗi hex đầy đủ **40 ký tự** (`^[0-9a-f]{40}$`) để kết quả quét gắn chặt với một phiên bản mã nguồn bất biến.
- **Không thực thi mã không tin cậy**: Hệ thống chỉ gọi phân tích tĩnh `trivy repo --scanners vuln,secret,misconfig --commit <sha>`, tuyệt đối không chạy `setup.py`, `Makefile` hay bất kỳ script nào trong kho mã.

---

## 2. Quy tắc Đánh giá Chính sách (`labops-policy-2026.11-v1`)
- **`REJECTED` (Từ chối tự động)**: Phát hiện bất kỳ khóa bí mật bị lộ (`secrets > 0`) HOẶC có lỗ hổng mức `CRITICAL > 0`.
- **`REVIEW_REQUIRED` (Cần quản trị viên duyệt thủ công)**: Không có `CRITICAL`/secret nhưng có lỗ hổng mức `HIGH > 0` hoặc lỗi cấu hình mức `HIGH/CRITICAL > 0`.
- **`PASSED` (Đạt tự động)**: Không có secret, không có lỗ hổng hay lỗi cấu hình mức `CRITICAL`/`HIGH`.
- **Fail-Closed khi thiếu Trivy**: Nếu máy chủ chưa cài binary `trivy`, bản quét chuyển sang `FAILED` với lỗi `NOT_CONFIGURED: chưa cài Trivy trên máy chủ quét`. Quản trị viên **bị chặn ở tầng code** (`409 Conflict`) nếu cố phê duyệt (`APPROVED`) một bản quét đang ở trạng thái `FAILED` hoặc `REJECTED`.

---

## 3. Tuyên bố Giới hạn Trung thực về Chỉnh sửa Mã nguồn Sau Khi Quét
Mọi bản ghi quét và giao diện người dùng đều hiển thị rõ:
> *"Kết quả chỉ áp dụng cho đúng commit SHA đã quét; nếu nghiên cứu viên sửa đổi mã nguồn trực tiếp bên trong workspace qua SSH sau khi quét, phần thay đổi đó không nằm trong phạm vi chứng nhận của bản quét."*

