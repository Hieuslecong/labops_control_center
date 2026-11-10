# TÍCH HỢP ARCANE DOCKER MANAGEMENT (ARCANE INTEGRATION V2)

## 1. Mục tiêu & Phiên bản Ghim
- **Phiên bản tương thích ghim**: Arcane `v1.1.0` (`PINNED_ARCANE_VERSION = "v1.1.0"` trong `backend/app/adapters/arcane_adapter.py`).
- **Mục đích**: Cung cấp cho **Quản trị viên (Admin)** giao diện và API chuẩn để giám sát môi trường Docker, danh sách container, image, volume, network và compose stack trên các máy chủ tính toán.

## 2. Ranh giới An ninh Bắt buộc
1. **Chỉ dành cho Admin (`require_admin`)**: Endpoint `GET /api/v1/v2/containers/summary` bắt buộc vai trò `ADMIN`. Mọi lời gọi từ `RESEARCHER` đều bị từ chối với mã `403 Forbidden` (đã được kiểm chứng tự động trong `test_v2_comprehensive.py`).
2. **Không bao giờ phơi Docker Socket cho Nghiên cứu viên**:
   - `security_boundary.researcher_access = False`
   - `security_boundary.docker_socket_exposed_to_researcher = False`
   - Mọi workspace của nghiên cứu viên đều bị `WorkspaceController` chặn cứng việc mount `/var/run/docker.sock`.

## 3. Hành vi Trung thực Khi Chưa Cấu hình
Khi `ARCANE_BASE_URL` và `ARCANE_API_KEY` chưa được khai báo trong `.env`, adapter trả về trạng thái `NOT_CONFIGURED` và danh sách rỗng, tuyệt đối không bịa danh sách container giả.

