# Kiểm thử bảo mật — 2026-11-10

PASS_AUTOMATED: helper lọc job theo principal và revoke key không tuyên bố đã xác minh; test security hiện có kiểm tra chữ ký token, hết hạn và owner ở cấp hàm.

NOT_VERIFIED: IDOR qua HTTP, vai trò Clerk/backend, tài khoản suspend và thu hồi token, cô lập container, SSH thực, agent impersonation và các API còn lại. BFF chưa đồng bộ role với backend và backend chưa kiểm tra revocation trên mỗi request. SSH key còn có thể được dùng trên runtime cho đến khi được thu hồi thực tế; PENDING_REVOKE không phải bảo vệ truy cập.
