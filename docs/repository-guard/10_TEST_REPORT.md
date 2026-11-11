# LABOPS REPOSITORY GUARD — TEST REPORT (PHASE 15 & 19)
**Execution Date:** 2026-11-11  
**Environment:** Debian 13 Host (Local Control Plane & Go Agent)  
**Standard:** 100% Real Test Executions (Zero Fake / Mock Approvals)  

---

## 1. TỔNG HỢP KẾT QUẢ KIỂM THỬ

| Nhóm Kiểm Thử | Công Cụ / Suite | Số Test | Kết Quả | Trạng Thái |
|---|---|---|---|---|
| **URL & SSRF Protection** | `go test -v ./internal/repositoryguard` | 7 test cases | 7/7 PASS | **PASS** |
| **Policy Engine & Fail-Closed** | `go test -v ./internal/repositoryguard` | 5 test cases | 5/5 PASS | **PASS** |
| **Approved Cache & Digest** | `go test -v ./internal/repositoryguard` | 3 test cases | 3/3 PASS | **PASS** |
| **Delivery & TOCTOU Integrity** | `go test -v ./internal/repositoryguard` | 2 test cases | 2/2 PASS | **PASS** |
| **Durable Queue Crash Recovery** | `go test -v ./internal/repositoryguard` | 1 test case | 1/1 PASS | **PASS** |
| **Audit Logger System** | `go test -v ./internal/repositoryguard` | 1 test case | 1/1 PASS | **PASS** |
| **All Existing Go Modules** | `go test -v ./...` | 20+ test cases | 100% PASS | **PASS** |
| **Python Scanner Coverage & Fail-Closed** | `backend/tests/test_repository_guard.py` | 5 test cases | 5/5 PASS | **PASS** |
| **Python V2 Scanner Suite** | `backend/tests/test_v2_scanner.py` | 8 test cases | 8/8 PASS | **PASS** |
| **TypeScript Frontend Build** | `tsc --noEmit` | 50+ components | 0 Errors | **PASS** |
| **CLI Binary Compilation** | `go build ./cmd/labops` | Binary `bin/labops` | 0 Errors | **PASS** |

---

## 2. KIỂM THỬ CÁC TÌNH HUỐNG BẢO MẬT BẮT BUỘC (SECURITY TESTS MATRIX)

1. **Repository không tồn tại / URL độc hại**: `PASS` (Trả về lỗi phân giải commit hoặc URL validation trước khi clone).
2. **SSRF / IP nội bộ (`127.0.0.1`, `localhost`)**: `PASS` (Bị chặn ngay tại `NormalizeAndValidateURL`).
3. **Commit SHA không hợp lệ**: `PASS` (Yêu cầu regex 40 hex ký tự).
4. **Scanner Crash / Incomplete Coverage**: `PASS` (Fail-Closed chuyển sang `REVIEW_REQUIRED`, không bao giờ `AUTO_APPROVED`).
5. **Rò rỉ Secrets / Credentials**: `PASS` (Chuyển sang `REJECTED`, đồng thời che giấu `maskSecret` trên UI và logs).
6. **Lỗ hổng CRITICAL**: `PASS` (Chuyển sang `REJECTED`).
7. **Lỗ hổng HIGH**: `PASS` (Chuyển sang `REVIEW_REQUIRED`, yêu cầu Admin phê duyệt ngoại lệ).
8. **TOCTOU / Mismatched Artifact Digest**: `PASS` (`DeliverArtifact` hủy tiến trình nếu digest không khớp).
9. **Durable Queue Crash Recovery**: `PASS` (Các tiến trình bị gián đoạn khi restart server tự động phục hồi thành `FAILED` có thông điệp rõ ràng).
10. **RBAC & Authorization**: `PASS` (Researcher chỉ xem được yêu cầu của mình, Admin mới có quyền quyết định).
