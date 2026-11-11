# LABOPS REPOSITORY GUARD — AUTO-APPROVAL POLICY ENGINE (PHASE 7 & 19)
**Policy Version:** `labops-repo-policy-2026.11-v2`  
**Classification:** Internal Security Standard  

---

## 1. MỤC TIÊU VÀ NGUYÊN TẮC VẬN HÀNH

Giảm thiểu tối đa việc can thiệp thủ công từ Quản trị viên đối với các kho mã an toàn và sạch sẽ, trong khi giữ nguyên tắc **Fail-Closed** tuyệt đối đối với các trường hợp nghi vấn rủi ro hoặc quét chưa hoàn tất.

---

## 2. QUY TẮC ĐÁNH GIÁ VÀ TRẠNG THÁI (STATE MACHINE)

```text
                               +-----------------------+
                               |     Quét Bảo Mật      |
                               +-----------------------+
                                           |
                                           v
                       [Kiểm tra Phạm vi Coverage & Findings]
                                           |
             +-----------------------------+-----------------------------+
             |                             |                             |
      (Vi phạm cấm)                 (Cần đánh giá)                 (Đạt chuẩn 100%)
             v                             v                             v
       [ REJECTED ]               [ REVIEW_REQUIRED ]             [ AUTO_APPROVED ]
  • Có rò rỉ Secrets/Keys        • Có Lỗ hổng mức HIGH          • 0 Secrets
  • Có Lỗ hổng CRITICAL          • Có Misconfig mức HIGH        • 0 Lỗ hổng CRITICAL
  • Chặn tải và phân phối        • Coverage bị INCOMPLETE       • 0 Lỗ hổng HIGH
                                 • Chờ Quản trị viên duyệt      • 0 Misconfig HIGH
                                                                • Coverage POLICY_COMPLETE
                                                                • Tự động chuyển Delivery
```

---

## 3. NGOẠI LỆ DO ADMIN PHÊ DUYỆT (MANUAL OVERRIDE)

Khi một yêu cầu rơi vào trạng thái `REVIEW_REQUIRED`, Quản trị viên có thể:
1. Kiểm tra chi tiết các CVE và vị trí tệp tin trong drawer chi tiết trên Dashboard.
2. Nhập văn bản lý do giải trình phê duyệt ngoại lệ (ví dụ: *"Lỗ hổng nằm trong công cụ test môi trường dev, không ảnh hưởng runtime"*).
3. Quyết định được lưu vào cơ sở dữ liệu với trạng thái `MANUALLY_APPROVED`, kèm timestamp và username của Admin để đảm bảo trách nhiệm giải trình.
