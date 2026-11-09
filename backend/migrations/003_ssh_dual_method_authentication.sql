-- LabOps Control Center - Mandatory SSH Public Key + Password Authentication
-- Migration: 003_ssh_dual_method_authentication.sql

-- 1. SSH Account Policy (Enforces two-method authentication per workspace)
CREATE TABLE IF NOT EXISTS ssh_account_policies (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    linux_username VARCHAR(32) NOT NULL,
    authentication_mode VARCHAR(64) NOT NULL DEFAULT 'PUBLIC_KEY_AND_PASSWORD', -- Strictly 'PUBLIC_KEY_AND_PASSWORD'
    password_status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE', -- 'ACTIVE', 'CHANGE_REQUIRED', 'LOCKED', 'EXPIRED'
    key_enrollment_status VARCHAR(32) NOT NULL DEFAULT 'ENROLLED', -- 'ENROLLED', 'PENDING', 'REVOKED'
    access_status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE', -- 'ACTIVE', 'SUSPENDED', 'LOCKED'
    min_password_length INTEGER NOT NULL DEFAULT 12,
    failed_login_attempts INTEGER NOT NULL DEFAULT 0,
    max_failed_attempts INTEGER NOT NULL DEFAULT 5,
    lockout_until TIMESTAMP WITH TIME ZONE,
    last_failed_login TIMESTAMP WITH TIME ZONE,
    last_successful_login TIMESTAMP WITH TIME ZONE,
    last_login_source_ip INET,
    sshd_effective_config JSONB DEFAULT '{
        "pubkey_authentication": true,
        "password_authentication": true,
        "kbd_interactive_authentication": false,
        "authentication_methods": "publickey,password",
        "permit_root_login": false,
        "permit_empty_passwords": false,
        "use_pam": true
    }',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_workspace_policy UNIQUE (workspace_id)
);

CREATE INDEX IF NOT EXISTS idx_ssh_policy_user ON ssh_account_policies(linux_username);
CREATE INDEX IF NOT EXISTS idx_ssh_policy_status ON ssh_account_policies(access_status);

-- 2. SSH Login Audit Trail (Tracks both authentication stages)
CREATE TABLE IF NOT EXISTS ssh_login_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    member_id VARCHAR(64) NOT NULL,
    workspace_id VARCHAR(64) NOT NULL,
    linux_username VARCHAR(32) NOT NULL,
    source_ip INET NOT NULL,
    listen_port INTEGER NOT NULL,
    auth_stage VARCHAR(32) NOT NULL, -- 'STAGE_1_PUBLICKEY', 'STAGE_2_PASSWORD', 'COMPLETED'
    authentication_result VARCHAR(32) NOT NULL, -- 'SUCCESS', 'PUBLICKEY_FAILED', 'PASSWORD_FAILED', 'BOTH_FAILED'
    failure_reason VARCHAR(256),
    correlation_id VARCHAR(64),
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ssh_login_member ON ssh_login_events(member_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_ssh_login_workspace ON ssh_login_events(workspace_id);
CREATE INDEX IF NOT EXISTS idx_ssh_login_result ON ssh_login_events(authentication_result);

-- 3. Update existing SSH credentials with revocation support
ALTER TABLE ssh_credentials 
ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMP WITH TIME ZONE,
ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE';

