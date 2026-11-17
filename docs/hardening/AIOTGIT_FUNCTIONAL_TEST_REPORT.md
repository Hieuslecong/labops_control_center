# AIOTGIT Functional Test Report

Snapshot: 2026-11-17
Environment: development clone; temporary Git repositories and files; fake HTTP Broker; no Rack Docker, DB, VPN or workspace runtime.

## Results

| Test / command | Expected | Actual | Result |
|---|---|---|---|
| Go AIOTGIT CLI package suite, included in go test -race ./internal/config ./internal/transport ./internal/aiotgit ./internal/repositoryguard ./internal/docker | CLI tests pass with no data race | Package PASS | PASS |
| Approved generated bundle clone | Git metadata exists, approved HEAD matches, clean tree, local Git workflow works | Verified in temporary repository; test performs local add/commit | PASS |
| Pending/unavailable Broker | No destination or source created; non-zero exit | No destination created | PASS |
| Bundle digest/commit tampering | Refuse publication | Refused by CLI tests | PASS |
| Destination traversal/symlink | Reject before Broker request | Rejected before request | PASS |
| aiotgit status | Show latest workspace-scoped request | Fake Broker response displayed | PASS |
| aiotgit status REQ-id | Query only the validated ID | Fake Broker path verified | PASS |
| Legacy labops clone/repo commands | Must not submit or deliver repositories with a machine token | Commands return nonzero and direct users to AIOTGIT; request/delivery network code was removed | PASS |
| Backend AIOTGIT route/token tests, targeted pytest command in 04_REGRESSION_TEST_REPORT.md | Scope and active workspace authorization; actor token forwarded unchanged | 22 tests across AIOTGIT/workspace registry group passed | PASS |
| Go build AIOTGIT, Agent and Gateway | Build source binaries only | All three builds exited 0; binaries under /tmp | PASS |
| Python compileall app and tests | Syntax compilation only | Exit 0 | PASS |
| Frontend repository request query test | Handle valid response, backend failure and malformed response | 3 passed, 5 assertions | PASS |
| TypeScript noEmit | Type contract valid | Exit 0 | PASS |
| Next.js Webpack production build | Compile app routes | Build completed; warnings documented in report 14 | PASS |
| Backend → Go Agent live API | Authenticated end-to-end request/approval/bundle | No test service started | SKIP |
| Scanner → approval → immutable bundle → CLI delivery | Approved commit and artifact arrive end-to-end | No live flow run | SKIP |
| Real pilot terminal and local Git | Terminal usable without outbound bypass; local Git works | No terminal broker or pilot exists | SKIP |
| Direct outbound bypass matrix | Deny Git/HTTP/SSH/proxy/IPv4/IPv6 paths | No isolated egress environment | SKIP |
| PostgreSQL test integration | Durable approval/audit/recovery | No isolated PostgreSQL server was available | SKIP |

## Exact commands run in this pass

Go, from go directory:

    GOCACHE=/tmp/labops-v2-gocache GOPROXY=off GOTOOLCHAIN=local go test -race ./cmd/labops ./cmd/aiotgit ./internal/config ./internal/transport ./internal/aiotgit ./internal/repositoryguard ./internal/docker

    GOCACHE=/tmp/labops-v2-gocache GOPROXY=off GOTOOLCHAIN=local go build -o /tmp/labops-v2-aiotgit ./cmd/aiotgit
    GOCACHE=/tmp/labops-v2-gocache GOPROXY=off GOTOOLCHAIN=local go build -o /tmp/labops-v2-legacy-cli ./cmd/labops
    GOCACHE=/tmp/labops-v2-gocache GOPROXY=off GOTOOLCHAIN=local go build -o /tmp/labops-v2-agent ./cmd/labops-agent
    GOCACHE=/tmp/labops-v2-gocache GOPROXY=off GOTOOLCHAIN=local go build -o /tmp/labops-v2-gateway ./cmd/labops-gateway
    GOCACHE=/tmp/labops-v2-gocache GOPROXY=off GOTOOLCHAIN=local go vet ./internal/config ./internal/aiotgit ./internal/repositoryguard ./internal/docker ./internal/transport ./cmd/aiotgit

Python, from backend directory:

    PYTHONPATH=. /tmp/labops-hardening-venv/bin/python -m pytest -q tests/test_aiotgit_tokens.py tests/test_aiotgit_route_auth.py tests/test_aiotgit_routes.py tests/test_repository_workspace_binding.py tests/test_workspace_runtime_registry.py tests/test_egress_script_safety.py tests/test_internal_workspace_registry_api.py tests/test_workspace_controller_safety.py

Frontend, from repository root:

    env -i PATH=/tmp/labops-v2-bun-dist/bun-linux-x64:/home/aiot-1/.vscode-server/cli/servers/Stable-6d62b1d5dace93a33b3a6d3eca1317bd5e15bcb1/server:/usr/bin:/bin HOME=/tmp/labops-v2-build-home TMPDIR=/tmp /tmp/labops-v2-bun-dist/bun-linux-x64/bun test frontend-tests/repository-requests-query.test.js

    env -i PATH=/home/aiot-1/.vscode-server/cli/servers/Stable-6d62b1d5dace93a33b3a6d3eca1317bd5e15bcb1/server:/usr/bin:/bin HOME=/tmp/labops-v2-build-home TMPDIR=/tmp NEXT_TELEMETRY_DISABLED=1 CI=1 node node_modules/typescript/bin/tsc --noEmit

    env -i PATH=/home/aiot-1/.vscode-server/cli/servers/Stable-6d62b1d5dace93a33b3a6d3eca1317bd5e15bcb1/server:/usr/bin:/bin HOME=/tmp/labops-v2-build-home TMPDIR=/tmp NEXT_TELEMETRY_DISABLED=1 CI=1 node node_modules/next/dist/bin/next build --webpack

## Limits

The CLI's local approval/delivery behavior has evidence, not Broker runtime evidence. A user-facing CLI binary, configuration and scoped token have not been installed into an actual workspace. Browser, PostgreSQL, Docker, scanner worker and terminal integration were not exercised.
