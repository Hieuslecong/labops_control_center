# Functional and integration test matrix

**Snapshot:** 2026-11-17. All PASS results below are isolated code/unit tests unless explicitly called loopback integration. No production service or user resource was used.

| Feature | Test / evidence | Expected | Actual | Result |
|---|---|---|---|---|
| Authentication and RBAC | Python auth/access-control tests in full pytest | Reject unverified identity and cross-user access | Isolated principal, role and ownership assertions pass | PASS (unit) |
| Session and CSRF | Python API auth tests | Invalid/expired or cross-origin mutation is rejected | Covered cases pass; deployed browser session not exercised | PASS (unit); runtime SKIP |
| Member suspend/delete | test_member_deletion_safety.py in full suite | Suspend/dry-run preserve data; permanent delete requires proofs | Safe paths pass; permanent cleanup remains blocked | PASS (safe-path unit); delete SKIP |
| Workspace lifecycle | workspace controller and runtime registry tests | Pending until verified runtime identity and limits | Registry/report validation passes; no runtime provisioning caller | PARTIAL |
| Docker ownership/stop | Go internal/docker tests with fake HTTP transport | Spoofed, wrong-owner, wrong-node or replaced container denied | Fake-registry/transport cases pass; no Docker daemon used | PASS (fake); runtime SKIP |
| CPU/RAM/PIDs/storage quotas | Workspace validation tests | Reject out-of-policy runtime observation | CPU/RAM/PID checks are tested; storage enforcement is not end-to-end | PARTIAL |
| GPU telemetry | Go/Python telemetry tests | Missing/stale values are degraded, UUIDs stable | Isolated parsing/freshness tests pass; live hardware test skipped | PASS (unit); hardware SKIP |
| GPU reservation | Python scheduler safety tests | Serialize concurrent reservations and never claim false FREE | Isolated locking/state tests pass; PostgreSQL concurrency unverified | PASS (unit); DB SKIP |
| Process monitoring/control | Go/Python process authorization/reference tests | Protect system processes and reject PID reuse/cross-user control | Unit safeguards pass; live /proc and real signal tests were not run | PASS (unit); live SKIP |
| Multi-node heartbeat/mTLS | Gateway loopback gRPC mTLS test | Bind cert/node identity and reject unknown/revoked node | Single test node mTLS/registration passes; Rack+Workstation mapping untested | PARTIAL |
| Repository scan/approval | Python scanner tests and Go service tests | Scanner failure cannot become approval | Isolated failure/policy tests pass; real Trivy subprocess skipped | PASS (unit); scanner runtime SKIP |
| Artifact delivery / aiotgit clone | Fake Broker bundle tests; Backend scoped-token/workspace tests; Go Agent resolver tests | Deliver only approved commit/digest to owned workspace and preserve Git metadata | Local Git publication and request binding pass in isolated tests; no live Broker-to-Agent or pilot workspace delivery | PASS (unit); E2E SKIP |
| Gateway actions | Go action allowlist tests | Reject arbitrary/mutating action without delegation | Read-only allowlist passes; mutating actions remain denied | PASS (deny boundary); delegated flow SKIP |
| PKI/revocation | Go persistence/fault tests and loopback TLS test | Persist revoke and deny replay/reconnect | Unit fault/restart tests and test-CA TLS handshake pass | PASS (isolated) |
| PostgreSQL/audit | Source review and Python atomic JSON tests | Transaction, isolation and durable audit in test DB | JSON paths have tests; no backend DB engine/session and no test server integration | SKIP / NOT IMPLEMENTED |
| Frontend/API | Typecheck, lint, query tests and Next build | Typed requests, errors surfaced, routes compile | Typecheck/build pass; query tests 3/3; no browser-to-live-API run | PASS (build/unit); E2E SKIP |
| Startup/readiness | bash -n and source review | No unsafe reload/kill/network changes; no false readiness | Syntax check only; scripts/services were not started | PASS (syntax); runtime SKIP |
| Repository egress | No namespace/host egress test run | All direct downloads denied or policy-enforced | No evidence that direct Git/HTTP/SSH downloads are blocked | SKIP; HOST_ENFORCEMENT_NOT_VERIFIED |

Full command evidence and skip reasons are in 04_REGRESSION_TEST_REPORT.md.
