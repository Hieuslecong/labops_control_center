# HƯỚNG DẪN TRIỂN KHAI & VẬN HÀNH LABOPS CONTROL CENTER V2 (DEPLOYMENT GUIDE)

## 1. Kiểm tra Tiền Triển khai (Preflight Check)
Trước mọi lần khởi chạy hoặc nâng cấp, thực thi kịch bản kiểm tra an toàn:
```bash
./scripts/preflight_check.sh
```
Kịch bản này chỉ đọc thông tin hệ thống (`python3`, `nvidia-smi`, `trivy`, `004_labops_v2_complete.sql`, cấu hình cổng `docker-compose.yml`), tuyệt đối không chạm vào dịch vụ đang chạy.

---

## 2. Cấu hình Biến Môi trường (`.env`)
1. Sao chép tệp mẫu:
   ```bash
   cp .env.example .env
   ```
2. Khởi tạo các khóa bí mật tối thiểu 32 byte ngẫu nhiên:
   ```bash
   python3 -c "import secrets; print('LABOPS_SESSION_SECRET=' + secrets.token_hex(32))"
   python3 -c "import secrets; print('LABOPS_ADMIN_API_TOKEN=' + secrets.token_hex(32))"
   python3 -c "import secrets; print('LABOPS_NODE_AGENT_TOKEN=' + secrets.token_hex(32))"
   ```
3. Khai báo khóa Clerk (`NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY`) và mật khẩu PostgreSQL (`POSTGRES_PASSWORD`).
4. Giữ `LABOPS_MODE=production`, `LABOPS_ALLOW_MOCK_DATA=false`, `NEXT_PUBLIC_DEMO_MODE=false`.

---

## 3. Khởi chạy Dịch vụ
### Phương án A: Chạy bằng Docker Compose (Khuyến nghị cho Control Plane)
```bash
docker compose up -d --build
```
- PostgreSQL chỉ lắng nghe tại `127.0.0.1:5432`.
- Backend FastAPI chỉ lắng nghe tại `127.0.0.1:8000` (trình duyệt giao tiếp qua BFF `/api/labops/*` của Next.js).

### Phương án B: Chạy trực tiếp trên Host
```bash
./start.sh
```

