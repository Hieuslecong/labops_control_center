# Compatibility matrix — release gates

All cells initially NOT_RUN against actual repository. This is an acceptance plan, not test evidence. Audit actual versions in G0. Legacy = reviewed/current running API before upgrade; new = proposed workload feature. Use sanitized fixtures and isolated staging, not production. Zalo source remains unchanged.

| Combination | Required behavior | Gate |
|---|---|---|
| Old frontend + new backend | Existing `/jobs`, `/jobs/active`, `/processes` shapes, units, auth and error behavior retained | G1/G7 |
| New frontend + old backend | Feature capability unavailable or flag off; legacy UI reachable; no fake metrics | G2/G7 |
| New backend + old Go Agent | Legacy metrics still render; missing identity/attribution marked unsupported; new dangerous actions disabled | G1/G5 |
| Old backend + new Go Agent | Additive fields ignored safely; old ref format accepted for legacy reads; old protected behavior retained | G1/G7 |
| Old backend + additive new DB schema | Starts, reads and writes original schema; no required new fields for old writes | G4/G7 |
| New backend + old DB schema | Feature off or read-only capability degraded; no migration-on-read or startup production writes | G4/G7 |
| Old/new history JSON rows | Version-aware reader; missing source semantics => null/unsupported; no fabricated mapping | G4 |
| Existing Zalo reader + new shared modules | Identical legacy fixture input/output contract, no worker/notification/config change | G0/G1/G7 |
| Concurrent Zalo migration changes | No conflicting heads/revision overwrite; defer migration if unresolved | G0/G4 |
| Admin → researcher/logout/relogin | Backend denies old permissions, query caches cleared, old in-flight responses discarded | G2/G5/G7 |
| Two browser tabs, multiple admin sessions | No cross-principal leakage; no duplicate collector scan per panel; safe target lock | G3/G5 |
| Node reboot / PID reuse | New immutable identity; old ref not actionable through new controls; no merged history | G1/G5 |
| All/partial offline and collector unconfigured | Correct coverage/null/stale; no phantom workload completion | G1/G4 |
| Feature flag off after upgrade | Existing route/sidebar/dialogs unchanged; no V3.1 requests/actions; no shared telemetry deletion | G7 |

For each row record tested versions/SHAs, fixture, commands, exit codes, result, screenshots/log references and blockers. Compare existing consumers before and after shared changes (do not change their tests to fit). If a mandatory row fails, keep feature flag off and do not release dependent functionality. Feature-off rollback must not change RBAC or old route semantics. History V2 optional only; cannot defer safety or legacy compatibility.
