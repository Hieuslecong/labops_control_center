-- LabOps Control Center - Member Provisioning, Private Storage & Secure SSH Schema
-- Migration: 002_member_provisioning_storage_ssh.sql

-- 1. Storage Pools Inventory
CREATE TABLE IF NOT EXISTS storage_pools (
    id VARCHAR(64) PRIMARY KEY, -- 'track-nvme-pool', 'ws-ssd-pool'
    server_id VARCHAR(32) NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    pool_name VARCHAR(64) NOT NULL,
    label VARCHAR(128) NOT NULL,
    driver VARCHAR(32) NOT NULL DEFAULT 'zfs', -- 'zfs', 'btrfs', 'dir'
    mount_point VARCHAR(256) NOT NULL,
    total_capacity_gb NUMERIC(10, 2) NOT NULL,
    used_capacity_gb NUMERIC(10, 2) NOT NULL DEFAULT 0.0,
    free_capacity_gb NUMERIC(10, 2) NOT NULL,
    disk_identifier VARCHAR(128) NOT NULL, -- Stable WWN or UUID (e.g. wwn-0x5002538f411b4101)
    health_status VARCHAR(32) NOT NULL DEFAULT 'ONLINE', -- 'ONLINE', 'DEGRADED', 'FAULTED'
    is_default BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Seed initial storage pools for Track and Workstation
INSERT INTO storage_pools (id, server_id, pool_name, label, driver, mount_point, total_capacity_gb, used_capacity_gb, free_capacity_gb, disk_identifier, health_status, is_default) VALUES
('track-nvme-pool', 'track', 'labops-nvme-pool', 'SSD NVMe Hiệu năng cao (ZFS RAID-Z2)', 'zfs', '/var/lib/incus/storage-pools/labops-nvme-pool', 8000.0, 4320.0, 3680.0, 'wwn-0x5002538f411b4101 (Samsung PM9A3 NVMe)', 'ONLINE', TRUE),
('track-bulk-pool', 'track', 'labops-bulk-pool', 'HDD Lưu trữ dữ liệu lớn (ZFS Mirror)', 'zfs', '/var/lib/incus/storage-pools/labops-bulk-pool', 16000.0, 7200.0, 8800.0, 'wwn-0x5000cca28f01b102 (Seagate Exos Enterprise)', 'ONLINE', FALSE),
('ws-ssd-pool', 'workstation', 'labops-ws-pool', 'SSD Enterprise NVMe (ZFS Mirror)', 'zfs', '/var/lib/incus/storage-pools/labops-ws-pool', 4000.0, 1520.0, 2480.0, 'wwn-0x5001b448bb920042 (Micron 7450 PRO NVMe)', 'ONLINE', TRUE)
ON CONFLICT (id) DO NOTHING;

-- 2. Storage Allocations per Workspace
CREATE TABLE IF NOT EXISTS storage_allocations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    storage_pool_id VARCHAR(64) NOT NULL REFERENCES storage_pools(id),
    volume_name VARCHAR(64) NOT NULL UNIQUE, -- 'data-<username>'
    mount_path VARCHAR(256) NOT NULL DEFAULT '/workspace',
    quota_bytes BIGINT NOT NULL, -- Storage quota in bytes
    used_bytes BIGINT NOT NULL DEFAULT 0,
    read_only_datasets JSONB DEFAULT '[]', -- JSON array of shared read-only dataset paths
    backup_policy VARCHAR(64) NOT NULL DEFAULT 'DAILY_SNAPSHOT',
    state VARCHAR(32) NOT NULL DEFAULT 'ATTACHED', -- 'ATTACHED', 'DETACHED', 'ARCHIVED'
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 3. Dedicated SSH Endpoints
CREATE TABLE IF NOT EXISTS ssh_endpoints (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    host_id VARCHAR(32) NOT NULL REFERENCES servers(id),
    listen_address INET NOT NULL, -- WireGuard VPN address (e.g. 10.200.0.1)
    listen_port INTEGER NOT NULL CHECK (listen_port BETWEEN 22000 AND 29999),
    target_port INTEGER NOT NULL DEFAULT 22,
    protocol VARCHAR(16) NOT NULL DEFAULT 'tcp',
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE', -- 'RESERVED', 'ACTIVE', 'INACTIVE'
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_host_listen_port UNIQUE (host_id, listen_address, protocol, listen_port)
);

-- 4. SSH Credentials & Key Lifecycle
CREATE TABLE IF NOT EXISTS ssh_credentials (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    public_key TEXT,
    fingerprint VARCHAR(128),
    algorithm VARCHAR(32) NOT NULL DEFAULT 'ed25519',
    enrollment_type VARCHAR(32) NOT NULL DEFAULT 'DEVICE_GENERATED', -- 'DEVICE_GENERATED', 'IMPORTED', 'PENDING'
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE', -- 'PENDING_KEY_ENROLLMENT', 'ACTIVE', 'ROTATING', 'REVOKED'
    allowed_cidrs VARCHAR(128) NOT NULL DEFAULT '10.200.0.0/16', -- WireGuard subnet only
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    revoked_at TIMESTAMP WITH TIME ZONE
);

-- 5. Durable Provisioning Operations (State Machine)
CREATE TABLE IF NOT EXISTS provisioning_operations (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    operation_type VARCHAR(64) NOT NULL DEFAULT 'PROVISION_WORKSPACE',
    idempotency_key VARCHAR(128) NOT NULL UNIQUE,
    status VARCHAR(32) NOT NULL DEFAULT 'COMPLETED', -- 'PENDING', 'RUNNING', 'COMPLETED', 'FAILED', 'ROLLED_BACK'
    current_step VARCHAR(128) NOT NULL,
    current_step_index INTEGER NOT NULL DEFAULT 1,
    total_steps INTEGER NOT NULL DEFAULT 11,
    steps_log JSONB DEFAULT '[]',
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_prov_op_user ON provisioning_operations(user_id);
CREATE INDEX IF NOT EXISTS idx_prov_op_idemp ON provisioning_operations(idempotency_key);

