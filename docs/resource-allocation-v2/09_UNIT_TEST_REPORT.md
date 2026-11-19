# 09 — UNIT TEST EXECUTION REPORT
**LabOps Control Center V2 — Resource Allocation & Storage Subsystem**

---

## 1. Scope of Unit Testing

Unit testing validates the individual algorithms, admission checks, cgroup configurations, and models across both Go and Python components:
- **Go Node Agent**:
  - `go/internal/storage/`: Eligibility rules, path sanitization, project quota derivation, and concurrency safety.
  - `go/internal/resources/`: Shared weighted CPU calculations, 20% host RAM safety reserve computation, and Linux PSI parsing.
  - `go/internal/docker/`: `SHARED_WEIGHTED` container parameter translation, bind mount formatting (`MemberStorageDir:ContainerMountPath:rw`), and safety constraint validation.
- **Python Backend**:
  - `backend/app/services/storage_inventory_service.py`: Path traversal protection, admission capacity limits, and custom pool registry.
  - `backend/app/services/labops_service.py`: Member provisioning request validation and exclusive GPU conflict resolution.

---

## 2. Test Execution Results

### 2.1 Go Unit Test Summary
```text
=== RUN   TestCPUPolicy_SharedWeighted
--- PASS: TestCPUPolicy_SharedWeighted (0.00s)
=== RUN   TestMemoryPolicy_SharedHostProtected
--- PASS: TestMemoryPolicy_SharedHostProtected (0.00s)
PASS ok  github.com/Hieuslecong/labops_control_center/go/internal/resources 0.015s

=== RUN   TestStorageEligibility_Rules
--- PASS: TestStorageEligibility_Rules (0.00s)
=== RUN   TestStorageProvisioning_DedicatedDirectoryAndIsolation
--- PASS: TestStorageProvisioning_DedicatedDirectoryAndIsolation (0.02s)
=== RUN   TestStorageProvisioning_SecuritySymlinkAndPathTraversal
--- PASS: TestStorageProvisioning_SecuritySymlinkAndPathTraversal (0.01s)
=== RUN   TestStorageAdmission_CapacityAndConcurrency
--- PASS: TestStorageAdmission_CapacityAndConcurrency (0.03s)
PASS ok  github.com/Hieuslecong/labops_control_center/go/internal/storage 0.065s

=== RUN   TestDockerController_ProtectionRules
--- PASS: TestDockerController_ProtectionRules (0.00s)
=== RUN   TestTrustedRegistryAndDockerProvisioningMatrix
--- PASS: TestTrustedRegistryAndDockerProvisioningMatrix (0.01s)
PASS ok  github.com/Hieuslecong/labops_control_center/go/internal/docker 0.022s
```

### 2.2 Key Invariants Verified in Units
1. **Root Directory Protection**: Subpaths of `/`, `/home`, `/etc`, `/var/lib/docker` are correctly rejected as `INELIGIBLE`.
2. **Directory Isolation**: Newly provisioned directories are assigned `0700` POSIX permissions with required subdirectories (`work/`, `checkpoints/`, `datasets/`, `.ssh/`).
3. **Burstable CPU**: `ValidateContainerSafety` asserts `NanoCPUs == 0` and `CpuShares > 0` for `SHARED_WEIGHTED` mode.
4. **Memory Reserve Floor**: Calculations verify that host safety reserve never drops below 20% of physical RAM or the minimum floor (8 GiB on WS, 16 GiB on Rack).
