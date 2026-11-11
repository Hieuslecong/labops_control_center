# LABOPS REPOSITORY GUARD — APPROVED ARTIFACT STORE (PHASE 8 & 19)
**Architecture:** Immutable Content-Addressed Storage  
**Digest Standard:** SHA-256 Checksum  

---

## 1. THIẾT KẾ KHO LƯU TRỮ ARTIFACT ĐÃ PHÊ DUYỆT

Để ngăn chặn việc quét lại lặp đi lặp lại cùng một commit SHA giữa các thành viên trong phòng lab, hệ thống duy trì một **Approved Artifact Store** cục bộ:
- **Đường dẫn**: `$LABOPS_DATA_DIR/approved_store/`
- **Quy tắc đặt tên tệp**: Content-Addressed theo chữ ký số SHA-256: `<sha256_digest>.tar.gz`
- **Chỉ mục Index (`index.json`)**:
  ```json
  [
    {
      "canonical_repo": "https://github.com/gin-gonic/gin",
      "commit_sha": "a1b2c3d4e5f6789012345678901234567890abcd",
      "artifact_digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
      "artifact_path": "/tmp/labops-repositoryguard/approved_store/e3b0c442....tar.gz",
      "artifact_size": 2450190,
      "policy_version": "labops-repo-policy-2026.11-v2",
      "scanner_version": "0.76.2",
      "db_version": "2026-11-10T15:01:55Z",
      "approved_at": "2026-11-11T22:30:00Z",
      "approved_by": "BOT_AUTO_APPROVAL"
    }
  ]
  ```

---

## 2. QUY TRÌNH KIỂM CHỨNG TOÀN VẸN KHI PHÂN PHỐI

Trước khi giải nén bất kỳ gói mã nguồn nào vào workspace:
1. Đọc stream của tệp artifact và tính toán SHA-256 checksum trực tiếp.
2. So sánh checksum thực tế với `expectedDigest` lưu trong chỉ mục đã duyệt.
3. Nếu phát hiện bất kỳ sai lệch nào (dấu hiệu tệp bị sửa đổi hoặc TOCTOU attack):
   - Hủy bỏ ngay lập tức tiến trình phân phối.
   - Ghi nhật ký lỗi `TOCTOU / Integrity Failure` vào Audit Log.
   - Trả về mã lỗi 400/500 cho client.
