# Review mã nguồn — 2026-11-10

Cơ sở: branch feature/labops-control-center, commit 5d12c02c600753aa55421b3f033016f9865921fe và working tree có thay đổi từ trước.

## IMPLEMENTED_NOT_INTEGRATION_TESTED

- `src/app/api/labops/_lib.ts`: bỏ mặc định ADMIN, chỉ suy ra ADMIN từ Clerk organization admin. Chưa có ánh xạ thành viên backend hoặc kiểm tra thu hồi theo từng request; không xem P0 hoàn tất.
- `backend/app/services/job_access.py`, `backend/app/api/v1/endpoints/jobs.py`, `backend/app/main.py`: lọc danh sách job theo principal trước query filter; helper có test, FastAPI và fallback HTTP chưa được test trực tiếp. Những endpoint dữ liệu khác chưa được kiểm toán đầy đủ.
- `backend/app/services/vpn_ssh_service.py`: yêu cầu thu hồi giữ PENDING_REVOKE, không tuyên bố đã xóa key. Chưa có agent thu hồi hay xác minh đăng nhập.
- `backend/app/services/labops_service.py`: không coi key đã đăng ký là bằng chứng privilege isolation.
- `backend/app/api/dependencies.py`: không gán audit actor tùy ý từ header admin token; định danh chỉ còn admin-service, chưa phải audit người dùng.
- `backend/app/main.py`: không khởi chạy stdlib fallback ngoài demo khi thiếu FastAPI/Uvicorn.

## NOT_IMPLEMENTED

PostgreSQL làm nguồn trạng thái cho toàn bộ domain, agent theo host, workspace runtime, scheduler phân tán, kiểm tra revoke thực và các integration E2E chưa hoàn thành. Không triển khai hoặc thay đổi NetBird.
