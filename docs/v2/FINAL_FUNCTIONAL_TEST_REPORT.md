# Kiểm thử chức năng — 2026-11-10

`python3 -m unittest discover -s backend/tests`: 57 test, OK, 7 skipped. Test P0 mới xác minh lọc danh sách theo principal và trạng thái revoke ở cấp hàm, không chứng minh API hoặc runtime. Test GPU thật đã yêu cầu opt-in `LABOPS_TEST_REAL_HARDWARE=1`; không chạy khi chưa chỉ định canary. FastAPI/Pydantic bị thiếu, sandbox không cho mở listener, GPU test bị skip, và một test thiếu Trivy bị skip vì Trivy hiện có. Không có Node/Bun trong PATH nên chưa lint/typecheck/build.

BLOCKED_BY_INFRASTRUCTURE: FastAPI integration, browser E2E, PostgreSQL, Docker workspace, GPU, SSH/VPN, Arcane, Beszel, restore. Không có PASS_REAL.
