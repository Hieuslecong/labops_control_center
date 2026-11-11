# Báo cáo Kiểm thử Hồi quy Toàn diện (Regression Test Report)

**Dự án:** LabOps Control Center  
**Thời điểm thực hiện:** 11/11/2026  
**Môi trường thử nghiệm:** Debian 12 (aiotlab-rack 192.168.167.250), Go Node Agent (port 9100), FastAPI backend (port 8000), Next.js App Router (port 3000).

---

## 1. Kết quả Kiểm tra Mã nguồn & Typescript (Frontend)

### 1.1. TypeScript Compiler (`tsc --noEmit`)
* **Lệnh thực thi:**
  `node ./node_modules/typescript/bin/tsc --noEmit`
* **Kết quả:** **0 lỗi** (Exit code: 0).
* **Phạm vi kiểm tra:**
  * Toàn bộ các định nghĩa TypeScript mở rộng trong `src/types/index.ts` và `src/types/labops.ts`.
  * Các trang mới: `/dashboard/researcher/connect/page.tsx`, `/dashboard/researcher/workspace/page.tsx`, `/dashboard/researcher/jobs/page.tsx`.
  * Endpoint BFF `/api/labops/session/route.ts`.
  * Các component và hook: `use-auth-session.ts`, `use-nav.ts`, `app-sidebar.tsx`, `header.tsx`, `researcher-portal-view.tsx`, `member-management-view.tsx`.

### 1.2. Oxlint Static Analysis
* **Lệnh thực thi:**
  `node ./node_modules/oxlint/bin/oxlint src/components/layout/app-sidebar.tsx src/components/layout/header.tsx src/hooks/use-auth-session.ts src/hooks/use-nav.ts src/features/researcher/components/researcher-portal-view.tsx src/features/users/components/member-management-view.tsx src/app/api/labops/session/route.ts`
* **Kết quả:** **0 lỗi** (Exit code: 0).

---

## 2. Kết quả Kiểm thử Go Node Agent (Hardware & OS Runtime)

* **Lệnh thực thi:**
  `cd go && go test -v ./...`
* **Kết quả:** **100% PASSED** (Exit code: 0).
* **Các test case quan trọng:**
  * `TestDockerController_ProtectionRules`: PASS
  * `TestDockerController_ListRealWorkspaces`: PASS
  * `TestGPUCollector_Collect`: PASS (Đọc chính xác telemetry GPU NVIDIA A30)
  * `TestHostCollector_Collect` & `TestHostCollector_CPUSampleDelta`: PASS
  * `TestProcessCollector_CollectCurrentProcess`: PASS
  * `TestRef_BuildAndParse`: PASS
  * `TestCheckSafeGuard_ProtectedProcesses`: PASS (Chặn dừng PID 1, systemd, dockerd, labops-agent)
  * `TestCheckSafeGuard_BackendProtection`: PASS (Chặn dừng uvicorn, app.main)
  * `TestServer_Endpoints`: PASS

---

## 3. Kết quả Kiểm thử Backend FastAPI & Tích hợp Hệ thống (Pytest)

* **Lệnh thực thi:**
  `PYTHONPATH=backend pytest backend/tests/test_*.py -q`
* **Kết quả tổng thể:** **103 passed, 2 skipped, 0 failed in 52.11s (100% Thành công)**.

### Chi tiết các tệp kiểm thử:
1. `backend/tests/test_v2_vpn_wgeasy.py`: **4/4 PASSED (100%)**
   * Quản lý đa thiết bị WireGuard, cấp IP subnet `10.88.40.0/24`, tải file `.conf`, thu hồi thiết bị, xóa vĩnh viễn thành viên.
2. `backend/tests/test_process_control.py`: **8/8 PASSED (100%)**
   * Phân loại 4 nhóm tiến trình (SYSTEM, MEMBER, RESEARCH_JOB, GPU_PROCESS).
   * Chống PID reuse bằng start time ticks (409 Conflict).
   * Bảo vệ PID 1 và kernel threads không thể bị dừng (403 Forbidden).
   * Dừng an toàn (SIGTERM) và buộc dừng (SIGKILL) qua Go Node Agent.
3. `backend/tests/test_admin_ssh_lifecycle.py`: **9/9 PASSED (100%)**
   * Vòng đời khóa SSH do Admin quản lý.
   * Canary SSH runtime trên Debian rack thật.
   * Mật khẩu tạm đăng nhập lần đầu và chính sách One-Time Vault.
4. `backend/tests/test_v2_comprehensive.py`: **10/10 PASSED (100%)**
   * Kiểm soát phạm vi dữ liệu Cổng nghiên cứu viên.
   * Chặn truy cập trái phép vào API Admin (403 Forbidden).
   * CORS và xác thực JWT Bearer token.
5. `backend/tests/test_v2_monitoring.py`, `test_v2_host_registry.py`, `test_wireguard_provisioning.py`: **PASSED**.

