# Regression and Verification Report

Snapshot: 2026-11-17
Environment: development clone only. Python venv, Bun distribution and Go build cache are under /tmp. Frontend dependencies are locked; lifecycle scripts were disabled. No project .env or production credentials were loaded.

## Earlier full-suite evidence

The earlier remediation pass recorded these completed commands in the same clone:

- Python pytest full safe suite: 201 passed, 7 skipped, one Pydantic deprecation warning.
- Go unit suite and Go race suite: all then-existing packages passed.
- Go vet: all then-existing packages passed.
- Isolated PKI/Gateway TLS and gRPC mTLS tests: two packages passed using generated test certificates, temporary state and ephemeral loopback listeners.
- Frontend typecheck, lint (25 warnings, zero errors), deprecated API check, three query tests and Next.js Webpack build passed.

Those results precede the supplemental checks below and do not represent live service/runtime integration.

## Supplemental verification — current pass

### Go

Run from go directory:

    GOCACHE=/tmp/labops-v2-gocache GOPROXY=off GOTOOLCHAIN=local go test -race ./cmd/labops ./cmd/aiotgit ./internal/config ./internal/transport ./internal/aiotgit ./internal/repositoryguard ./internal/docker

PASS — all package suites. This includes legacy CLI repository-command denial, AIOTGIT generated-bundle behavior, Repository Guard authorization/artifact tests, fake Docker tests, Agent transport auth, and config requiring an explicit Repository Guard data directory. The live-host Agent endpoint test remained skipped unless explicitly opted in.

Builds, run from go directory:

    GOCACHE=/tmp/labops-v2-gocache GOPROXY=off GOTOOLCHAIN=local go build -o /tmp/labops-v2-aiotgit ./cmd/aiotgit
    GOCACHE=/tmp/labops-v2-gocache GOPROXY=off GOTOOLCHAIN=local go build -o /tmp/labops-v2-legacy-cli ./cmd/labops
    GOCACHE=/tmp/labops-v2-gocache GOPROXY=off GOTOOLCHAIN=local go build -o /tmp/labops-v2-agent ./cmd/labops-agent
    GOCACHE=/tmp/labops-v2-gocache GOPROXY=off GOTOOLCHAIN=local go build -o /tmp/labops-v2-gateway ./cmd/labops-gateway

PASS — all commands exited 0. The binaries were written under /tmp and were not installed or run as services.

Static analysis:

    GOCACHE=/tmp/labops-v2-gocache GOPROXY=off GOTOOLCHAIN=local go vet ./cmd/labops ./cmd/aiotgit ./internal/config ./internal/aiotgit ./internal/repositoryguard ./internal/docker ./internal/transport

PASS — exit 0.

### Python

Run from backend directory:

    PYTHONPATH=. /tmp/labops-hardening-venv/bin/python -m pytest -q tests/test_aiotgit_tokens.py tests/test_aiotgit_route_auth.py tests/test_aiotgit_routes.py tests/test_repository_workspace_binding.py tests/test_workspace_runtime_registry.py tests/test_egress_script_safety.py tests/test_internal_workspace_registry_api.py tests/test_workspace_controller_safety.py

PASS — 22 passed, one Pydantic class-config deprecation warning.

Syntax compilation:

    PYTHONPATH=. /tmp/labops-hardening-venv/bin/python -m compileall -q app tests

PASS — exit 0.

The pytest conftest clears LabOps/VPN/database variables and redirects app state into a disposable TemporaryDirectory. These tests call handlers/mocks; they did not connect to production services.

### Frontend

Repository request query tests:

    env -i PATH=/tmp/labops-v2-bun-dist/bun-linux-x64:/home/aiot-1/.vscode-server/cli/servers/Stable-6d62b1d5dace93a33b3a6d3eca1317bd5e15bcb1/server:/usr/bin:/bin HOME=/tmp/labops-v2-build-home TMPDIR=/tmp /tmp/labops-v2-bun-dist/bun-linux-x64/bun test frontend-tests/repository-requests-query.test.js

PASS — 3 tests, 5 assertions.

TypeScript:

    env -i PATH=/home/aiot-1/.vscode-server/cli/servers/Stable-6d62b1d5dace93a33b3a6d3eca1317bd5e15bcb1/server:/usr/bin:/bin HOME=/tmp/labops-v2-build-home TMPDIR=/tmp NEXT_TELEMETRY_DISABLED=1 CI=1 node node_modules/typescript/bin/tsc --noEmit

PASS — exit 0.

Next.js production build:

    env -i PATH=/home/aiot-1/.vscode-server/cli/servers/Stable-6d62b1d5dace93a33b3a6d3eca1317bd5e15bcb1/server:/usr/bin:/bin HOME=/tmp/labops-v2-build-home TMPDIR=/tmp NEXT_TELEMETRY_DISABLED=1 CI=1 node node_modules/next/dist/bin/next build --webpack

PASS — compiled routes and generated static pages. Non-fatal warnings: no Sentry auth token was provided, so release upload was skipped; metadataBase defaults to localhost:3000.

### Repository and final hygiene

- The egress script safety test passed in the targeted pytest command. The scripts are no-op guards and were not executed.
- git diff --check passed after the final source and report edits; untracked Markdown reports were separately checked for trailing whitespace.
- Python compileall creates no runtime service or network activity.

## Safety gate and skipped runtime work

- No Docker CLI, Docker API, host Docker socket, test container, or Docker network was used.
- No test database was available or contacted. PostgreSQL migrations/transactions and audit persistence have no current integration evidence.
- No live Backend-to-Agent request, Scanner worker, AIOTGIT approval-to-delivery flow, actual workspace CLI install, or test workspace was run.
- No direct Git/HTTP/SSH/proxy/IPv4/IPv6 bypass attempt was made because there is no isolated pilot network and existing workspaces were left untouched.
- No terminal broker exists in inspected source; host-shell versus workspace-session boundary remains unproven.
- Prior testing found the local FastAPI TestClient/ASGI path hangs even for a trivial app, so route logic was verified by direct handler calls and dependency inspection, not HTTP server E2E.

## Conclusion

Source-level CLI, scoped auth, bundle verification, local Git behavior and the tested packages have evidence. Full integrated service operation, durable production storage, quarantine isolation and workspace egress remain unverified. Overall readiness remains NOT_READY.
