# LABOPS REPOSITORY GUARD — SCANNER DESIGN (PHASE 6 & 19)
**Version:** 2.0.0  
**Tools:** Trivy Static Analysis v0.76.2 + OpenSSF Scorecard Signal Engine  

---

## 1. THIẾT KẾ CÁC CÔNG CỤ QUÉT TĨNH (STATIC SCANNERS)

### 1.1. Trivy Static Filesystem Scan
Trivy được thực thi trực tiếp trên thư mục snapshot tĩnh trong vùng Quarantine (`quarantine/<request-id>/source`):
- **Tham số vận hành**:
  ```bash
  trivy fs --quiet --format json --scanners vuln,secret,misconfig <source_dir>
  ```
- **Các thành phần được kiểm tra**:
  1. **Vulnerabilities (`vuln`)**: Kiểm tra CVE trong các tệp quản lý dependency (`requirements.txt`, `poetry.lock`, `package-lock.json`, `go.sum`, `Cargo.lock`, `Gemfile.lock`).
  2. **Secrets (`secret`)**: Quét phát hiện 100+ mẫu khóa API (AWS keys, GitHub tokens, Private keys, JWT tokens, Database credentials).
  3. **Misconfigurations (`misconfig`)**: Kiểm tra cấu hình Dockerfile, Kubernetes YAML, Terraform và cấu hình bảo mật sai quy cách.

### 1.2. OpenSSF Scorecard Integration
- Đánh giá chỉ số rủi ro chuỗi cung ứng mã nguồn đối với kho lưu trữ công cộng GitHub:
  - Branch protection
  - Vulnerability reporting process
  - Dependency pinning
  - Maintained project activity
  - Dangerous workflows & token permissions
- **Xử lý ngoại lệ**:
  - Khi không cài đặt binary `scorecard` hoặc bị GitHub API rate-limit -> Trả về trạng thái `NOT_CONFIGURED` hoặc `UNKNOWN` với điểm số `-1`.
  - Không bao giờ mặc định điểm 0 hoặc 10 khi thiếu dữ liệu thực tế.

---

## 2. SCAN COVERAGE MANIFEST

Mọi bản ghi quét đều gắn liền với bảng kê phạm vi quét (Coverage Manifest):
```json
{
  "commit_sha": "a1b2c3d4e5f6789012345678901234567890abcd",
  "vulnerability_scan": "COMPLETED",
  "secret_scan": "COMPLETED",
  "misconfiguration_scan": "COMPLETED",
  "submodules": "NOT_PRESENT",
  "lfs": "NOT_PRESENT",
  "overall_coverage": "POLICY_COMPLETE"
}
```

Nếu `vulnerability_scan` bị lỗi hoặc bỏ qua, `overall_coverage` lập tức chuyển thành `INCOMPLETE` và chặn cơ chế tự động duyệt.
