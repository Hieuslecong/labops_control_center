# Giới hạn còn lại

- BuildKit và phiên SSH trong Docker staging cần một daemon riêng. Các kiểm tra runtime còn gồm TLS từ namespace của container, mount credential, quyền Agent trên filesystem, ownership của checkout, và scanner thật.
- Trivy là scanner tích hợp hiện tại. Chưa có adapter Gitleaks riêng. Các test tích hợp cục bộ dùng scanner fixture, không chứng minh chất lượng hoặc độ mới của database Trivy.
- Fetch toàn bộ lịch sử có thể ghi nhiều hơn giới hạn artifact 500 MB trước khi đóng gói. Guard cho phép tối đa hai pipeline fetch/scan đồng thời. Đặt quarantine trên filesystem riêng có dung lượng giới hạn và theo dõi cả yêu cầu chờ duyệt.
- Bundle bảo toàn lịch sử reachable của commit được fetch. Mỗi artifact giữ một tree đã xác minh, chưa cung cấp đầy đủ remote branches, tags, đối tượng LFS hoặc nội dung submodule. Với tag, worktree checkout commit ở detached HEAD.
- Credential hết hạn sau tám giờ. Cấp lại cần Admin. Không có self-service refresh.
- Request state và publish vào filesystem là hai thao tác riêng. Nếu Agent dừng đột ngột sau publish, hãy kiểm tra checkout và audit trước khi thử lại. Guard bảo toàn đích đã tồn tại.
- Git gốc qua đường dẫn tuyệt đối cùng các luồng fetch, pull, alias, SCP, SFTP và tool khác vẫn có thể đưa mã vào workspace. Scan bao phủ cây làm việc tại commit đã chốt.

- Bản sao runbook `run.sh` ở baseline kết thúc giữa hàm và không parse được. Dùng `labops_admin_runbook.sh` cho lệnh build tương ứng.
