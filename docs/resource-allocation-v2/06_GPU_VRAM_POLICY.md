# 06 — GPU ALLOCATION MODES & VRAM POLICY TRUTHFULNESS
**LabOps Control Center V2 — Resource Allocation & Storage Subsystem**

---

## 1. Overview & Physical Hardware Context

The LabOps cluster hosts NVIDIA A30 Tensor Core GPUs (24 GB HBM2 VRAM per device):
- **Workstation (`aiot-ws1`)**: 1 × NVIDIA A30 (GPU 0)
- **Rack (`aiotlab-rack`)**: 3 × NVIDIA A30 (GPU 0, GPU 1, GPU 2)

Without hardware Multi-Instance GPU (MIG) slicing enabled, NVIDIA driver architectures do not support hard hardware partitioning of VRAM among multiple non-MIG processes sharing the same physical GPU device. Attempting to claim "HARD_ENFORCED" VRAM isolation on non-MIG GPUs is misleading.

---

## 2. GPU Allocation Modes

LabOps Control Center V2 provides two explicit GPU allocation modes:

### 2.1 Mode 1: Exclusive GPU Allocation (`EXCLUSIVE` — Recommended)
- **Mechanism**: The container is passed dedicated access to the GPU via Docker NVIDIA Container Toolkit:
  ```json
  "Env": ["NVIDIA_VISIBLE_DEVICES=0"],
  "DeviceRequests": [{"Driver": "nvidia", "DeviceIDs": ["0"], "Capabilities": [["gpu"]]}]
  ```
- **Conflict Prevention**: The admission controller in `labops_service.py` strictly prevents overbooking. If GPU 0 on `workstation` is allocated to `@namnv` under `EXCLUSIVE` mode, any subsequent attempt by an admin to assign GPU 0 exclusively to another user is rejected with:
  ```text
  ValueError: Xung đột cấp phát GPU: GPU 'gpu-ws1-0' đã được cấp phát độc quyền (EXCLUSIVE) cho thành viên @namnv
  ```
- **VRAM Status**: `DEDICATED_HARDWARE` / `POLICY_LIMIT` (Full 24 GB VRAM available to the container).

### 2.2 Mode 2: Shared GPU Allocation (`SHARED`)
- **Mechanism**: Multiple researcher containers share visibility of the GPU device.
- **Quota Tracking**: Admins specify a soft limit (e.g. 12 GB VRAM).
- **Runtime Monitoring**: Continuous NVML polling via `HostMonitorAdapter` and `NvmlAdapter` tracks per-process VRAM consumption.
- **VRAM Status**: Honest reporting as `POLICY_LIMIT` or `MONITORED`.
- **Truthfulness Contract**: The system **never** reports `HARD_ENFORCED` for VRAM unless verified by hardware MIG partitioning.

---

## 3. Truthfulness Status Taxonomy

| Status Code | Meaning | When Used |
| :--- | :--- | :--- |
| `NOT_CONFIGURED` | Member has no GPU access | `selectedGpu == 'NONE'` |
| `MONITORED` | Telemetry tracks VRAM; alerts on breach | Shared GPU mode with active telemetry |
| `POLICY_LIMIT` | Administrative quota tracked in db | Standard shared GPU allocation |
| `HARD_ENFORCED` | Hardware MIG slice active | **Only** when NVIDIA MIG is verified active |
| `NOT_APPLICABLE` | CPU-only workload | Container has no GPU devices mounted |

This transparent classification eliminates confusion and ensures researchers understand the real isolation boundaries of their computational environment.
