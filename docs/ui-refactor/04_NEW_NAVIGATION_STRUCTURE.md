# Cấu trúc Điều hướng & Hệ thống Menu Mới (New Navigation Structure)

**Dự án:** LabOps Control Center  
**Mục tiêu:** Tinh giản tối đa thanh điều hướng, loại bỏ tiền tố số thứ tự (`1.`, `2.`), phân tách triệt để giữa Admin và Researcher.

---

## 1. Cấu trúc Thanh Điều hướng Quản trị viên (Admin Navigation)

Được tối giản từ 11 mục rườm rà thành đúng **7 nhóm chức năng quản trị mạch lạc**, tuân thủ phân tầng hạ tầng enterprise:

```
[LABOPS CONTROL CENTER - ADMIN]
│
├── 1. TỔNG QUAN (Overview)
│   └── Tổng quan Hệ thống                 [/dashboard/overview]
│
├── 2. HẠ TẦNG & PHẦN CỨNG (Infrastructure)
│   └── Máy chủ & GPU                      [/dashboard/servers]
│
├── 3. THÀNH VIÊN & TRUY CẬP (Access Control - Hợp nhất)
│   └── Quản lý Thành viên & Truy cập      [/dashboard/users]
│       (Bao gồm VPN WireGuard, Cấp khóa SSH 1-chạm, Quản lý thiết bị)
│
├── 4. TÀI NGUYÊN & WORKSPACE (Compute & Storage)
│   └── Workspace & Lưu trữ                [/dashboard/resources]
│
├── 5. TÁC VỤ & TIẾN TRÌNH (Workloads)
│   └── Giám sát Tiến trình & Jobs         [/dashboard/jobs]
│
├── 6. GIÁM SÁT & BẢO MẬT (Observability & Security)
│   ├── Số liệu Giám sát Chi tiết          [/dashboard/monitoring]
│   └── An ninh & Quét Mã nguồn            [/dashboard/security]
│
└── 7. QUẢN TRỊ HỆ THỐNG (Administration)
    ├── Lịch hoạt động                     [/dashboard/calendar]
    ├── Báo cáo Sử dụng GPU                [/dashboard/usage]
    ├── Nhật ký Kiểm toán                  [/dashboard/audit-logs]
    └── Cài đặt Hệ thống                   [/dashboard/settings]
```

### Các cải tiến nổi bật trên Sidebar Admin:
* **Loại bỏ hoàn toàn tiền tố số thứ tự thô ráp:** Thay thế các chuỗi như "1. Tổng quan", "2. Quản lý VPN & SSH" bằng nhãn ngữ nghĩa chuẩn enterprise: "Tổng quan Hệ thống", "Máy chủ & GPU", "Quản lý Thành viên & Truy cập".
* **Loại bỏ trùng lặp:** Tuyến đường `/dashboard/vpn-ssh` đã được gỡ bỏ khỏi Sidebar; toàn bộ nghiệp vụ được tích hợp trọn vẹn vào `/dashboard/users`.
* **Chế độ Giả lập Xem trước cho Admin (Admin Role Preview):**
  Admin có thanh chuyển đổi nhanh "Chế độ xem Nghiên cứu viên" ngay dưới Sidebar để kiểm tra giao diện của học viên mà không cần đăng xuất hoặc tạo tài khoản ảo.

---

## 2. Cấu trúc Thanh Điều hướng Nghiên cứu viên (Researcher Portal)

Được tinh gọn hoàn toàn thành **4 nhóm chức năng cốt lõi**:

```
[LABOPS CONTROL CENTER - RESEARCHER]
│
├── CỔNG THÔNG TIN
│   └── Tổng quan của tôi                  [/dashboard/researcher]
│
├── TRUY CẬP & KẾT NỐI
│   └── Kết nối & Bảo mật                  [/dashboard/researcher/connect]
│       (Quy trình 5 bước: WireGuard VPN + Đăng ký SSH Ed25519 + Lệnh SSH động)
│
└── TÀI NGUYÊN CÁ NHÂN
    ├── Workspace của tôi                  [/dashboard/researcher/workspace]
    │   (Container riêng, vCPU/RAM/PIDs, Thư mục /media/LabOps-Data/<username>)
    └── GPU & Tác vụ của tôi               [/dashboard/researcher/jobs]
        (Giữ chỗ GPU độc quyền, Bảng phân tích GPU-hours trung thực)
```

### Ưu điểm vượt trội của Researcher Navigation:
1. **Không rò rỉ bất kỳ thông tin quản trị nào:** Menu Admin bị loại bỏ ngay từ phía Server Component, không render mã HTML và không dùng mẹo ẩn CSS.
2. **Hỗ trợ Sub-routes & URL trực tiếp:** Nghiên cứu viên có thể bookmark hoặc truy cập thẳng các sub-route `/connect`, `/workspace`, `/jobs` mà không bị lỗi 404.
3. **Phản ánh đúng danh tính:** Hiển thị chính xác tên người dùng, username và avatar từ Clerk session thay vì thông tin hardcode.

