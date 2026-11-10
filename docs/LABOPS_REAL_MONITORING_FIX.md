# LabOps monitoring status — evidence boundary

This note supersedes the earlier monitoring report in this file. Its PID, hardware, kernel, GPU, SSH-session, latency, and test claims were not re-verified in the current restricted execution environment and must not be treated as production evidence.

The code contains local read-only adapters for host/process/GPU telemetry. They can report only what the process running the backend can observe. A backend running in a container reports host monitoring as `CONTAINER_ONLY`; the remote Track node reports `unknown` until a remote collector/Gateway integration exists. GPU-hours, historical CPU/RAM charts, and security review results have no verified production collectors.

Current verified checks are recorded in [PRODUCTION_ACTIVATION_REPORT.md](PRODUCTION_ACTIVATION_REPORT.md). They cover isolated backend unit tests and frontend typecheck only. Live host telemetry, GPU attribution, WireGuard, SSH sessions, and remote Track remain `NOT VERIFIED`.
