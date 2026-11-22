# Mô hình bảo mật

Credential HMAC có audience `labops-repository`, subject, member ID, workspace ID, node ID, scopes `repository:request`, `repository:status`, `repository:deliver`, thời điểm cấp, hạn tám giờ và JTI. Backend kiểm tra quyền trước mỗi thao tác. Phiên tổng quát và credential repository dùng các quy tắc kiểm tra loại token riêng. Thu hồi theo JTI dùng kho revocation hiện có. Việc đình chỉ thành viên cũng chặn các thao tác tiếp theo.

Workspace dùng tệp `/run/labops/credentials/repository-token` mode `0600` trong bind mount riêng read-only. Người cùng UID có thể đọc token và dùng lại đến khi hết hạn hoặc bị thu hồi. Session secret, broker secret và machine credential giữ tại Backend hoặc Agent. Chỉ đường dẫn token và URL API xuất hiện trong môi trường Docker.

Broker tạo workspace identity từ claim đã ký và metadata phía server. Agent đối chiếu request owner, ID workspace, node, thành viên, Docker registry, container đang chạy và mount đã inspect. Đích tương đối được giới hạn dưới mount. Agent từ chối traversal, symlink, thư mục đích tồn tại và các thành phần nhạy cảm. Agent mở parent bằng `openat2` rồi publish bằng `renameat2(RENAME_NOREPLACE)` từ staging riêng trên cùng filesystem. Request chuyển trạng thái dưới mutex để ngăn hai lần bàn giao đồng thời cùng request.

Delivery đối chiếu SHA-256 artifact với bản đã duyệt, kiểm tra bundle và commit, áp giới hạn khi giải nén, rồi chuyển ownership sang UID/GID của workspace. Thay đổi policy version yêu cầu duyệt lại cache đời trước. Artifact đưa vào workspace sau khi kiểm tra hoàn tất.

Wrapper kiểm soát cú pháp clone được đi qua `/usr/local/bin/git`. Git gốc, công cụ import mã, Git alias và Git fetch/pull nằm ngoài phạm vi kiểm duyệt của wrapper. Trivy quét snapshot của commit yêu cầu. Người dùng có thể checkout thông tin lịch sử đến từ bundle và tiếp tục chỉnh sửa hoặc fetch sau bàn giao.
