# LabOps hardening: inventory và baseline

Ngày: 2026-11-12. HEAD: `dda7eac5663459fc09152baf348605db535bc943`, nhánh `feature/labops-control-center`. Workspace có thay đổi chưa commit trước đợt kiểm tra; kết quả sau phản ánh cả các thay đổi đó, không chỉ commit HEAD. Đây là kiểm kê ban đầu, chưa phải kiểm toán toàn bộ hay xác nhận production.

## Luồng đã nhận diện

| Chức năng | Frontend → API → xác thực → thực thi → lưu trữ → xác minh/audit |
| --- | --- |
| Dashboard/API | `src/app/dashboard` → `src/app/api/labops/_lib.ts` → Clerk hoặc cookie nội bộ → FastAPI `backend/app/api/v1` → `LabOpsDataService`/adapter → nhiều JSON và runtime; xác minh, audit và ownership cần kiểm tra theo từng endpoint. |
| Gateway | Backend adapter → HTTP Gateway `go/internal/gateway/http_api.go` → shared control secret → `SessionManager`/gRPC → Agent; chưa chứng minh actor/permission riêng theo thao tác. |
| VPN | Member API → `LabOpsDataService` → `VPNManager` → `WgEasyAdapter` → API wg-easy; metadata hiện còn JSON, chưa chứng minh atomic persistence/ownership. |
| Workspace/SSH | V2 API → `LabOpsDataService`/`SSHAccessManager` và các controller khác → host/Docker; nhiều đường thực thi, cần kiểm tra từng bước trước khi can thiệp. |
| GPU | GPU API → `gpu_scheduler.py` + host/agent adapters → runtime GPU; chưa chứng minh khóa transaction PostgreSQL và dữ liệu cả hai node. |
| Repository | `repositories.py` → Repository Guard → artifact/workspace; kiểm soát egress và containment cần kiểm tra độc lập. |

## Bảng đối chiếu ban đầu

| Ưu tiên | Bằng chứng | Nguyên nhân / tác động | Hướng sửa và kiểm thử | Trạng thái |
| --- | --- | --- | --- | --- |
| P0 | `backend/app/adapters/wg_easy_adapter.py:107` và `:157` | Tạo client gọi kiểm tra subnet có thể POST đổi CIDR; có thể làm gián đoạn peer hiện hữu. | Đường thường chỉ đọc/cảnh báo; test mock xác nhận không có POST CIDR. | Chưa sửa |
| P0 | `backend/app/adapters/wg_easy_adapter.py:200`, `backend/app/core/config.py` | Ghi đè Endpoint trong profile bằng cấu hình mặc định; chưa xác minh cổng thực tế. | Giữ endpoint từ wg-easy trừ khi migration được phê duyệt; test profile. | Chưa sửa |
| P0 | `backend/app/core/config.py`, `infrastructure/wg-easy/compose.yaml` | Có mật khẩu VPN fallback trong code/cấu hình. | Bỏ fallback, fail closed; đánh giá credential thực tế và lên kế hoạch rotation riêng. | Chưa sửa; chưa xác định credential có đang dùng |
| P0 | `go/internal/pki/pki.go:462` | Enrollment manager có fallback secret cố định khi không cấu hình. | Fail closed và test thiếu secret; rà soát caller, provisioning, rotation. | Chưa sửa |
| P0 | `src/proxy.ts` | Cookie bất kỳ mang tên `labops_session` khiến proxy bỏ qua Clerk middleware; cần xác minh các route phụ thuộc Clerk và mọi guard độc lập. | Bỏ bypass không kiểm chữ ký, làm rõ tương thích Clerk và session nội bộ; test cookie giả. | Chưa sửa |
| P0 | `backend/app/core/security.py` | Token HMAC có payload hoặc expiry sai kiểu gây exception thay vì 401. | Validate payload trước khi dùng; test payload sai kiểu. | Đã sửa trong workspace; test độc lập PASS |
| P0 | `backend/app/core/security.py`, `backend/app/api/dependencies.py` | Static service token cũ được coi là ADMIN user ở một luồng; header actor từng được chấp nhận theo test cũ. | Tách service identity và user permission, test truy cập trực tiếp/giả actor; cần kiểm tra tương thích. | Chưa sửa |
| P1 | `backend/tests/test_v2_vpn_wgeasy.py` | Setup/teardown có `shutil.rmtree` dưới `/media/LabOps-Data/users` và `docker rm -f`. | Cô lập test bằng filesystem/runtime giả trước khi chạy. | NOT_EXECUTED để bảo toàn dữ liệu |
| P1 | `infrastructure/wg-easy/compose.yaml` | `INIT_PORT=2205`, mapping UDP vào 2204; comment và mapping không thống nhất. | Kiểm tra topology thực tế và canary; không tự đổi port. | Chưa xác minh production |
| P1 | `backend/app/services/labops_service.py:1578` | Permanent delete mặc định `remove_files=True` và có `rmtree`. | Phân tích toàn bộ đường gọi, đổi lifecycle và test path/symlink/backup. | Chưa sửa |
| P1 | `backend/data/` | Nhiều JSON runtime được Git theo dõi, gồm file tên `wireguard_server_keys.json`. | Kiểm kê an toàn và migration có backup; không in nội dung key. | Chưa xác minh nội dung hay rotate |
| P1 | `go/internal/docker/controller.go:160` | Phát hiện managed có fallback theo tên `ws-`; cần kiểm tra đường stop và inspect thực tế. | Kiểm tra metadata/ownership fail closed, test container lạ. | Cần kiểm chứng sâu |

## Baseline không phá hủy

- `backend/.venv/bin/python -m pytest -q backend/tests/test_v2_security.py`: PASS, 8 test; 1 cảnh báo Pydantic deprecated.
- `go test ./internal/pki -run '^TestEnrollmentTokenManager$' -count=1`: PASS.
- `node node_modules/typescript/bin/tsc --noEmit --pretty false`: PASS với Node cục bộ.
- Bộ test VPN nêu trên: NOT_EXECUTED vì chứa thao tác xóa thật. Test hạ tầng, hai host, PostgreSQL restore, wg-easy và GPU: NOT_EXECUTED; chưa có môi trường cách ly được xác minh.
- Chưa chạy toàn bộ test/build; chưa xác nhận baseline đầy đủ.

## Phạm vi và rollback

Không khởi động lại, thay cấu hình, ngắt kết nối hoặc xóa tài nguyên production. Rollback thay đổi mã nguồn phải áp dụng theo diff từng file, giữ nguyên các thay đổi có sẵn của người dùng; không dùng `git reset --hard`. Chưa có backup/restore production được thực hiện hay nghiệm thu. Không sử dụng các tài liệu hiện có làm bằng chứng môi trường đang chạy.

Tài liệu chính thức wg-easy v15.4 cho biết API Basic Auth không hoạt động khi 2FA bật, API chưa ổn định; `INIT_*` chỉ áp dụng lần khởi tạo đầu. Không tắt 2FA tự động. Nguồn: https://wg-easy.github.io/wg-easy/v15.4/advanced/api/ và https://wg-easy.github.io/wg-easy/v15.4/advanced/config/unattended-setup/.
