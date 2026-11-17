# LABOPS REPOSITORY GUARD — ARCHITECTURE DESIGN (PHASE 2)
**Version:** 2.0.0  
**Status:** PARTIAL SOURCE IMPLEMENTATION / RUNTIME NOT VERIFIED
**Authors:** Principal DevSecOps & Security Architect  

> This is a target architecture diagram, not a statement of current deployment. AIOTGIT, scoped request authorization and Git bundle handling have source/unit-test evidence. No production service, scanner worker, terminal broker, durable production store, workspace delivery or egress boundary was exercised.

---

## 1. TỔNG QUAN KIẾN TRÚC HỆ THỐNG

LabOps Automated Repository Security Gateway hướng tới kiểm soát nguồn mã bên ngoài. Source code có các kiểm tra approval/digest; hiện chưa có bằng chứng runtime đủ để khẳng định mọi workspace chỉ nhận mã đã kiểm duyệt.

```text
                      LABOPS DASHBOARD (Next.js 16)
                                    |
                                    v
                       FASTAPI CONTROL PLANE (BFF)
                                    |
                                    v
                         GO REPOSITORY GUARD
                                    |
            +-----------------------+-----------------------+
            |                                               |
     Approved Cache (Hit)                            New Repo (Miss)
            |                                               |
            |                                               v
            |                                     Quarantine Downloader
            |                                       (Isolated Sandbox)
            |                                               |
            |                                               v
            |                                       Security Scanners
            |                                    +----------+----------+
            |                                    |                     |
            |                                  Trivy           OpenSSF Scorecard
            |                               (Vuln/Secret)     (Supply Chain Risk)
            |                                    |                     |
            |                                    +----------+----------+
            |                                               |
            |                                               v
            |                                     Policy Engine (V2)
            |                                               |
            |                         +---------------------+---------------------+
            |                         |                     |                     |
            |                   AUTO_APPROVED        REVIEW_REQUIRED           REJECTED
            |                         |                     |                     |
            |                         |              Admin Dashboard              x
            |                         |            (Exception Review)             |
            |                         +---------------------+                     |
            |                                               |                     |
            v                                               v                     |
   +----------------------------------------------------------------+             |
   |                   APPROVED ARTIFACT STORE                     |             |
   |              (Content-Addressed SHA-256 Digest)               |             |
   +----------------------------------------------------------------+             |
                                    |                                             |
                                    v                                             |
                            LABOPS GO AGENT                                       |
                       (Verified Artifact Delivery)                               |
                                    |                                             |
                                    v                                             |
                       DOCKER WORKSPACE (PILOT)                                   |
                  [Egress Enforcement: GitHub Blocked] <--------------------------+
```

---

## 2. CÁC THÀNH PHẦN NÒNG CỐT

### 2.1. Go Repository Guard Service (`go/internal/repositoryguard/`)
Module Go tích hợp trực tiếp vào Node Agent / Control Plane:
- **`models.go`**: Định nghĩa cấu trúc dữ liệu cho ScanRequest, ApprovalRecord, PolicyVerdict, ArtifactDigest, ScanCoverage.
- **`github.go`**: Chuẩn hóa URL, phân giải Git reference (branch/tag/PR) thành full 40-hex commit SHA. Chống SSRF và URL parsing injection.
- **`downloader.go`**: Quarantine Downloader tải mã về vùng cách ly tạm thời, kiểm tra kích thước tối đa, giới hạn số tệp tin, quét symlinks độc hại trỏ ra ngoài vùng cách ly, chặn thực thi mã (no-exec).
- **`scanner.go`**: Gọi Trivy static scan với đầy đủ kiểm tra: vulnerabilities (`vuln`), leaked credentials/secrets (`secret`), misconfigurations (`misconfig`). Xuất bản scan coverage manifest.
- **`scorecard.go`**: Đánh giá rủi ro chuỗi cung ứng mã nguồn theo tiêu chuẩn OpenSSF Scorecard. Hỗ trợ chế độ fallback an toàn (`UNKNOWN`) khi không có kết nối hoặc bị rate-limit.
- **`policy.go`**: Động cơ chính sách đánh giá kết quả tự động:
  - `AUTO_APPROVED`: Commit SHA xác định, coverage đầy đủ (`POLICY_COMPLETE`), 0 Secrets, 0 Lỗ hổng Critical, 0 High misconfigs.
  - `REVIEW_REQUIRED`: Có lỗ hổng High, nghi ngờ secret cần xác minh, hoặc scan coverage không hoàn chỉnh.
  - `REJECTED`: Có secrets rò rỉ hoặc lỗ hổng Critical bị cấm.
  - `FAILED`: Lỗi mạng, commit không tồn tại, hoặc công cụ quét không khả dụng (Fail-Closed).
- **`cache.go`**: Bộ nhớ đệm các commit đã được phê duyệt dựa trên content digest và policy version.
- **`delivery.go`**: Chứa helper xử lý artifact; direct arbitrary-path delivery bị vô hiệu hóa. AIOTGIT approved-bundle route tồn tại trong source nhưng chưa được kiểm thử end-to-end tới workspace.
- **`audit.go`**: Ghi nhận toàn bộ sự kiện kiểm duyệt, tải về, phê duyệt ngoại lệ và phân phối mã vào hệ thống Audit Log bất biến.

---

## 3. STATE MACHINE VÀ QUY TRÌNH DUYỆT

```text
       SUBMITTED
           |
           v
       VALIDATING  -------> [URL Không Hợp Lệ / SSRF] ------> FAILED
           |
           v
    RESOLVING_COMMIT -----> [Commit Không Tồn Tại] ---------> FAILED
           |
           v
     CHECKING_CACHE ------> [Cache Hit & Valid] ------------> AUTO_APPROVED -> PUBLISHING -> DELIVERED
           | (Cache Miss)
           v
       FETCHING (Quarantine) -> [Vượt Quota / Symlink Độc] -> FAILED / REJECTED
           |
           v
       SCANNING (Trivy + Scorecard)
           |
           v
   POLICY_EVALUATION
           |
           +----------------> [Thỏa tiêu chí Auto-Approve] ----> AUTO_APPROVED -> PUBLISHING -> DELIVERED
           |
           +----------------> [Có rủi ro HIGH / Coverage thiếu] -> REVIEW_REQUIRED -> Chờ Admin duyệt
           |                                                                              |
           |                                                      +--- (Admin Chấp Thuận) -+-> MANUALLY_APPROVED -> DELIVERED
           |                                                      +--- (Admin Từ Chối) ----+-> REJECTED
           |
           +----------------> [Vi phạm nghiêm trọng / Secrets] -> REJECTED
```

---

## 4. WORKSPACE EGRESS BOUNDARY — CHƯA ĐƯỢC KIỂM CHỨNG

- AIOTGIT approval alone cannot prevent direct downloads from a workspace with outbound network access or from a host SSH shell.
- Source attestation accepts REPOSITORY_ENFORCED only for an observed Docker network mode of none, but this does not provision a workspace or create a terminal access channel.
- No pilot container, terminal broker or direct-download bypass test was run. Host firewall/Docker egress scripts are no-op guards.
- Dùng `aiotgit clone` để yêu cầu kiểm duyệt và `aiotgit status` để theo dõi; dùng binary `git` cho các thao tác local. Legacy `labops clone` và `labops repo` commands đã bị disable trong source.
- Trạng thái hiện tại: HOST_ENFORCEMENT_NOT_VERIFIED; host-shell bypass remains possible until the execution boundary is proven.

---

## 5. BẢO MẬT VÀ PHÒNG CHỐNG TẤN CÔNG (THREAT MODELING)

1. **TOCTOU Attack**: Ngăn ngừa bằng cách pin chính xác commit SHA 40 ký tự và tính toán SHA-256 digest của archive tại thời điểm quét. Quá trình phân phối chỉ giải nén archive đã khớp digest.
2. **Malicious Symlink Traversal**: Trước khi lưu trữ hoặc phân phối, quarantine crawler kiểm tra toàn bộ symlinks; bất kỳ symlink nào trỏ ra ngoài root thư mục đều bị từ chối và cảnh báo.
3. **Zip Bomb / Decompression Exhaustion**: Giới hạn kích thước tải về tối đa (ví dụ: 500MB) và số lượng tệp tối đa (50.000 tệp).
4. **Credential Leak Prevention**: Toàn bộ chuỗi tokens/secrets phát hiện trong báo cáo quét được băm hoặc ẩn (masked), không hiển thị plaintext trên giao diện hay audit log.
