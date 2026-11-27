# Prototype verification

Local Chromium via Playwright, 25/11/2026. Standalone HTML only; no LabOps API or production connection.

- PASS — default six KPIs, five snapshot rows, history empty
- PASS — explicit demo switch renders labeled history
- PASS — member and PID filters
- PASS — filtered CSV export
- PASS — runtime numeric sorting both directions
- PASS — CPU sorting
- PASS — column toggle
- PASS — raw diagnostics empty state
- PASS — detail drawer / disabled destructive actions / Escape
- PASS — offline scope does not fabricate zero jobs
- PASS — attention matches selected node scope
- PASS — stable owner color across filters
- PASS — range changes demo chart not live KPIs
- PASS — incompatible rack filters cleared
- PASS — theme toggle
- PASS — 390px mobile viewport without page-level horizontal overflow
- PASS — no pageerror events in tested paths.

Not verified: application code, staging/production API, authentication, real telemetry, process actions, migration, network performance, full WCAG audit. Screenshots are captured from the local prototype.
