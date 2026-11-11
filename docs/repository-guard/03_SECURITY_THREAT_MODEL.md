# LABOPS REPOSITORY GUARD — SECURITY THREAT MODEL (PHASE 14 & 19)
**Version:** 2.0.0  
**Target:** Supply Chain & External Source Code Ingress  
**Model:** STRIDE & Zero-Trust External Input  

---

## 1. THREAT ACTORS & ATTACK SURFACE

### 1.1. Threat Actors
1. **Malicious External Contributor / Attacker**:
   - Chèn mã độc, reverse shells, crypto-miners vào GitHub repositories hoặc dependencies.
   - Sửa commit sau khi yêu cầu quét (TOCTOU).
2. **Untrusted / Compromised Researcher Account**:
   - Cố tình hoặc vô ý tải mã độc vào môi trường tính toán để khai thác GPU hoặc leo thang đặc quyền.
   - Thử nghiệm vượt tường lửa (Egress bypass) hoặc tấn công SSRF vào mạng nội bộ LabOps.
3. **Compromised Dependency / Typosquatting**:
   - Thư viện Python (`PyPI`) hoặc npm bị nhiễm mã độc, cài đặt script tự động qua `setup.py` hoặc `install.sh`.

### 1.2. Attack Vectors & Safeguards

| Mã Nguy Cơ | Véc-tơ Tấn Công | Nguy Hiểm Tiềm Tàng | Cơ Chế Phòng Thủ Của LabOps Repository Guard |
|---|---|---|---|
| **THREAT-01** | **TOCTOU Attack** (Time-of-Check to Time-of-Use) | Đánh lừa bot duyệt commit lành tính nhưng tải về commit độc sau đó qua branch pointer. | Bắt buộc ghim (pin) **full 40-hex commit SHA**; tính toán và kiểm tra chữ ký số SHA-256 Content Digest trước khi giải nén vào workspace. |
| **THREAT-02** | **Zip Bomb / Decompression Bomb** | Gửi archive chứa tệp rỗng nén dung lượng khổng lồ gây cạn kiệt đĩa/RAM host. | Áp dụng hạn ngạch nghiêm ngặt trong Quarantine: Tối đa 500 MB dung lượng giải nén và tối đa 30.000 tệp tin; tự động ngắt kết nối nếu vượt ngưỡng. |
| **THREAT-03** | **Symlink Traversal Attack** | Tạo symlink trỏ đến `/etc/shadow`, `/root/.ssh` hoặc Docker socket trong repository. | `PrepareQuarantine` và `DeliverArtifact` phân giải đích đến của toàn bộ symlink; nếu symlink trỏ ra ngoài root của thư mục giải nén, lập tức hủy bỏ và báo động. |
| **THREAT-04** | **Arbitrary Code Execution in Scan Phase** | Repository chứa mã kích hoạt tự động trong `setup.py`, `Makefile`, `npm postinstall`. | Chỉ thực hiện **Static Analysis (SAST)** bằng Trivy trên mã nguồn ở trạng thái tĩnh. Tuyệt đối **không chạy lệnh shell, không build, không cài dependencies** trong quarantine. |
| **THREAT-05** | **SSRF / Internal Network Probing** | URL độc hại như `https://127.0.0.1/...` hoặc `file:///etc/passwd`. | `NormalizeAndValidateURL` chỉ cho phép giao thức `https://` và tên miền `github.com` (hoặc `gitlab.com`); cấm credentials, cấm IP nội bộ, cấm domain cục bộ. |
| **THREAT-06** | **Credential Exposure in Logs / UI** | Khóa API, token phát hiện trong mã bị in ra màn hình hoặc audit log dưới dạng plaintext. | Hàm `maskSecret()` tự động che giấu giá trị bí mật (chỉ giữ 3 ký tự đầu và 3 ký tự cuối, ví dụ `AKI******PLE`). |
| **THREAT-07** | **Scanner Crash Fail-Open** | Scanner gặp lỗi hoặc timeout nhưng hệ thống tự ý phê duyệt do nhánh fallback sai sót. | Động cơ chính sách áp dụng nguyên tắc **Fail-Closed**: nếu một loại quét bắt buộc gặp lỗi (`INCOMPLETE`), trạng thái bắt buộc là `REVIEW_REQUIRED` hoặc `FAILED`, không bao giờ `AUTO_APPROVED`. |
| **THREAT-08** | **Direct Git Bypass** | Thành viên chạy trực tiếp `git clone` trong container bỏ qua cổng kiểm duyệt. | Thí điểm Network Egress Policy (chặn cổng 443 và 22 đến GitHub IP) trên container, chỉ cho phép nhận mã qua `labops clone`. |

---

## 2. KẾT LUẬN & ĐÁNH GIÁ RỦI RO CÒN LẠI

- **Rủi ro còn lại**: Nếu container được cấp quyền truy cập internet không giới hạn đến các dịch vụ chia sẻ tệp bên thứ 3 (ví dụ Google Drive, pastebin), người dùng vẫn có thể copy mã nguồn qua kênh phụ ngoài GitHub.
- **Biện pháp khuyến nghị**: Mở rộng egress whitelist sang toàn bộ outbound traffic ở các giai đoạn tiếp theo (Stage E) sau khi hoàn tất giai đoạn Pilot.

