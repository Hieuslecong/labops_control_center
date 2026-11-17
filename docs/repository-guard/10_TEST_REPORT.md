# Repository Guard Verification Report — Current Evidence

Snapshot: 2026-11-17
This report supersedes the older PHASE 15 report below in Git history. The earlier “100% real tests / zero mocks” claim is withdrawn: current safe tests intentionally use fakes and mocks.

## Current executed evidence

- Go race suites: legacy CLI, AIOTGIT CLI, config, transport, Repository Guard and Docker packages PASS.
- Go AIOTGIT/Agent/Gateway builds PASS; binaries were created under /tmp only.
- Python AIOTGIT/workspace regression tests: 22 passed, one Pydantic deprecation warning.
- Frontend query tests: 3 passed; TypeScript noEmit and Next.js Webpack build PASS.
- See docs/hardening/04_REGRESSION_TEST_REPORT.md for exact commands and environment.

## Scope of these tests

- Temporary Git repositories and generated bundles.
- Fake Broker HTTP transport.
- Mocked workspace/runtime observations.
- Temporary Python test state.
- Fake Docker HTTP transport; no Docker socket.
- No production secrets, database, workspace, container, GPU workload, SSH/VPN runtime or host network policy.

## Not verified

- Live scanner or a separate constrained quarantine worker.
- Live Backend-to-Agent API flow.
- Persistent production artifact storage, backup or restore.
- Approval-to-bundle-to-workspace end-to-end delivery.
- Direct network egress or alternate-download bypass behavior.
- Host shell and terminal isolation.
- Full runtime behavior on Rack or Workstation.

Overall: source/unit evidence is available; runtime enforcement is NOT_VERIFIED and overall readiness is NOT_READY.
