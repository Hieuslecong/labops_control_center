# Checklist triển khai — 2026-11-10

Chưa triển khai production. Cần hoàn thành backend identity/revocation, PostgreSQL/migration, agent, workspace, scheduler, SSH revoke và security review trước khi triển khai. Trên host canary được cho phép, chạy migration/restore, API auth/IDOR, Docker resource isolation, GPU reservation và SSH/VPN theo preflight, backup và rollback đã duyệt. Không sửa SSH legacy, VPN hoặc workload nghiên cứu. Rollback code bằng bản phát hành trước đã được kiểm chứng; không tự xóa dữ liệu hoặc rollback schema khi chưa có backup và phương án tương thích.
