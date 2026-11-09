-- LabOps Control Center - Initial PostgreSQL Schema
-- Migration: 001_initial_schema.sql
-- Designed for Ubuntu / Incus / NVIDIA A30 AI Research Infrastructure

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 1. Roles
CREATE TABLE IF NOT EXISTS roles (
    id VARCHAR(32) PRIMARY KEY,
    name VARCHAR(64) NOT NULL,
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO roles (id, name, description) VALUES
('SUPER_ADMIN', 'Quản trị viên Cấp cao', 'Toàn quyền kiểm soát hạ tầng, cấp phát GPU và duyệt an ninh'),
('LAB_MANAGER', 'Quản lý Phòng Lab', 'Quản lý thành viên, hạn ngạch tài nguyên và giám sát hoạt động'),
('RESEARCHER', 'Nghiên cứu viên', 'Quyền sử dụng workspace được cấp, chạy tiến trình tính toán'),
('GUEST', 'Khách mời', 'Quyền xem và chạy CPU cơ bản, đợi duyệt hạn ngạch')
ON CONFLICT (id) DO NOTHING;

-- 2. Physical Servers
CREATE TABLE IF NOT EXISTS servers (
    id VARCHAR(32) PRIMARY KEY, -- 'track', 'workstation'
    name VARCHAR(128) NOT NULL,
    hostname VARCHAR(128) NOT NULL UNIQUE,
    location VARCHAR(128) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'online', -- 'online', 'offline', 'degraded'
    ip_address INET NOT NULL,
    vpn_ip INET NOT NULL,
    uptime VARCHAR(64) NOT NULL,
    cpu_model VARCHAR(128) NOT NULL,
    cpu_cores INTEGER NOT NULL,
    ram_total_gb NUMERIC(10, 2) NOT NULL,
    disk_total_tb NUMERIC(10, 2) NOT NULL,
    disk_type VARCHAR(64) NOT NULL,
    gpu_count INTEGER NOT NULL DEFAULT 0,
    incus_version VARCHAR(32) NOT NULL,
    driver_version VARCHAR(32) NOT NULL,
    cuda_version VARCHAR(32) NOT NULL,
    last_heartbeat TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 3. GPU Devices
CREATE TABLE IF NOT EXISTS gpu_devices (
    id VARCHAR(64) PRIMARY KEY, -- 'track-gpu-01', 'ws-gpu-01'
    display_label VARCHAR(64) NOT NULL UNIQUE,
    server_id VARCHAR(32) NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
    model VARCHAR(128) NOT NULL DEFAULT 'NVIDIA A30 24GB',
    pci_bus_id VARCHAR(32) NOT NULL,
    uuid VARCHAR(128) NOT NULL UNIQUE,
    vram_total_mb INTEGER NOT NULL DEFAULT 24576,
    power_limit_watts INTEGER NOT NULL DEFAULT 165,
    allocation_mode VARCHAR(32) NOT NULL DEFAULT 'SHARED', -- 'SHARED', 'EXCLUSIVE', 'MIG'
    enforcement_state VARCHAR(64) NOT NULL DEFAULT 'DRIVER_ENFORCED_VERIFIED',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 4. Users / Members
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    username VARCHAR(64) NOT NULL UNIQUE,
    full_name VARCHAR(128) NOT NULL,
    email VARCHAR(128) NOT NULL UNIQUE,
    role_id VARCHAR(32) NOT NULL REFERENCES roles(id),
    assigned_server VARCHAR(32) NOT NULL REFERENCES servers(id),
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE', -- 'ACTIVE', 'SUSPENDED', 'ARCHIVED'
    ssh_status VARCHAR(32) NOT NULL DEFAULT 'OFFLINE',
    cpu_limit_cores INTEGER NOT NULL DEFAULT 8,
    ram_limit_gb INTEGER NOT NULL DEFAULT 32,
    storage_quota_gb INTEGER NOT NULL DEFAULT 200,
    vram_policy VARCHAR(64) NOT NULL DEFAULT 'Quota 12GB (Shared)',
    max_runtime_hours INTEGER NOT NULL DEFAULT 48,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 5. Workspaces (Incus Containers)
CREATE TABLE IF NOT EXISTS workspaces (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    server_id VARCHAR(32) NOT NULL REFERENCES servers(id),
    container_name VARCHAR(64) NOT NULL UNIQUE,
    image_template VARCHAR(128) NOT NULL,
    ip_address INET,
    state VARCHAR(32) NOT NULL DEFAULT 'RUNNING', -- 'RUNNING', 'STOPPED', 'FROZEN'
    zfs_dataset VARCHAR(128) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 6. SSH Keys & Access Records
CREATE TABLE IF NOT EXISTS ssh_keys (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    public_key TEXT NOT NULL,
    fingerprint VARCHAR(128) NOT NULL,
    key_type VARCHAR(32) NOT NULL DEFAULT 'ed25519',
    allowed_cidrs VARCHAR(128) DEFAULT '10.200.0.0/16',
    is_revoked BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ssh_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL REFERENCES users(id),
    server_id VARCHAR(32) NOT NULL REFERENCES servers(id),
    client_ip INET NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP WITH TIME ZONE,
    is_active BOOLEAN DEFAULT TRUE
);

-- 7. Jobs & Process History
CREATE TABLE IF NOT EXISTS jobs (
    id VARCHAR(64) PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id),
    workspace_id UUID REFERENCES workspaces(id),
    server_id VARCHAR(32) NOT NULL REFERENCES servers(id),
    gpu_id VARCHAR(64) REFERENCES gpu_devices(id),
    pid INTEGER NOT NULL,
    script TEXT NOT NULL,
    vram_allocated_mb INTEGER DEFAULT 0,
    cpu_limit_percent NUMERIC(5, 2) DEFAULT 100.0,
    status VARCHAR(32) NOT NULL DEFAULT 'RUNNING', -- 'RUNNING', 'PENDING', 'COMPLETED', 'FAILED', 'CANCELLED'
    started_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    ended_at TIMESTAMP WITH TIME ZONE,
    exit_code INTEGER
);

-- 8. High-Frequency Sampling (Timeseries with retention strategy)
CREATE TABLE IF NOT EXISTS gpu_usage_samples (
    id BIGSERIAL PRIMARY KEY,
    gpu_id VARCHAR(64) NOT NULL REFERENCES gpu_devices(id),
    vram_used_mb INTEGER NOT NULL,
    util_percent INTEGER NOT NULL,
    temp_celsius INTEGER NOT NULL,
    power_watts INTEGER NOT NULL,
    recorded_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_gpu_samples_time ON gpu_usage_samples (gpu_id, recorded_at DESC);

-- 9. Security Reviews (Quarantine & Sandbox scans)
CREATE TABLE IF NOT EXISTS repository_reviews (
    id VARCHAR(64) PRIMARY KEY,
    repo_url TEXT NOT NULL,
    repo_name VARCHAR(128) NOT NULL,
    commit_sha VARCHAR(64) NOT NULL,
    submitted_by UUID REFERENCES users(id),
    status VARCHAR(32) NOT NULL DEFAULT 'SCANNING',
    risk_score INTEGER DEFAULT 0,
    findings_json JSONB DEFAULT '{}',
    summary TEXT,
    reviewed_by VARCHAR(128),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 10. Lab Calendar Events
CREATE TABLE IF NOT EXISTS lab_events (
    id VARCHAR(64) PRIMARY KEY,
    title VARCHAR(256) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    event_date DATE NOT NULL,
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    location VARCHAR(256) NOT NULL,
    organizer VARCHAR(128) NOT NULL,
    related_server VARCHAR(32),
    status VARCHAR(32) NOT NULL DEFAULT 'SCHEDULED',
    description TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 11. Administrative Audit Logs (Append-only & Tamper-resistant)
CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    actor VARCHAR(128) NOT NULL,
    action VARCHAR(128) NOT NULL,
    target_type VARCHAR(64) NOT NULL,
    target_id VARCHAR(128) NOT NULL,
    result VARCHAR(32) NOT NULL DEFAULT 'SUCCESS',
    correlation_id VARCHAR(64) NOT NULL,
    details TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_audit_time ON audit_logs (created_at DESC);

