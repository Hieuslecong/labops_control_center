-- ============================================================================
-- Migration 004: LabOps Control Center V2 Complete Schema
-- Tách biệt rõ Desired State (trạng thái mong muốn) và Observed State (trạng thái thực tế)
-- Hỗ trợ N máy chủ động, GPU theo UUID, WireGuard + OpenSSH per-member public keys,
-- Docker/Incus Workspaces, GPU Scheduler (FULL_GPU_EXCLUSIVE), Trivy Scans, Beszel Alerts.
-- ============================================================================

BEGIN;

-- 1. Bảng Máy chủ vật lý (Dynamic Host Registry)
CREATE TABLE IF NOT EXISTS v2_hosts (
    id VARCHAR(64) PRIMARY KEY,
    hostname VARCHAR(255) NOT NULL UNIQUE,
    display_name VARCHAR(255) NOT NULL,
    role VARCHAR(64) NOT NULL DEFAULT 'COMPUTE_NODE', -- CONTROL_PLANE | COMPUTE_NODE | STORAGE_NODE | HYBRID
    management_ip VARCHAR(64) NOT NULL,
    vpn_ip VARCHAR(64),
    public_endpoint VARCHAR(255),
    ssh_legacy_port INTEGER DEFAULT 22,
    agent_url VARCHAR(255),
    agent_version VARCHAR(64),
    agent_token_hash VARCHAR(128),
    desired_state VARCHAR(32) NOT NULL DEFAULT 'ACTIVE', -- ACTIVE | MAINTENANCE | DRAINING | DISABLED
    observed_state VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN', -- ONLINE | STALE | OFFLINE | UNKNOWN
    cpu_model VARCHAR(255),
    cpu_sockets INTEGER DEFAULT 1,
    cpu_cores INTEGER DEFAULT 0,
    cpu_threads INTEGER DEFAULT 0,
    ram_total_mb BIGINT DEFAULT 0,
    os_name VARCHAR(128),
    kernel_version VARCHAR(128),
    nvidia_driver_version VARCHAR(64),
    cuda_version VARCHAR(64),
    docker_version VARCHAR(64),
    incus_version VARCHAR(64),
    arcane_environment_id VARCHAR(128),
    beszel_system_id VARCHAR(128),
    last_heartbeat_at TIMESTAMPTZ,
    telemetry_source VARCHAR(64) DEFAULT 'UNKNOWN',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2. Bảng Thiết bị GPU vật lý (Định danh theo UUID thực)
CREATE TABLE IF NOT EXISTS v2_gpu_devices (
    uuid VARCHAR(96) PRIMARY KEY,
    host_id VARCHAR(64) NOT NULL REFERENCES v2_hosts(id) ON DELETE CASCADE,
    device_index INTEGER NOT NULL,
    display_label VARCHAR(64) NOT NULL,
    model VARCHAR(128) NOT NULL,
    pci_bus_id VARCHAR(64),
    vram_total_mb INTEGER NOT NULL DEFAULT 24576,
    mig_capable BOOLEAN NOT NULL DEFAULT TRUE,
    mig_mode_current VARCHAR(32) NOT NULL DEFAULT 'DISABLED', -- DISABLED | ENABLED | UNKNOWN
    desired_allocation_mode VARCHAR(32) NOT NULL DEFAULT 'FULL_GPU_EXCLUSIVE',
    observed_state VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN', -- FREE | RESERVED | IN_USE | BUSY_EXTERNAL | OFFLINE | UNKNOWN
    vram_used_mb INTEGER DEFAULT 0,
    gpu_util_percent NUMERIC(5,2) DEFAULT 0,
    temp_celsius INTEGER DEFAULT 0,
    power_watts NUMERIC(6,2) DEFAULT 0,
    power_limit_watts NUMERIC(6,2) DEFAULT 0,
    telemetry_source VARCHAR(64) DEFAULT 'nvidia-smi',
    collected_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (host_id, device_index)
);

-- 3. Bảng Thực thể GPU / MIG Instance (tương thích tương lai cho MIG)
CREATE TABLE IF NOT EXISTS v2_gpu_instances (
    id VARCHAR(96) PRIMARY KEY,
    gpu_uuid VARCHAR(96) NOT NULL REFERENCES v2_gpu_devices(uuid) ON DELETE CASCADE,
    instance_type VARCHAR(32) NOT NULL DEFAULT 'FULL_GPU', -- FULL_GPU | MIG_1G_6GB | MIG_2G_12GB | MIG_4G_24GB
    profile_id VARCHAR(64),
    vram_mb INTEGER NOT NULL,
    desired_state VARCHAR(32) NOT NULL DEFAULT 'AVAILABLE',
    observed_state VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 4. Bảng Chính sách Tài nguyên (Resource Policies / Quotas)
CREATE TABLE IF NOT EXISTS v2_resource_policies (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(128) NOT NULL UNIQUE,
    role_scope VARCHAR(32) NOT NULL DEFAULT 'RESEARCHER', -- ADMIN | RESEARCHER
    max_concurrent_gpus INTEGER NOT NULL DEFAULT 1,
    max_gpu_hours_weekly NUMERIC(8,2) NOT NULL DEFAULT 40.0,
    max_reservation_hours INTEGER NOT NULL DEFAULT 24,
    cpu_cores_limit INTEGER NOT NULL DEFAULT 8,
    ram_mb_limit INTEGER NOT NULL DEFAULT 32768,
    pids_limit INTEGER NOT NULL DEFAULT 4096,
    storage_quota_gb INTEGER NOT NULL DEFAULT 200,
    allow_internet_egress BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 5. Bảng Thành viên (Members)
CREATE TABLE IF NOT EXISTS v2_members (
    id VARCHAR(64) PRIMARY KEY,
    username VARCHAR(64) NOT NULL UNIQUE,
    full_name VARCHAR(255) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    role VARCHAR(32) NOT NULL DEFAULT 'RESEARCHER', -- ADMIN | RESEARCHER
    research_group VARCHAR(128),
    assigned_host_id VARCHAR(64) REFERENCES v2_hosts(id) ON DELETE SET NULL,
    policy_id VARCHAR(64) REFERENCES v2_resource_policies(id) ON DELETE SET NULL,
    desired_status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE', -- ACTIVE | SUSPENDED | REVOKED
    observed_status VARCHAR(32) NOT NULL DEFAULT 'PENDING_VERIFICATION',
    uid_number INTEGER,
    gid_number INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 6. Bảng Khóa Công khai SSH của Thành viên (OpenSSH per-member public keys)
CREATE TABLE IF NOT EXISTS v2_ssh_public_keys (
    id VARCHAR(64) PRIMARY KEY,
    member_id VARCHAR(64) NOT NULL REFERENCES v2_members(id) ON DELETE CASCADE,
    label VARCHAR(128) NOT NULL,
    key_type VARCHAR(32) NOT NULL, -- ssh-ed25519 | rsa-sha2-256 | ecdsa-sha2-nistp256
    public_key TEXT NOT NULL,
    fingerprint_sha256 VARCHAR(128) NOT NULL UNIQUE,
    desired_state VARCHAR(32) NOT NULL DEFAULT 'ACTIVE', -- ACTIVE | REVOKED
    observed_state VARCHAR(32) NOT NULL DEFAULT 'PENDING_SYNC', -- SYNCED | PENDING_SYNC | FAILED | REVOKED
    last_synced_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ
);

-- 7. Bảng Thiết bị / Peer VPN WireGuard
CREATE TABLE IF NOT EXISTS v2_vpn_peers (
    id VARCHAR(64) PRIMARY KEY,
    member_id VARCHAR(64) REFERENCES v2_members(id) ON DELETE CASCADE,
    host_id VARCHAR(64) REFERENCES v2_hosts(id) ON DELETE SET NULL,
    device_name VARCHAR(128) NOT NULL,
    interface_name VARCHAR(32) NOT NULL DEFAULT 'wg0',
    public_key VARCHAR(128) NOT NULL UNIQUE,
    assigned_ip CIDR NOT NULL UNIQUE,
    allowed_ips TEXT NOT NULL,
    desired_state VARCHAR(32) NOT NULL DEFAULT 'ACTIVE', -- ACTIVE | REVOKED | DISABLED
    observed_state VARCHAR(32) NOT NULL DEFAULT 'NOT_VERIFIED', -- ACTIVE | HANDSHAKE_STALE | NOT_VERIFIED | REVOKED | FAILED
    last_handshake_at TIMESTAMPTZ,
    rx_bytes BIGINT DEFAULT 0,
    tx_bytes BIGINT DEFAULT 0,
    is_legacy_preserved BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ
);

-- 8. Bảng Môi trường Làm việc Cách ly (Workspaces: Docker & Incus Legacy)
CREATE TABLE IF NOT EXISTS v2_workspaces (
    id VARCHAR(64) PRIMARY KEY,
    name VARCHAR(128) NOT NULL UNIQUE,
    member_id VARCHAR(64) NOT NULL REFERENCES v2_members(id) ON DELETE RESTRICT,
    host_id VARCHAR(64) NOT NULL REFERENCES v2_hosts(id) ON DELETE RESTRICT,
    runtime_type VARCHAR(32) NOT NULL DEFAULT 'DOCKER', -- DOCKER | INCUS_LEGACY
    image_ref VARCHAR(255) NOT NULL,
    container_id VARCHAR(128),
    ssh_host_port INTEGER,
    vpn_bind_ip VARCHAR(64),
    cpu_cores_limit INTEGER NOT NULL DEFAULT 8,
    ram_mb_limit INTEGER NOT NULL DEFAULT 32768,
    pids_limit INTEGER NOT NULL DEFAULT 4096,
    storage_path VARCHAR(512),
    storage_quota_gb INTEGER NOT NULL DEFAULT 200,
    non_privileged BOOLEAN NOT NULL DEFAULT TRUE,
    docker_socket_mounted BOOLEAN NOT NULL DEFAULT FALSE,
    desired_state VARCHAR(32) NOT NULL DEFAULT 'ACTIVE', -- REQUESTED | ACTIVE | STOPPED | DELETED
    lifecycle_state VARCHAR(32) NOT NULL DEFAULT 'REQUESTED',
    -- REQUESTED -> VALIDATING -> PROVISIONING -> VERIFYING -> ACTIVE | FAILED | PENDING_RETRY | PARTIAL_FAILURE | NOT_VERIFIED
    observed_state VARCHAR(32) NOT NULL DEFAULT 'NOT_VERIFIED',
    idempotency_key VARCHAR(128) UNIQUE,
    last_verified_at TIMESTAMPTZ,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (host_id, ssh_host_port)
);

-- 9. Bảng Giữ chỗ & Đặt lịch GPU (Reservations)
CREATE TABLE IF NOT EXISTS v2_reservations (
    id VARCHAR(64) PRIMARY KEY,
    gpu_uuid VARCHAR(96) NOT NULL REFERENCES v2_gpu_devices(uuid) ON DELETE RESTRICT,
    member_id VARCHAR(64) NOT NULL REFERENCES v2_members(id) ON DELETE RESTRICT,
    workspace_id VARCHAR(64) REFERENCES v2_workspaces(id) ON DELETE SET NULL,
    allocation_mode VARCHAR(32) NOT NULL DEFAULT 'FULL_GPU_EXCLUSIVE',
    desired_state VARCHAR(32) NOT NULL DEFAULT 'RESERVED', -- QUEUED | RESERVED | RELEASED | CANCELLED
    observed_state VARCHAR(32) NOT NULL DEFAULT 'PENDING_VERIFICATION', -- RESERVED | STARTING | RUNNING | STOP_REQUESTED | RELEASED | FAILED
    runtime_verified BOOLEAN NOT NULL DEFAULT FALSE,
    idempotency_key VARCHAR(128) UNIQUE,
    reserved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    notes TEXT
);

-- 10. Bảng Tác vụ Tính toán (Jobs)
CREATE TABLE IF NOT EXISTS v2_jobs (
    id VARCHAR(64) PRIMARY KEY,
    reservation_id VARCHAR(64) REFERENCES v2_reservations(id) ON DELETE SET NULL,
    member_id VARCHAR(64) NOT NULL REFERENCES v2_members(id) ON DELETE RESTRICT,
    host_id VARCHAR(64) NOT NULL REFERENCES v2_hosts(id) ON DELETE RESTRICT,
    workspace_id VARCHAR(64) REFERENCES v2_workspaces(id) ON DELETE SET NULL,
    gpu_uuid VARCHAR(96) REFERENCES v2_gpu_devices(uuid) ON DELETE SET NULL,
    job_name VARCHAR(255) NOT NULL,
    command TEXT,
    observed_pid INTEGER,
    desired_state VARCHAR(32) NOT NULL DEFAULT 'RUNNING', -- QUEUED | RUNNING | STOPPED
    observed_state VARCHAR(32) NOT NULL DEFAULT 'PENDING_VERIFICATION', -- QUEUED | STARTING | RUNNING | STOP_REQUESTED | STOPPED | FAILED | UNKNOWN
    runtime_verified BOOLEAN NOT NULL DEFAULT FALSE,
    allocated_seconds BIGINT DEFAULT 0,
    active_runtime_seconds BIGINT DEFAULT 0,
    idle_hold_seconds BIGINT DEFAULT 0,
    avg_gpu_util_percent NUMERIC(5,2),
    max_vram_used_mb INTEGER,
    idempotency_key VARCHAR(128) UNIQUE,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ
);

-- 11. Bảng Quét An toàn Mã nguồn & Container (Trivy Security Scans)
CREATE TABLE IF NOT EXISTS v2_security_scans (
    id VARCHAR(64) PRIMARY KEY,
    submitted_by VARCHAR(64) NOT NULL,
    repo_url TEXT NOT NULL,
    commit_sha VARCHAR(64) NOT NULL,
    container_image VARCHAR(255),
    state VARCHAR(32) NOT NULL DEFAULT 'SUBMITTED',
    -- SUBMITTED -> FETCHING -> SCANNING -> POLICY_EVALUATION -> PASSED | REVIEW_REQUIRED | REJECTED | FAILED
    scanner_version VARCHAR(64),
    db_version VARCHAR(64),
    policy_version VARCHAR(64) NOT NULL,
    cache_key VARCHAR(512) NOT NULL,
    vuln_critical INTEGER DEFAULT 0,
    vuln_high INTEGER DEFAULT 0,
    vuln_medium INTEGER DEFAULT 0,
    vuln_low INTEGER DEFAULT 0,
    secrets_count INTEGER DEFAULT 0,
    misconfig_high INTEGER DEFAULT 0,
    admin_decision VARCHAR(32), -- APPROVED | REJECTED
    reviewed_by VARCHAR(64),
    reviewed_at TIMESTAMPTZ,
    error_message TEXT,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 12. Bảng Nhật ký Kiểm toán Bất biến (Audit Events)
CREATE TABLE IF NOT EXISTS v2_audit_events (
    id VARCHAR(64) PRIMARY KEY,
    actor_username VARCHAR(128) NOT NULL,
    actor_role VARCHAR(32) NOT NULL,
    action VARCHAR(128) NOT NULL,
    target_type VARCHAR(64) NOT NULL,
    target_id VARCHAR(128) NOT NULL,
    result VARCHAR(32) NOT NULL, -- SUCCESS | DENIED | FAILED | PENDING_VERIFICATION
    correlation_id VARCHAR(128) NOT NULL,
    request_ip VARCHAR(64),
    details JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 13. Bảng Cảnh báo Hệ thống (Alerts)
CREATE TABLE IF NOT EXISTS v2_alerts (
    id VARCHAR(64) PRIMARY KEY,
    host_id VARCHAR(64) REFERENCES v2_hosts(id) ON DELETE CASCADE,
    gpu_uuid VARCHAR(96) REFERENCES v2_gpu_devices(uuid) ON DELETE CASCADE,
    source VARCHAR(64) NOT NULL, -- BESZEL | NVML | NODE_AGENT | SCHEDULER | WIREGUARD
    severity VARCHAR(32) NOT NULL, -- INFO | WARNING | CRITICAL
    title VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    state VARCHAR(32) NOT NULL DEFAULT 'OPEN', -- OPEN | ACKNOWLEDGED | RESOLVED
    triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

-- 14. Bảng Sự kiện Bảo trì & Lịch Lab (Maintenance Events)
CREATE TABLE IF NOT EXISTS v2_maintenance_events (
    id VARCHAR(64) PRIMARY KEY,
    host_id VARCHAR(64) REFERENCES v2_hosts(id) ON DELETE CASCADE,
    title VARCHAR(255) NOT NULL,
    event_type VARCHAR(64) NOT NULL, -- MAINTENANCE | GPU_RESERVATION | DEADLINE | INCIDENT
    start_time TIMESTAMPTZ NOT NULL,
    end_time TIMESTAMPTZ NOT NULL,
    created_by VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'SCHEDULED',
    notes TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_v2_gpu_host ON v2_gpu_devices(host_id);
CREATE INDEX IF NOT EXISTS idx_v2_reservations_gpu_state ON v2_reservations(gpu_uuid, observed_state);
CREATE INDEX IF NOT EXISTS idx_v2_jobs_member ON v2_jobs(member_id, observed_state);
CREATE INDEX IF NOT EXISTS idx_v2_scans_cache ON v2_security_scans(cache_key);
CREATE INDEX IF NOT EXISTS idx_v2_audit_created ON v2_audit_events(created_at DESC);

COMMIT;

