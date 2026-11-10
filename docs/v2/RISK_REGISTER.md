# SỔ ĐĂNG KÝ RỦI RO VẬN HÀNH & AN NINH (RISK REGISTER — LABOPS V2)

| Mã rủi ro | Mô tả rủi ro | Mức độ | Biện pháp giảm thiểu trong V2 | Trạng thái sau nâng cấp |
|---|---|---|---|---|
| **RISK-01** | Mất kết nối SSH quản trị vào máy chủ vật lý khi cấu hình lại truy cập từ xa | Chí mạng (Critical) | Cấm mọi thao tác tự động sửa `/etc/ssh/sshd_config` của host hoặc restart `sshd` trên cổng 22. Workspace SSH chạy trên dải cổng cô lập `22010–29999`. | Đã kiểm soát (Mitigated) |
| **RISK-02** | Đứt kết nối VPN WireGuard `wg0` đang hoạt động giữa các máy chủ | Chí mạng (Critical) | Không bao giờ gọi `wg-quick down wg0` hoặc ghi đè cấu hình gốc; chỉ thêm/thu hồi từng peer riêng lẻ kèm xác minh dump. | Đã kiểm soát (Mitigated) |
| **RISK-03** | Nghiên cứu viên leo thang đặc quyền qua Docker socket hoặc container `--privileged` | Cao (High) | `WorkspaceController.validate_security_constraints()` chặn cứng `--privileged`, `/var/run/docker.sock`, `/etc`, `/root`, và thư mục của thành viên khác. | Đã kiểm soát & Kiểm thử (`IMPLEMENTED_AND_TESTED`) |
| **RISK-04** | Hai tác vụ chiếm cùng một GPU hoặc ghi đè lên tiến trình nghiên cứu chạy ngoài hệ thống | Cao (High) | `GpuScheduler` khóa file `fcntl`, kiểm tra `active_processes` trực tiếp từ `nvidia-smi` trước khi cho phép giữ `FULL_GPU_EXCLUSIVE` theo UUID. | Đã kiểm soát & Kiểm thử (`IMPLEMENTED_AND_TESTED`) |
| **RISK-05** | Nghiên cứu viên sửa mã nguồn trực tiếp qua SSH sau khi commit đã vượt qua bước quét Trivy | Trung bình (Medium) | Ghi rõ tuyên bố giới hạn (`disclaimer`) trên cả API và giao diện: kết quả Trivy chỉ chứng nhận cho đúng `commit_sha` đã quét; khuyến nghị gắn volume mã nguồn chỉ-đọc cho các lượt chạy kiểm định nghiêm ngặt. | Đã công bố minh bạch |
| **RISK-06** | Môi trường hộp cát (sandbox) hoặc máy chủ chưa cài Trivy / Arcane / Beszel | Trung bình (Medium) | Áp dụng cơ chế fail-closed: trả về `NOT_CONFIGURED` / `FAILED` / `BLOCKED_BY_INFRASTRUCTURE`, tuyệt đối không sinh dữ liệu giả hay báo `PASSED` giả. | Đã kiểm soát & Kiểm thử (`IMPLEMENTED_AND_TESTED`) |

