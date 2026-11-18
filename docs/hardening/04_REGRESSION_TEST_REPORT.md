# 04 — BÁO CÁO KIỂM THỬ HỒI QUY (REGRESSION TEST REPORT)

- **Branch**: `hardening/labops-v2`
- **Timestamp thực thi gần nhất**: `2026-11-18T15:16:29+07:00` (trạng thái mã nguồn sau commit `c8b360d` + bản vá Milestone 4)
- **Môi trường**: `aiot-ws1` (Linux, Python 3.13.5 venv, Go test toolchain với `-race`, Bun v1.4.4, Next.js 16.4.6 Turbopack, isolated `tmp_path` SQLite DBs)

---

## 1. Bảng Tổng hợp Kết quả Thực thi Toàn Stack

| Tầng kiểm thử | Lệnh thực thi (Command) | Kỳ vọng (Expected) | Kết quả thực tế (Actual) | Trạng thái |
|---|---|---|---|---|
| **Go Toolchain (`go vet` & Race Tests)** | `cd go && go vet ./... && go test -race -count=1 ./...` | 0 lỗi vet, 11/11 internal packages pass với `-race` | **11/11 internal packages PASS** (`agent` 1.367s, `config` 1.028s, `docker` 1.072s, `gateway` 1.109s, `gitaiotcli` 1.055s, `gpu` 1.532s, `host` 1.140s, `pki` 1.100s, `process` 1.647s, `repositoryguard` 1.903s, `transport` 1.286s); `cmd/*` & `proto/*` `[no test files]` | **PASS** |
| **Python Full Regression Suite** | `backend/.venv/bin/pytest backend/tests -v` | 0 failed; toàn bộ integration, security, migration, backup, lifecycle, Zalo & AI tests pass | **151 passed, 3 skipped, 0 failed** trong **48.79s** (có 1 warning Starlette/httpx2 deprecation không ảnh hưởng kết quả) | **PASS** |
| **Milestone 4 Integration & Benchmark** | `backend/.venv/bin/pytest backend/tests/test_milestone4_integration_and_ops.py -v` | 5/5 integration/migration/backup/SSRF/benchmark pass với p95 < 200ms | **5 passed, 0 failed** trong **11.09s** (summary p95 cải thiện từ `4,117.05 ms` xuống `12.219 ms`) | **PASS** |
| **Frontend Unit & Hygiene Tests** | `bun test src/__tests__/milestone3-frontend-hardening.test.ts` | 9/9 test pass (HMAC TTL 8h, CSRF, RBAC matrix, 0 `setInterval(`, 5 nav groups, `text-xs`) | **9 pass, 0 fail** (345.00 ms) | **PASS** |
| **TypeScript Typecheck** | `bun run typecheck` | Exit 0, 0 lỗi type trên toàn repo | `tsc --noEmit` exit **0** | **PASS** |
| **ESLint & Deprecated API Gate** | `bun run lint` | `oxlint` 0 error; `lint:deprecated` 0 error & 0 warning | `oxlint`: 0 errors (30 pre-existing warnings); `oxlint --type-aware -D typescript/no-deprecated`: **0 warnings, 0 errors** | **PASS** |
| **Isolated Production Build** | `NEXT_DIST_DIR=.next-verify bun run build` (tại `c8b360d`, BypassSandbox cho Google Fonts) | Build độc lập vào `.next-verify`, không chạm `.next/`, 29/29 route sạch | Compile **943ms**, TypeScript **4.1s**, Static pages **29/29** trong **1498ms**, Exit **0** (sau đó `rm -rf .next-verify`) | **PASS** *(chạy ở `c8b360d`)* |
| **Tracked Source Secret Scan** | `git grep` secret patterns ngoài test/docs | 0 matches | `ZERO_SECRET_MATCHES` (exit 0 wrapper) | **PASS** |

> **Lưu ý minh bạch về build Frontend**: `NEXT_DIST_DIR=.next-verify bun run build` được chạy và pass trước khi sửa bổ sung kiểu `MemberStatus` trong `src/types/labops.ts` và `safe-member-delete-dialog.tsx` ở Milestone 4; sau sửa đổi đó đã chạy lại đầy đủ `bun run typecheck`, `bun run lint` và `bun test` với kết quả **0 error**.

---

## 2. Chi tiết 3 Test `SKIPPED` trong `pytest`

Cả 3 test bị bỏ qua đều là các test có điều kiện gắn với phần cứng hoặc host Rack đang offline, **không phải lỗi logic**:
1. `backend/tests/test_adapters.py::TestAdapters::test_nvml_adapter_live_hardware` — **SKIPPED** (yêu cầu phần cứng NVML live ngoài ngữ cảnh adapter sandbox).
2. `backend/tests/test_v2_comprehensive.py::TestV2ModulesAndSecurity::test_real_host_aiotlab_rack_gpu_inventory_and_canary_reservation` — **SKIPPED** (host `aiotlab-rack` `192.168.167.250` hiện đang ngoại tuyến).
3. `backend/tests/test_v2_scanner.py::TestScanner::test_real_trivy_binary_executes_on_aiotlab_rack` — **SKIPPED** (host `aiotlab-rack` ngoại tuyến không thể thực thi Trivy từ xa).

---

## 3. Hạn chế Kiểm chứng Còn lại
- Chưa thực thi browser E2E thật (Playwright / Chrome) đối với giao diện web đang phục vụ để giữ nguyên quy tắc không can thiệp tiến trình `:3000` đang chạy.
- Chưa kết nối mTLS live giữa Workstation Gateway và Rack `aiotlab-rack` do Rack chưa bật nguồn/kết nối lại.
