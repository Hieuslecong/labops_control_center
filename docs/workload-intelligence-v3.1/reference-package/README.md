# LabOps Workload Intelligence V3.1 — Implementation Hardened

Bản thay thế V3 sau review. Chỉ nâng cấp `/dashboard/jobs`, **không bao gồm Zalo AI**. Baseline tham chiếu `c1e3af18c31e6c8fa982d1958a1c83863585bcb9`; HEAD thực tế phải được audit khi triển khai. Không reset về baseline, không chồng lên công việc Zalo đang làm. Không triển khai production, thay đổi mạng, SSH/VPN, tài khoản, workspace hoặc thực thi tín hiệu tiến trình thật.

## Cách sử dụng
1. Giải nén package vào `docs/workload-intelligence-v3.1/` hoặc thư mục riêng phù hợp repo, không ghi đè tài liệu/source hiện có. Giữ cả cây thư mục, không chỉ copy master prompt.
2. Mở `04_Interactive_Prototype.html` offline và xem `visuals/`. Snapshot tham chiếu không phải dữ liệu hiện tại. Nút “Lịch sử minh họa” mặc định tắt; dữ liệu demo tuyệt đối không đưa vào production. Mọi hành động dừng tiến trình đều vô hiệu hóa trong prototype.
3. Giao `03_Master_Implementation_Prompt.md` cho coding agent. Yêu cầu đọc toàn bộ package, audit actual HEAD rồi thực hiện G0–G7 theo `prompts/`, dùng `08_Test_Acceptance_Matrix.md` và `07_Compatibility_Matrix.md` làm acceptance gates.
4. Chạy các gate theo thứ tự, không bật thao tác mới trước G5/G6 PASS; giữ feature off production. Nếu thiếu dữ liệu lịch sử, triển khai trạng thái không khả dụng; không sửa Zalo để bổ sung history. G7 chỉ chuẩn bị release/rollback, không phải quyền triển khai.

## Danh mục
| File | Vai trò |
|---|---|
| 01_Specification.md | Đặc tả tổng thể, evidence ledger, UI/UX, chart/table inventory, kiến trúc và an toàn |
| 02_Implementation_Playbook.md | Hướng dẫn thực hiện theo gate, quy trình audit/test và handoff |
| 03_Master_Implementation_Prompt.md | Master prompt hoàn chỉnh, tiếng Anh kỹ thuật |
| 04_Interactive_Prototype.html | Giao diện tham chiếu tương tác, độc lập, offline |
| 05_Data_Contract.md | Normative DTO, scope, null semantics, API, pagination, action result |
| 05_Contract_Examples.json | Index đến fixture đầy đủ, không phải payload legacy |
| 06_Metric_Dictionary.md | Định nghĩa KPI, đơn vị, coverage, attribution, giờ GPU và identity |
| 07_Compatibility_Matrix.md | Ma trận phiên bản và kiểm tra consumer chung (bao gồm Zalo read-only) |
| 08_Test_Acceptance_Matrix.md | Danh mục test và quality gate |
| 09_Protected_Scope.md | Ranh giới bắt buộc, bảo toàn công việc Zalo và hạ tầng |
| 10_Rollback_Plan.md | Release staging, rollback, điều kiện dừng |
| 11_Prototype_QA.md | Kết quả kiểm thử standalone |
| contracts/ | 5 JSON Schema target (Draft 2020-12) |
| fixtures/ | 10 complete synthetic fixtures |
| prompts/ | 8 phase prompts G0–G7 |
| tests/ | 2 script kiểm thử offline và log thực tế |
| visuals/ | 8 PNG chụp từ prototype đã sửa |

## Kiểm thử package (tách biệt khỏi code LabOps)
- `python tests/validate_contracts.py`: Python 3 + jsonschema; kiểm tra schema và invariant của fixture.
- `python tests/test_prototype.py`: Python 3 + Playwright + Chromium (mặc định `/usr/bin/chromium`, override `CHROMIUM_PATH` nếu cần); kiểm tra tương tác, tạo lại ảnh và QA. Chạy trên môi trường development; không cần API, không cần quyền quản trị server.
- Kết quả đã thực thi: 10 fixture hợp lệ + 1 negative KPI invariant; 16 kiểm tra prototype, không có pageerror trong luồng kiểm tra. Không coi là test application hoặc production.

## Giới hạn
Source review ở commit tham chiếu từ lượt trước, chưa checkout/build/test branch hiện tại, chưa truy vấn database production. JSON Schema là contract đề xuất được kiểm tra độc lập, không phải OpenAPI đã sinh từ code thực tế. Prototype chưa mô phỏng toàn bộ pagination/nhóm/process tree/real history/auth/actions; production phải hoàn thiện theo đặc tả và gate. Cần xác minh source units, real telemetry, stable identity, agent capabilities, và dữ liệu lịch sử. Không được nhập fixture vào production. Mục tiêu hiệu năng, retention mới và feature flag cần kiểm chứng; không phải thông số đã deploy. Zalo AI không bị sửa trong package này.
