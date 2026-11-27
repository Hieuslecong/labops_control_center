# LabOps Workload Intelligence V3.1 — Normative Metric Contract (G1)

All new V3.1 API fields use explicit units (`_bytes`, `elapsed_seconds`, `value_percent`) and nullable unknowns (`None`, displayed as `—` in UI). Missing or offline telemetry is never coerced to `0`.

## 1. KPI Definitions & Scope Separation

| KPI | API Field (`kpis.*`) | Entity Scope | Null / Partial Semantics |
| --- | --- | --- | --- |
| Workload quan sát | `observed_workloads` | Node, GPU, Member, Group (`quality='FRESH'` roots only) | `None` if `fresh_workload_nodes == 0`; `0` only on fresh empty observation; `PARTIAL` if partial node outage. |
| Workload gần nhất (Stale) | `last_known_workloads` | Separate count of `quality='STALE'` cached roots | Never added to `observed_workloads`; `None` if no stale rows. |
| Tác vụ có GPU | `gpu_associated_workloads`, `gpu_association_unknown` | Fresh roots where `gpu_associated is True` (or `None`) | Unknown reported distinctly; independent of `workload_kind`. |
| Thành viên hoạt động | `observed_members`, `unmapped_member_workloads` | Verified active `MemberRecord.id` values in fresh scope | Username without verified `member_id` counts under `unmapped_member_workloads`. |
| VRAM thiết bị | `device_vram_used_bytes`, `device_vram_capacity_bytes` | **Device-wide only** (`node_id`, `gpu_uuid`; ignores member/group/search) | Paired fresh GPU devices only (`mib * 1048576`); offline GPUs excluded; `used > capacity` triggers `MISMATCH`. |
| GPU có telemetry | `gpu_coverage.{fresh, expected}` | Authorized cluster inventory (`workstation=1`, `track=3`) | Fresh GPUs divided by expected authorized count (coverage, not free GPU count). |
| Cần chú ý | `attention_count` | Validated live snapshot conditions | Offline/stale/error nodes plus VRAM reconciliation mismatches. |

## 2. Process Resource, Unit & Taxonomy Rules

- **VRAM & Memory Conversion**: NVML and Go Collector MiB values are normalized strictly via `mib * 1048576` into integer `_bytes`. UI formats binary `GiB` (`bytes / 1073741824`).
- **RAM Attribution**: Root tree aggregation reports `memory.method = "TREE_RSS_SUM"` (`PROCESS_RSS` when single PID), with `unique_memory = false` and tooltip noting shared pages across parent-worker trees may be summed more than once. `PSS` is `UNSUPPORTED` unless explicitly queried per selected PID without extra host capabilities.
- **CPU Basis**: Reported per Go Collector / psutil with explicit `basis = "HOST_CAPACITY_PERCENT"` (or `"UNKNOWN"` when absent).
- **Workload Taxonomy**:
  - `MONITORING` (`gpustat`, `nvidia-smi`, `nvtop` exact executable basename or `python -m gpustat`) — never misclassified as AI training; substring matches inside directory names are ignored.
  - `NOTEBOOK` (`jupyter*`, `ipykernel`), `INFERENCE` (`vllm`, `tritonserver`, `ollama`), `TRAINING` (`torchrun`, `deepspeed`, `accelerate`, explicit `train.py`/`finetune`), `GENERAL_COMPUTE` (python scripts, `pytest`, binaries), `SYSTEM` (excluded from workload mode), `OTHER`/`UNKNOWN`.
- **Multi-GPU Per-PID Allocation**: Go Collector & adapter aggregate per `(pid, gpu_uuid)` into `gpu_allocations[]` (`PER_DEVICE_VERIFIED`). Legacy single representative totals without per-device mapping on multi-GPU nodes use `LEGACY_AGGREGATE` and `gpu_allocations = null`.
