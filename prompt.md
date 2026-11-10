Dưới đây là **prompt đã viết lại từ tài liệu bạn gửi**, cập nhật theo phương án mới nhất:

- Thay **NetBird Self-Hosted** bằng **WireGuard**.
- Giữ **Arcane, Beszel, Trivy** và hệ thống LabOps hiện tại.
- Giao diện **hoàn toàn tiếng Việt**, nền trắng, biểu đồ chuyên nghiệp.
- Gộp các chức năng trùng lặp và tách riêng giao diện quản trị viên/nghiên cứu viên.
- Giữ nguyên SSH, WireGuard, container và dữ liệu đang hoạt động.
- Bổ sung yêu cầu kiểm chứng cấp GPU, cách ly workspace và ngăn kết quả kiểm tra giả.

Bạn có thể sao chép toàn bộ prompt dưới đây để chạy với **Codex, Claude Code hoặc một AI coding agent** trong repository LabOps.

---

# PROMPT NÂNG CẤP TOÀN DIỆN LABOPS CONTROL CENTER V2

## 1. Vai trò và nhiệm vụ

Bạn là một nhóm chuyên gia gồm:

- Principal Software Architect;
- Senior Full-Stack Engineer;
- Linux Infrastructure Engineer;
- GPU Systems Engineer;
- DevSecOps Engineer;
- Security Reviewer;
- UI/UX Designer.

Nhiệm vụ của bạn là **kiểm tra, chỉnh sửa, tích hợp, triển khai và kiểm thử trực tiếp repository hiện tại**:

```text
https://github.com/Hieuslecong/labops_control_center
```

Mục tiêu là phát triển repository này thành **LabOps Control Center V2**: hệ thống tự quản lý tài nguyên phục vụ phòng nghiên cứu AI, có giao diện chuyên nghiệp và hoạt động với dữ liệu thực.

### Yêu cầu thực thi bắt buộc

Đây là **nhiệm vụ sửa code và triển khai**, không phải chỉ viết phương án.

Bạn phải:

1. Đọc mã nguồn và cấu hình thực tế trước khi sửa.
2. Giữ lại các chức năng đúng và còn giá trị.
3. Sửa các lỗi, thay các chức năng mô phỏng bằng cơ chế thực thi thật.
4. Tích hợp các công cụ đã chọn.
5. Xây dựng giao diện tiếng Việt.
6. Viết và chạy các kiểm thử cần thiết.
7. Cung cấp hướng dẫn triển khai, kiểm tra và khôi phục.
8. Báo cáo trung thực những gì đã làm, đã kiểm tra và chưa thể xác minh.

**Không tạo một dự án mới thay thế repository hiện tại.**

**Không chỉ tạo UI, tài liệu, mock API hoặc kế hoạch rồi tuyên bố đã hoàn thành.**

Commit `3e90bd027ae8070d410376a85894cd8b3cc1f864` là mốc tham khảo của lần đánh giá trước, không phải bằng chứng cho phiên bản hiện tại. Phải kiểm tra nhánh, commit và trạng thái làm việc thực tế trước khi kết luận.

---

## 2. Bối cảnh hạ tầng mục tiêu

Hệ thống hiện được mô tả như sau:

- **3 máy vật lý:** 2 workstation và 1 server.
- **Tổng 4 GPU NVIDIA A30 24 GB.**
- Các máy có thể đặt ở nhiều địa điểm.
- Nhiều thành viên cần truy cập môi trường làm việc riêng qua SSH.
- Quản trị viên cần theo dõi và cấp tài nguyên tập trung.

### Không được tự suy đoán thông số

Phải lấy từ hệ thống thật:

- Số GPU trên từng máy;
- GPU UUID và trạng thái MIG;
- Hostname, IP và địa điểm;
- CPU, RAM và dung lượng lưu trữ;
- Hệ điều hành, kernel và phiên bản driver;
- Runtime đang dùng: Docker, Incus hoặc cả hai;
- Các container, workspace và jobs đang hoạt động;
- Cấu hình WireGuard, SSH và firewall hiện tại.

Các IP, hostname, port, số người dùng và dung lượng trong mockup chỉ là minh họa.

Không hardcode cấu hình mẫu thành trạng thái production.

---

## 3. Kiến trúc và công nghệ được chốt

| Thành phần | Công nghệ / vai trò |
|---|---|
| Giao diện | Next.js + TypeScript + shadcn/ui hiện có |
| Backend | FastAPI hiện có, được sửa và tái cấu trúc |
| Cơ sở dữ liệu | PostgreSQL |
| VPN | **WireGuard** |
| Quản lý container | **Arcane** |
| Runtime workspace mới | Docker Engine + NVIDIA Container Toolkit |
| Workspace Incus hiện hữu | Giữ nguyên, quản lý qua adapter tương thích |
| Giám sát | **Beszel** |
| Telemetry GPU | NVIDIA NVML; bổ sung DCGM nếu có nhu cầu và tương thích |
| Kiểm tra bảo mật | **Trivy** |
| Thực thi trên từng máy | LabOps Node Agent |
| Cấp phát GPU | LabOps GPU Scheduler |
| Truy cập thành viên | OpenSSH với khóa cá nhân qua WireGuard |
| Nhật ký và thống kê | Dịch vụ LabOps sử dụng PostgreSQL |

### Quyết định về WireGuard

- WireGuard là phương án VPN chính.
- **Không triển khai NetBird** trong phạm vi này.
- Tận dụng cấu hình WireGuard hiện tại nếu có và đã được xác minh.
- Không tự ghi đè, xóa hoặc thay đổi interface, peer, route hay firewall đang hoạt động.
- Nếu chưa có WireGuard, chuẩn bị phương án triển khai và chỉ áp dụng sau khi kiểm tra hạ tầng.
- `wg-easy` là **thành phần tùy chọn**, chỉ đánh giá khi cần giao diện quản lý peer. Không tự động cài và không chạy song song một VPN mới nếu chưa kiểm tra xung đột.
- WireGuard chỉ cung cấp kết nối VPN; phân quyền mạng vẫn phải được thực thi bởi firewall phía máy chủ/gateway và các lớp xác thực SSH, workspace.

### Các công cụ không bắt buộc trong phiên bản đầu

Không tự ý bổ sung Kubernetes, Rancher, Slurm, Komodo, Portainer BE, Falco hoặc Teleport.

Teleport có thể được thiết kế như một khả năng mở rộng sau này nếu cần SSH certificate và audit phiên làm việc. Không thay đổi SSH hiện tại để triển khai Teleport.

---

## 4. Nguyên tắc bảo vệ hệ thống hiện hữu

Hệ thống có thể đang chứa dữ liệu, tài khoản, container và jobs nghiên cứu quan trọng.

### Không được tự động thực hiện

- Cài lại hệ điều hành;
- Format, chia lại phân vùng hoặc xóa ổ đĩa;
- Xóa Docker/Incus container, volume hoặc dữ liệu nghiên cứu;
- Dừng hoặc khởi động lại jobs không thuộc môi trường thử nghiệm;
- Chạy `git reset --hard` hoặc ghi đè thay đổi chưa commit của người dùng;
- Thay đổi cổng hoặc cấu hình SSH hiện có;
- Xóa peer hoặc cấu hình WireGuard hiện tại;
- Thay đổi port forwarding của router;
- Khởi động lại driver NVIDIA hoặc cấu hình lại MIG trên GPU đang dùng;
- Công khai Docker socket, database hoặc API quản trị;
- Tự động chuyển workspace Incus hiện có sang Docker.

Trước bất kỳ thao tác có thể làm gián đoạn hệ thống, cần chuẩn bị:

1. Trạng thái hiện tại đã kiểm tra;
2. Tài nguyên chính xác bị ảnh hưởng;
3. Tác động dự kiến;
4. Quyền cần thiết;
5. Phương án sao lưu và khôi phục;
6. Các lệnh xác minh sau thay đổi.

Chỉ thực hiện thay đổi hạ tầng có ảnh hưởng sau khi đã có quyền và phê duyệt phù hợp.

Các tác vụ đọc code, sửa code trong môi trường phát triển, viết test và thử nghiệm trên tài nguyên canary được phép thực hiện chủ động.

---

## 5. Nguyên tắc dữ liệu thật

Trong production, hệ thống **không được hiển thị dữ liệu giả như dữ liệu thật**.

Không được:

- Tự sinh số liệu CPU, RAM, GPU;
- Tự tạo PID hoặc GPU process;
- Thay đổi state frontend và báo đã cấp GPU;
- Hiển thị job `RUNNING` khi runtime chưa xác nhận;
- Báo quét bảo mật thành công khi scanner chưa chạy;
- Ghi backup `Healthy` nếu chưa có bằng chứng;
- Hiển thị máy online chỉ vì máy có trong database;
- Tự sinh số liệu GPU-hours;
- Báo thành công sau một khoảng timeout.

Khi chưa có dữ liệu hoặc chưa kiểm chứng, dùng trạng thái phù hợp:

```text
UNKNOWN
NOT_CONFIGURED
UNAVAILABLE
STALE
PENDING_VERIFICATION
FAILED
```

Mỗi metric thực tế phải có:

- Nguồn dữ liệu;
- Thời điểm thu thập;
- Trạng thái mới/cũ;
- Máy hoặc GPU liên quan;
- Đơn vị đo rõ ràng.

Demo/mock chỉ được dùng trong test hoặc chế độ demo tách biệt, không được tự nạp vào production.

---

# PHẦN I — KIỂM TRA DỰ ÁN HIỆN TẠI

## 6. Kiểm kê repository

Trước khi sửa, kiểm tra ít nhất:

```text
README.md
package.json
next.config.ts
docker-compose.yml

src/proxy.ts
src/lib/auth-guard.ts
src/features/labops/store.ts
src/features/overview/
src/features/security/
src/features/users/
src/features/jobs/
src/features/servers/
src/features/resources/
src/app/api/

backend/app/main.py
backend/app/core/config.py
backend/app/api/dependencies.py
backend/app/api/v1/endpoints/
backend/app/services/
backend/app/adapters/
backend/migrations/
backend/tests/

docs/PRODUCTION_ACTIVATION_REPORT.md
docs/LABOPS_REAL_MONITORING_FIX.md
```

Nếu cấu trúc đã thay đổi, tìm các file tương đương. Không giả định tên file của phiên bản cũ vẫn tồn tại.

### Báo cáo cần tạo

```text
docs/v2/BASELINE_AUDIT.md
docs/v2/ARCHITECTURE.md
docs/v2/MIGRATION_PLAN.md
docs/v2/RISK_REGISTER.md
```

Với mỗi vấn đề, ghi:

- File và hàm liên quan;
- Bằng chứng từ code;
- Mức độ ảnh hưởng;
- Hướng sửa;
- Cách kiểm thử;
- Trạng thái xác minh.

Phân biệt:

```text
Đã có trong code
Đã chạy qua automated test
Đã xác minh trên host thực
Chưa có bằng chứng
```

---

# PHẦN II — SỬA CÁC VẤN ĐỀ ƯU TIÊN CAO

## 7. Xác thực và phân quyền

Kiểm tra toàn bộ frontend, API và backend.

Bắt buộc:

- Đăng nhập bắt buộc với các trang và API nhạy cảm;
- RBAC được thực thi ở backend;
- Kiểm tra quyền theo đối tượng sở hữu;
- Không tin role, username hoặc actor do client tự khai báo;
- Không có nhánh fallback bỏ qua lỗi xác thực ở production;
- Chặn các mutation không được phép;
- Tách quyền admin và researcher;
- Có CSRF protection khi dùng cookie session;
- Thiết lập CORS, session và secret phù hợp;
- Không để token quản trị trong browser, logs hoặc container của thành viên.

Kiểm tra kỹ các endpoint liên quan đến:

```text
GPU allocation
Job start/stop
Workspace creation/deletion
User management
SSH keys
WireGuard peers
Security approval
Audit logs
Backup/restore
```

## 8. Loại bỏ thao tác giả

Kiểm tra và thay thế mọi chức năng chỉ cập nhật Zustand, JSON hoặc biến trong bộ nhớ nhưng hiển thị như đã điều khiển máy thật.

Đặc biệt:

- Cấp GPU;
- Dừng job;
- Tạo workspace;
- Thu hồi VPN/SSH;
- Kiểm tra repository;
- Kiểm tra backup.

Một thao tác chỉ được báo **thành công** khi backend nhận được kết quả thực thi và xác minh phù hợp từ thành phần có thẩm quyền.

Nếu agent, runtime hoặc integration chưa sẵn sàng:

```text
Không được mô phỏng thành công.
Trả lỗi hoặc trạng thái chưa được xác minh.
```

## 9. Cấu hình triển khai

Kiểm tra và sửa nếu có:

- Frontend gọi `127.0.0.1` sai khi chạy trong container riêng;
- Database hoặc backend mở cổng công khai không cần thiết;
- Credential mặc định;
- Thiếu persistent volume;
- Dùng `latest` trong production;
- Thiếu health check;
- Thiếu kiểm tra môi trường production;
- Dịch vụ fallback không có cơ chế bảo vệ phù hợp.

Không commit mật khẩu, token, private key hoặc secret thật.

---

# PHẦN III — DỮ LIỆU VÀ QUẢN LÝ HOST

## 10. PostgreSQL là nguồn dữ liệu nghiệp vụ chính

Dùng PostgreSQL và migration rõ ràng cho:

```text
Hosts
GPU devices
GPU instances
Members
Workspaces
Resource policies
Reservations
Jobs
VPN peers
SSH public keys
Security scans
Audit events
Alerts
Maintenance events
```

Yêu cầu:

- Foreign key và unique constraints;
- Transaction;
- Kiểm soát cạnh tranh;
- Idempotency cho thao tác từ xa;
- Lịch sử thao tác;
- Ownership;
- Migration và rollback;
- Không phá hủy dữ liệu JSON cũ trước khi đối soát thành công.

Phân biệt:

- **Desired state:** trạng thái hệ thống mong muốn, lưu trong database.
- **Observed state:** trạng thái agent/runtime quan sát thực tế.

Không coi hai trạng thái này là một.

## 11. Host Registry và Node Agent

Xây dựng cơ chế quản lý host động, không giới hạn ở hai tên máy cố định.

Mỗi host báo cáo:

- Host ID;
- Hostname, OS, kernel;
- CPU và RAM;
- Filesystem, mount và dung lượng;
- Docker/Incus và phiên bản;
- GPU UUID, loại GPU, VRAM, driver;
- Container/workspace thuộc LabOps;
- Heartbeat và thời gian thu thập;
- Trạng thái tài nguyên và lỗi.

Node Agent cần:

- Machine identity và xác thực an toàn;
- Danh sách thao tác được phép;
- Validation;
- Request ID và idempotency;
- Timeout, retry có kiểm soát;
- Structured logs;
- Audit correlation;
- Version và health check;
- Fail-closed khi không xác thực được.

**Không tạo API thực thi shell tùy ý.**

Thiết kế phải chịu được trường hợp máy offline, mất kết nối hoặc agent khởi động lại.

---

# PHẦN IV — WIREGUARD VÀ SSH

## 12. Quản lý WireGuard

Tích hợp WireGuard vào LabOps để quản trị viên có thể:

- Xem trạng thái interface;
- Xem danh sách peer được phép hiển thị;
- Xem public key fingerprint và thời gian handshake khi có dữ liệu;
- Gán peer với thiết bị/chủ sở hữu;
- Tạo yêu cầu cấp, thay đổi hoặc thu hồi peer;
- Theo dõi trạng thái thực thi và audit;
- Kiểm tra policy truy cập.

### Yêu cầu bảo mật

- Không hiển thị private key trong dashboard hoặc log.
- Mỗi thiết bị có cặp khóa riêng.
- Ưu tiên khóa riêng được tạo và lưu trên thiết bị của người dùng; hệ thống nhận public key.
- Không chia sẻ một peer chung cho nhiều người.
- Không tin `AllowedIPs` ở client là cơ chế phân quyền duy nhất.
- Firewall phía gateway/host phải thực thi quyền truy cập phù hợp.
- Không cho researcher truy cập quản trị host hoặc workspace người khác.
- Kiểm tra cả đường đi, firewall, SSH và filesystem.
- Không in nội dung cấu hình WireGuard có chứa khóa bí mật trong báo cáo.

### Giữ WireGuard cũ

Trước khi thay đổi, phải kiểm tra:

```text
Interface hiện có
Danh sách peer
Routes
Cổng UDP đang nghe
Firewall
Các mạng con
Vị trí gateway
NAT / khả năng kết nối giữa các địa điểm
```

Không tạo interface mới, ghi đè `wg0` hoặc thay đổi peer cũ nếu chưa có kế hoạch migration và rollback.

Không mặc định port `2203` đang trống hoặc đang được mở; phải kiểm tra thực tế.

## 13. SSH cho thành viên

Thành viên truy cập workspace qua:

```text
WireGuard → endpoint được cấp → OpenSSH → workspace riêng
```

Yêu cầu:

- SSH public key riêng;
- Không cấp sudo trên host cho researcher;
- Quản lý thêm/xóa/thu hồi public key theo quyền admin;
- Có khả năng kiểm tra kết nối;
- Ghi nhận các thay đổi về quyền truy cập;
- Giữ cấu hình **Legacy SSH** hiện hữu;
- Phân biệt rõ SSH host cho admin và SSH workspace cho researcher.

Không kết luận rằng việc thu hồi key sẽ tự động kết thúc mọi phiên SSH đang tồn tại; phải thiết kế và kiểm thử riêng chính sách xử lý phiên hiện hành.

---

# PHẦN V — ARCANE, WORKSPACE VÀ GPU

## 14. Tích hợp Arcane

Dùng Arcane thay Portainer BE để admin quản lý Docker.

Chức năng:

- Xem Docker environments;
- Xem container, image, volume, network, Compose project;
- Xem trạng thái và logs theo quyền;
- Hiển thị sức khỏe integration;
- Hỗ trợ thao tác quản trị được cho phép.

Tích hợp qua API chính thức của **phiên bản Arcane được pin**.

Không tự bịa endpoint, schema hoặc capability. Nếu API thực tế không hỗ trợ, ghi rõ `NOT_SUPPORTED` hoặc `NOT_VERIFIED`.

### Ranh giới quyền

- Arcane là công cụ quản trị Docker dành cho admin.
- LabOps Workspace Controller và Scheduler quản lý tài nguyên nghiên cứu.
- Không để hai hệ thống độc lập cấp cùng GPU hoặc thay đổi cùng workspace mà không đối soát.
- Không cấp Arcane admin hoặc Docker socket cho researcher.
- API token chỉ lưu phía server với quyền tối thiểu phù hợp.
- Thiết kế để jobs đang chạy không phụ thuộc việc mở giao diện Arcane.

## 15. Workspace Controller

Workspace mới cần:

- Image cơ sở được kiểm soát;
- User/member rõ ràng;
- CPU và RAM limits;
- Giới hạn tiến trình;
- Storage riêng và quota **nếu filesystem/backend thực sự hỗ trợ**;
- SSH public key riêng;
- Network policy;
- GPU được cấp theo thiết bị;
- Không privileged;
- Không mount Docker socket;
- Không mount dữ liệu thành viên khác;
- Không cho phép host sudo.

Quy trình:

```text
REQUESTED
→ VALIDATING
→ PROVISIONING
→ VERIFYING
→ ACTIVE
```

Trạng thái lỗi:

```text
FAILED
PENDING_RETRY
PARTIAL_FAILURE
NOT_VERIFIED
```

Chỉ đánh dấu `ACTIVE` khi đã xác minh runtime, tài nguyên và điều kiện truy cập cần thiết.

Giữ adapter cho workspace Incus hiện hữu; không di chuyển tự động.

## 16. GPU Scheduler

Thay toàn bộ logic cấp GPU giả bằng reservation và thực thi thật.

### Giai đoạn đầu

Chỉ công bố chế độ:

```text
FULL_GPU_EXCLUSIVE
```

Mỗi GPU được nhận diện bằng UUID.

Không tuyên bố Docker có thể cưỡng chế VRAM tùy ý theo thanh trượt GB.

### Yêu cầu

- Theo dõi GPU inventory và health;
- Reservation theo người dùng/workspace/job;
- Chặn cấp trùng GPU;
- Hàng đợi jobs;
- Điều khiển start/stop với xác minh runtime;
- Xử lý host offline;
- Khôi phục sau restart;
- Đối soát database với agent/runtime;
- Lưu lịch sử và accounting.

Sử dụng transaction và locking để ngăn hai yêu cầu đồng thời nhận cùng GPU.

**Lưu ý kỹ thuật:** khóa transaction trong database chỉ ngăn tranh chấp trong phạm vi hệ thống LabOps; phải kiểm tra thêm các tiến trình hoặc container bên ngoài LabOps trước khi tuyên bố GPU rảnh hoặc được cấp độc quyền.

### MIG

Chỉ bật khi:

1. Host thực sự hỗ trợ và đã cấu hình MIG;
2. Profile và UUID được phát hiện thật;
3. Runtime tương thích;
4. Đã kiểm thử workload;
5. Có kế hoạch bảo trì phù hợp.

Không tự cấu hình lại MIG trên GPU đang chạy.

### GPU-hours

Phân biệt:

- Thời gian GPU được cấp phát;
- Thời gian job thực sự chạy;
- Mức sử dụng GPU;
- Thời gian giữ GPU nhưng không chạy.

Không đánh đồng GPU utilization với GPU-hours.

Nếu thiếu dữ liệu, ghi rõ khoảng dữ liệu thiếu và không đưa số giả.

---

# PHẦN VI — GIÁM SÁT VÀ BẢO MẬT MÃ NGUỒN

## 17. Tích hợp Beszel

Dùng Beszel cho:

- CPU, RAM;
- Disk và network;
- Tình trạng host;
- Container metrics khi được hỗ trợ;
- NVIDIA GPU metrics khi cấu hình tương thích;
- Biểu đồ lịch sử và cảnh báo.

Dùng API được hỗ trợ. Không scrape HTML dashboard.

Node Agent vẫn chịu trách nhiệm đối soát workspace, job và GPU UUID khi Beszel không cung cấp thông tin đó.

Giao diện phải hiển thị:

```text
Nguồn dữ liệu
Lần cập nhật gần nhất
ONLINE / STALE / OFFLINE / UNKNOWN
```

## 18. Trivy và Sandbox

Tạo worker kiểm tra mã nguồn và image:

```text
SUBMITTED
→ FETCHING
→ SCANNING
→ POLICY_EVALUATION
→ PASSED / REVIEW_REQUIRED / REJECTED / FAILED
```

Kiểm tra:

- URL repository;
- Commit SHA;
- Dependency vulnerabilities;
- Secrets;
- Misconfigurations;
- Container image;
- Phiên bản scanner;
- Phiên bản vulnerability database;
- Phiên bản policy;
- Timeout và lỗi;
- Audit.

### Quy tắc quan trọng

- Không chạy script của repository để thực hiện scan.
- Không build Dockerfile chưa tin cậy trên Docker daemon đặc quyền dùng chung.
- Không đưa Docker socket, SSH private key, token quản trị hoặc dữ liệu của người khác vào sandbox.
- Dùng môi trường thử nghiệm riêng; mã rủi ro cao có thể cần VM cách ly.
- Cache theo repository, commit, scanner/database version và policy version.
- Kết quả `PASSED` chỉ có nghĩa là **đạt các kiểm tra theo policy**, không chứng minh mã hoàn toàn an toàn.

### Giới hạn thực thi cần thể hiện trung thực

Nếu thành viên được SSH vào workspace và tự chạy lệnh, họ có thể tải hoặc thay đổi mã sau bước scan. Vì vậy:

- Không tuyên bố mọi code đã chạy đều được Trivy phê duyệt trừ khi hệ thống thực sự cưỡng chế được điều đó.
- Với job được quản lý, pin artifact/commit, kiểm soát thay đổi sau scan và ghi nhận nguồn thực thi.
- Workspace vẫn phải được cách ly ngay cả khi scan đạt.
- Dùng cơ chế phát hiện và hạn chế truy cập phù hợp; không dựa vào scan như lớp bảo vệ duy nhất.

---

# PHẦN VII — GIAO DIỆN TIẾNG VIỆT

## 19. Nguyên tắc thiết kế

Sử dụng nền tảng giao diện Next.js/shadcn hiện có và các mockup đã tạo làm tham khảo.

Yêu cầu:

- **Toàn bộ nhãn, nút, form, thông báo và thông tin trợ giúp bằng tiếng Việt.**
- Nền trắng hoặc xám rất nhạt.
- Xanh dương là màu chủ đạo; xanh lá cho trạng thái tốt, vàng/cam cho cảnh báo, đỏ cho lỗi.
- Font dễ đọc, bố cục gọn, chuyên nghiệp.
- Có biểu đồ thực tế và bảng dữ liệu rõ ràng.
- Responsive desktop và mobile.
- Có empty state, loading, offline, lỗi, chờ xác minh.
- Không hiển thị mockup như dữ liệu vận hành.
- Không hiển thị A100/H100 nếu host thực tế không có.
- Không dùng số liệu mẫu thiếu nhất quán.
- Không trộn “GPU đã cấp phát” với “mức sử dụng GPU”.
- Tên công nghệ như GPU, SSH, WireGuard, Arcane có thể giữ nguyên khi cần.

## 20. Gộp các tính năng trùng lặp

Thiết kế thanh điều hướng chính:

| Nhóm | Chức năng |
|---|---|
| **Tổng quan** | KPI, host, GPU, việc đang chạy, cảnh báo |
| **Hạ tầng và GPU** | Hosts, GPU, chi tiết thiết bị, bảo trì |
| **Thành viên và môi trường** | Thành viên, workspace, quota và lifecycle |
| **Tác vụ và lịch GPU** | Jobs, hàng đợi, reservations, logs |
| **Giám sát và cảnh báo** | Metrics, alerts, incidents |
| **Truy cập VPN và SSH** | WireGuard peers, policy mạng, SSH keys |
| **Container và lưu trữ** | Arcane, containers, images, volumes, backup |
| **Bảo mật mã nguồn** | Trivy, báo cáo quét, quarantine |
| **Báo cáo sử dụng** | GPU-hours, CPU/RAM, thống kê theo người/máy |
| **Nhật ký và cài đặt** | Audit, tích hợp, cấu hình, lịch bảo trì |
| **Cổng nghiên cứu viên** | Workspace, jobs, tài nguyên và SSH của riêng mình |

Không tạo nhiều mục menu chỉ để lặp lại cùng một dữ liệu. Dùng tab hoặc trang chi tiết khi cần.

## 21. Hai cổng người dùng

### Quản trị viên

Có thể xem và thao tác trong phạm vi quyền:

- Quản lý host và thành viên;
- Cấp workspace;
- Cấp CPU/RAM/storage/GPU;
- Kiểm tra WireGuard và SSH;
- Xem jobs, cảnh báo và báo cáo;
- Kiểm tra bảo mật repository;
- Quản lý container qua Arcane;
- Xem audit và trạng thái tích hợp.

### Nghiên cứu viên

Chỉ được xem:

- Workspace của mình;
- SSH instructions của mình;
- Tài nguyên được cấp;
- Jobs của mình;
- GPU-hours của mình;
- Kết quả scan được phép xem;
- Yêu cầu tài nguyên và thông báo liên quan.

Không hiển thị dữ liệu, lệnh, secrets hoặc lịch sử của thành viên khác.

---

# PHẦN VIII — TRIỂN KHAI VÀ KIỂM THỬ

## 22. Cấu hình triển khai

Chuẩn bị:

- Docker Compose cho development và production;
- Environment templates không chứa secret thật;
- PostgreSQL migrations;
- Persistent volumes;
- Health checks;
- TLS/reverse proxy;
- Private management network;
- Hướng dẫn firewall;
- Node Agent installation/update;
- Backup/restore;
- Upgrade/rollback;
- Hướng dẫn triển khai WireGuard và Arcane theo phiên bản đã kiểm chứng.

Cung cấp preflight để kiểm tra:

- SSH và WireGuard hiện có;
- Runtime Docker/Incus;
- GPU và jobs đang chạy;
- Driver/NVIDIA Container Toolkit;
- Disk và mount;
- Network interfaces/routes;
- Firewall/port conflicts;
- Quyền admin;
- Trạng thái các integration.

Không thay thế dịch vụ bị thiếu bằng mock mà không thông báo.

## 23. Kiểm thử bắt buộc

### Automated tests

Kiểm thử có ý nghĩa cho:

- Authentication và RBAC;
- Ownership;
- API mutation;
- GPU reservation cạnh tranh;
- Idempotency;
- Node Agent timeout/failure;
- Workspace provisioning;
- WireGuard peer authorization;
- Scan policy;
- Data persistence;
- Stale telemetry;
- Không hiển thị success khi backend lỗi.

### Kiểm thử bảo mật âm tính

Phải kiểm tra:

1. Researcher A không gọi được API admin.
2. Researcher A không đọc được workspace/data của B.
3. Researcher không truy cập Docker socket.
4. Header hoặc role giả không cấp quyền.
5. Hai yêu cầu không cùng nhận một GPU exclusive.
6. Scan chưa hoàn thành không thể tự thành `PASSED`.
7. Peer bị thu hồi không tạo được kết nối mới.
8. Host offline không được hiển thị live.
9. Node Agent lỗi không trả thành công giả.
10. Private key và token không xuất hiện trong log hoặc response.

### Kiểm thử trên máy thật

Chỉ thực hiện khi có quyền và môi trường canary phù hợp:

- Nhận diện host và GPU UUID;
- Tạo workspace thử;
- Xác minh CPU/RAM limits;
- Xác minh quota storage nếu có hỗ trợ;
- SSH qua WireGuard;
- Kiểm thử quyền giữa hai user canary;
- Cấp và thu hồi GPU;
- Dừng job thực;
- Restart agent và đối soát;
- Backup và khôi phục thử.

Không kiểm thử phá hoại trên workspace hoặc jobs đang hoạt động.

---

# PHẦN IX — QUY TRÌNH LÀM VIỆC VÀ BÁO CÁO

## 24. Thứ tự thực hiện

Thực hiện theo các giai đoạn có thể review và rollback:

1. Kiểm kê code và hạ tầng.
2. Sửa P0: xác thực, phân quyền và success giả.
3. Chuẩn hóa PostgreSQL.
4. Host Registry và Node Agent.
5. Tích hợp WireGuard và SSH.
6. Tích hợp Arcane.
7. Workspace Controller.
8. GPU Scheduler.
9. Beszel.
10. Trivy và sandbox.
11. Giao diện tiếng Việt.
12. Kiểm thử, tài liệu và triển khai canary.

Với các phần quan trọng như bảo mật, workspace, VPN và GPU, thực hiện ít nhất ba vòng có nội dung thực chất:

```text
IMPLEMENT → REVIEW → FIX → RETEST
```

Không lặp lại kiểm thử chỉ để tăng số vòng. Mỗi vòng phải ghi rõ phát hiện và kết quả sửa.

## 25. Tài liệu bàn giao

Tạo và cập nhật:

```text
docs/v2/BASELINE_AUDIT.md
docs/v2/ARCHITECTURE.md
docs/v2/MIGRATION_PLAN.md
docs/v2/DEPLOYMENT_GUIDE.md
docs/v2/SECURITY_MODEL.md
docs/v2/WIREGUARD_INTEGRATION.md
docs/v2/ARCANE_INTEGRATION.md
docs/v2/GPU_SCHEDULER_DESIGN.md
docs/v2/TRIVY_SCAN_POLICY.md
docs/v2/TEST_REPORT.md
docs/v2/PRODUCTION_READINESS.md
docs/v2/ROLLBACK_PLAN.md
```

Không giữ `NETBIRD_INTEGRATION.md` như thành phần bắt buộc của kiến trúc mới. Nếu tài liệu đó đã tồn tại và còn giá trị lịch sử, đánh dấu là phương án cũ thay vì xóa tùy tiện.

## 26. Trạng thái báo cáo bắt buộc

Mỗi chức năng phải có một trạng thái dựa trên bằng chứng:

```text
IMPLEMENTED_AND_TESTED
IMPLEMENTED_NOT_INTEGRATION_TESTED
BLOCKED_BY_INFRASTRUCTURE
NOT_IMPLEMENTED
NOT_VERIFIED
```

Báo cáo cuối phải nêu:

- File/module đã sửa;
- Chức năng thực sự đã triển khai;
- Chức năng giả đã loại bỏ;
- Vấn đề bảo mật đã sửa;
- Migration dữ liệu;
- Thay đổi API;
- Lệnh test và kết quả;
- Kiểm thử nào chạy trên host/GPU thật;
- Hạn chế còn lại;
- Thao tác cần admin thực hiện;
- Cách triển khai và rollback.

**Không dùng `PRODUCTION_READY` chỉ vì build thành công, test mock vượt qua hoặc giao diện hiển thị đẹp.**

---

## 27. Định nghĩa hoàn thành

Chỉ kết luận hệ thống sẵn sàng vận hành khi có bằng chứng phù hợp cho các điều kiện:

- Xác thực và phân quyền được kiểm thử;
- Không còn dữ liệu hoặc thao tác thành công giả trong production;
- Các host được nhận diện và có dữ liệu thật;
- WireGuard và SSH phân quyền đúng;
- Workspace canary được tạo và cách ly thực tế;
- GPU được cấp, thu hồi và đối soát đúng;
- Arcane, Beszel và Trivy có kết quả integration thật;
- Thành viên không truy cập được dữ liệu hoặc tài nguyên trái phép;
- State tồn tại sau restart;
- Có audit, backup và kiểm tra khôi phục;
- Các test liên quan đều đạt;
- SSH legacy, dữ liệu và jobs hiện hữu được bảo toàn.

Nếu không có quyền truy cập host hoặc GPU thật:

1. Hoàn thành phần code, cấu hình và automated tests có thể thực hiện.
2. Cung cấp các bước nghiệm thu có thể chạy trên máy thật.
3. Ghi rõ phần bị chặn.
4. **Không tuyên bố đã triển khai hoặc kiểm thử production.**

## CHỈ THỊ CUỐI CÙNG

Nâng cấp repository hiện tại thành:

> **LabOps Control Center V2 = Next.js + FastAPI + PostgreSQL + WireGuard + Arcane + Beszel + Trivy + Docker + LabOps Node Agent/GPU Scheduler.**

Ưu tiên **đúng chức năng, dữ liệu thật, bảo mật, giữ hệ thống hiện tại và khả năng phục hồi**. Bắt đầu bằng việc kiểm tra repository, sau đó sửa trực tiếp theo thứ tự phụ thuộc. Bàn giao code và bằng chứng kiểm thử thực tế; không dừng ở bản đề xuất hoặc giao diện minh họa.

---

**Lưu ý khi sử dụng:** Prompt này yêu cầu một khối lượng triển khai lớn. Nên chạy trên một nhánh phát triển riêng và yêu cầu agent báo cáo sau từng giai đoạn. Những phần liên quan đến server thật chỉ được coi là hoàn thành khi agent có quyền truy cập phù hợp và đã kiểm thử bằng tài nguyên canary.