# 08 — FRONTEND UI/UX REDESIGN SPECIFICATION
**LabOps Control Center V2 — Member Provisioning Wizard (`member-provisioning-wizard.tsx`)**

---

## 1. Overview of Changes

The Member Provisioning Wizard has been redesigned from a static form into an interactive, host-aware resource configuration console.

The wizard consists of 5 guided steps:
1. **Thông tin thành viên** (Account identity, username validation, Linux UID/GID)
2. **Máy chủ & Ổ đĩa lưu trữ** (Physical server selection, dynamic drive selector, quota input, path preview)
3. **Chính sách Tài nguyên** (Shared Weighted CPU, Shared Host-Protected RAM, GPU mode & VRAM)
4. **Phương thức SSH & Khóa** (Admin-managed Ed25519 key generation or custom public key)
5. **Xác nhận & Cấp phát** (Detailed configuration review, live validation check, submission, one-click zip bundle)

---

## 2. Step 2: Server & Dynamic Drive Selector

### 2.1 Server Selection Behavior
- Administrator selects between `aiot-ws1` (Workstation) and `aiotlab-rack` (Track).
- Selecting a new server immediately clears previous drive selections (`selectedDrive = null`) and triggers a dynamic fetch to `/api/labops/storage-pools?host_id=${serverId}`.

### 2.2 Drive Card Anatomy
Each discovered block device is displayed as an interactive card featuring:
- **Mount Point & Device**: E.g. `/media/LabOps-Data` · `ext4 · /dev/sdb1`
- **Quota Backend Badge**:
  - `🛡️ EXT4_PROJECT (Cưỡng chế cứng)` for quota-enabled drives.
  - `⚠️ Không hỗ trợ quota filesystem` for unverified drives.
- **Eligibility Indicator**:
  - `Đủ điều kiện` (Emerald badge) for valid storage pools.
  - `FORBIDDEN_SYSTEM_ROOT` (Destructive badge) with click-disabled interaction for system drives.
- **Visual Capacity Bar**:
  - Color-coded progress bar showing **Used space** (purple/primary), **Allocatable space** (emerald), and **Total capacity**.
  - Clearly distinguishes between raw free space and allocatable capacity (after deducting the 10% safety reserve).

### 2.3 Auto-Calculated Paths Preview
Live read-only preview card showing:
- **Host Storage Directory**: `<selectedDrive.mountPoint>/labops/users/<username>` (Mode: `0700`)
- **Container Mount Path**: `/workspace` (Fixed safe mount)

---

## 3. Step 3: Shared Resource Policy Console

Rather than asking the administrator to guess static core and RAM numbers, Step 3 displays the physical host's true capability:

### 3.1 Host Overview Header
Displays real-time hardware capacity (e.g. `16 CPU Cores · 128 GiB RAM`).

### 3.2 Block 1: Shared Weighted CPU (`SHARED_WEIGHTED`)
- Informs the administrator that the user is not pinned to fixed cores.
- Highlights burstability: container can utilize up to 100% of all host cores when idle.
- Emphasizes the `4096 PIDs limit` to protect the host against fork bombs.

### 3.3 Block 2: Shared Host-Protected RAM (`SHARED_HOST_PROTECTED`)
- Three-column visual metric breakdown:
  1. **Total Host RAM**: e.g. 128 GiB
  2. **OS Safety Reserve**: e.g. 25.6 GiB (20% reserved for Debian, Docker, PostgreSQL, SSH)
  3. **Container Shared Budget**: e.g. 102.4 GiB allocatable ceiling
- Reassures operators that containers cannot crash host system daemons under peak loads.

### 3.4 Block 3: GPU & VRAM Selection
- **GPU Device Selector**: Allows assigning dedicated A30 GPUs or CPU-only.
- **Allocation Mode**: Choice between `EXCLUSIVE` (Dedicated full GPU passthrough) and `SHARED`.
- **Honest VRAM Status**: Explicitly flags soft limits as `Chính sách giám sát (Policy-Monitored)`, maintaining complete transparency.

---

## 4. Accessibility & Quality Compliance

The component complies with Web Interface Guidelines:
- Static interactive elements replaced with semantic `<button type='button'>` elements with accessible keyboard navigation (`tabIndex`, `onKeyDown`).
- Strict TypeScript types (`StoragePoolInfo`, `HostResourceProfile`, `QuotaBackend`, `StorageVerificationStatus`) eliminate `any` casts.
- Clean oxlint validation: **0 warnings, 0 errors**.
