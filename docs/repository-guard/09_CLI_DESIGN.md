# LABOPS REPOSITORY GUARD — CLI DESIGN (PHASE 4 & 19)
**Binary:** `bin/labops`  
**Target:** Researcher Workspaces & Terminal Usage  

---

## 1. CÚ PHÁP VÀ LỆNH HỖ TRỢ

```bash
# 1. Clone an toàn qua Security Gateway
labops clone <repo_url> [--ref <ref>] [--dest <path>]

# 2. Liệt kê các yêu cầu kiểm duyệt gần đây
labops repo list

# 3. Xem trạng thái chi tiết của một yêu cầu
labops repo status <request_id>

# 4. Phân phối thủ công một artifact đã duyệt vào thư mục
labops repo deliver <request_id> <dest_directory>

# 5. Xem phiên bản
labops version
```

---

## 2. TRẢI NGHIỆM TERMINAL THỰC TẾ

```text
==========================================================
🛡️  LabOps Repository Guard — Automated Security Gateway
==========================================================
Kho mã:   https://github.com/gin-gonic/gin
Nhánh/Ref: v1.9.1
Người gửi: researcher
----------------------------------------------------------
Mã yêu cầu: req-b2a8c4f109de
Đang xử lý quy trình kiểm duyệt an toàn...
🔍 Đang xác minh nguồn kho mã và phân giải commit SHA...
⚡ Đã gắn commit SHA: d354a480d0d5d4d5e6b7c8a9f0e1d2c3b4a5f6e7
📦 Đang kiểm tra bộ nhớ đệm kho mã đã được phê duyệt...
📥 Cache Miss -> Đang tải về vùng Quarantine cô lập (No-Exec, Limit 500MB)...
🔬 Đang chạy Trivy Security Scan (Lỗ hổng CVE, Secrets, Misconfig)...
⚖️  Đang đánh giá theo chính sách bảo mật LabOps...
----------------------------------------------------------
✅ [AUTO_APPROVED] Kho mã đạt 100% tiêu chuẩn an toàn LabOps!
🔒 Chữ ký số SHA-256 Digest: 8f4a2b1c9d...
🚀 Đang phân phối mã nguồn đã xác minh vào: gin ...
🎉 Hoàn tất phân phối mã! Thư mục đã sẵn sàng để nghiên cứu.
```
