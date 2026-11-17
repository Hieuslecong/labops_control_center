# Repository Guard Performance Report

Snapshot: 2026-11-17
Status: NOT BENCHMARKED

Earlier numeric estimates in this file were not backed by a reproducible benchmark command and are withdrawn as acceptance evidence.

## Current evidence

- AIOTGIT logic tests use generated repositories and a fake Broker.
- Go builds, targeted race tests and frontend builds completed.
- No live GitHub fetch, Trivy process, concurrent scan, production artifact storage, real workspace delivery or Rack load test was performed.
- No GPU job or production workload was used for benchmarking.

## Metrics required for a pilot

Measure, with resource limits and test-only repositories:

- request-to-scan latency and approval queue delay;
- quarantine storage, file-count and expanded-size limits;
- scanner CPU, memory and process count;
- artifact cache hit rate and bundle transfer throughput;
- delivery time and Git verification time;
- concurrent scans, retry behavior and failure recovery.

Set a disk budget before testing. Do not run load tests against production traffic or while using member workspaces.
