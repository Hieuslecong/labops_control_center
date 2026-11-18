"""LabOps V2 hardened persistence schema.

Revision ID: 0001_labops_v2_hardened
Revises: None
Create Date: 2026-11-18 00:00:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001_labops_v2_hardened"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "hosts",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("hostname", sa.String(length=128), nullable=False, unique=True),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False, server_default="COMPUTE_NODE"),
        sa.Column("management_ip", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("vpn_ip", sa.String(length=64), nullable=True),
        sa.Column("agent_url", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="NOT_CONFIGURED"),
        sa.Column("freshness_status", sa.String(length=32), nullable=False, server_default="OFFLINE"),
        sa.Column("last_heartbeat_at", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "members",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("username", sa.String(length=64), nullable=False, unique=True),
        sa.Column("email", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("full_name", sa.String(length=160), nullable=False, server_default=""),
        sa.Column("role", sa.String(length=32), nullable=False, server_default="RESEARCHER"),
        sa.Column("status", sa.String(length=48), nullable=False, server_default="ACTIVE"),
        sa.Column("provisioning_state", sa.String(length=64), nullable=False, server_default="PENDING"),
        sa.Column("assigned_server", sa.String(length=64), nullable=False, server_default="workstation"),
        sa.Column("workspace_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("workspace_dir", sa.String(length=255), nullable=True),
        sa.Column("ssh_port", sa.Integer(), nullable=True),
        sa.Column("storage_quota_gb", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("cpu_limit_cores", sa.Integer(), nullable=False, server_default="8"),
        sa.Column("ram_limit_gb", sa.Integer(), nullable=False, server_default="16"),
        sa.Column("vram_quota_gb", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("gpu_permissions_json", sa.JSON(), nullable=False),
        sa.Column("archived_at", sa.String(length=64), nullable=True),
        sa.Column("archive_manifest_json", sa.JSON(), nullable=True),
        sa.Column("deleted_at", sa.String(length=64), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_members_username", "members", ["username"], unique=True)
    op.create_index("ix_members_status", "members", ["status"])

    op.create_table(
        "workspaces",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False, unique=True),
        sa.Column("owner", sa.String(length=64), nullable=False),
        sa.Column("host_id", sa.String(length=64), nullable=False),
        sa.Column("container_id", sa.String(length=128), nullable=True),
        sa.Column("runtime_type", sa.String(length=32), nullable=False, server_default="DOCKER"),
        sa.Column("image_ref", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("ssh_host_port", sa.Integer(), nullable=True),
        sa.Column("lifecycle_state", sa.String(length=48), nullable=False, server_default="PENDING_RETRY"),
        sa.Column("observed_state", sa.String(length=48), nullable=False, server_default="NOT_VERIFIED"),
        sa.Column("non_privileged", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("docker_socket_mounted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("limits_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_workspaces_owner", "workspaces", ["owner"])

    op.create_table(
        "gpu_reservations",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("gpu_uuid", sa.String(length=128), nullable=False),
        sa.Column("gpu_id", sa.String(length=64), nullable=True),
        sa.Column("host_id", sa.String(length=64), nullable=True),
        sa.Column("owner", sa.String(length=64), nullable=False),
        sa.Column("requested_by", sa.String(length=64), nullable=False),
        sa.Column("job_name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("mode", sa.String(length=64), nullable=False, server_default="FULL_GPU_EXCLUSIVE"),
        sa.Column("requested_vram_gb", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("runtime_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("reserved_at", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.String(length=64), nullable=True),
        sa.Column("ended_at", sa.String(length=64), nullable=True),
    )
    op.create_index("ix_gpu_reservations_gpu_uuid", "gpu_reservations", ["gpu_uuid"])
    op.create_index("ix_gpu_reservations_owner", "gpu_reservations", ["owner"])
    op.create_index("ix_gpu_reservations_state", "gpu_reservations", ["state"])

    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("timestamp", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=96), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.String(length=255), nullable=False),
        sa.Column("result", sa.String(length=48), nullable=False),
        sa.Column("correlation_id", sa.String(length=96), nullable=False, server_default="corr-legacy"),
        sa.Column("details", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index("ix_audit_events_timestamp", "audit_events", ["timestamp"])
    op.create_index("ix_audit_events_actor", "audit_events", ["actor"])
    op.create_index("ix_audit_events_action", "audit_events", ["action"])

    op.create_table(
        "idempotency_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("endpoint", sa.String(length=160), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("request_hash", sa.String(length=128), nullable=False),
        sa.Column("status_code", sa.Integer(), nullable=False, server_default="200"),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("actor", "endpoint", "idempotency_key", name="uq_idem_actor_endpoint_key"),
    )
    op.create_index("ix_idempotency_records_expires_at", "idempotency_records", ["expires_at"])

    op.create_table(
        "revoked_tokens",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("jti", sa.String(length=128), nullable=True, unique=True),
        sa.Column("username", sa.String(length=128), nullable=True),
        sa.Column("revoked_before_epoch", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=128), nullable=False, server_default="revoked"),
        sa.Column("expires_at", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_revoked_tokens_username", "revoked_tokens", ["username"])

    op.create_table(
        "deletion_operations",
        sa.Column("op_id", sa.String(length=64), primary_key=True),
        sa.Column("member_id", sa.String(length=64), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.String(length=48), nullable=False),
        sa.Column("precheck_json", sa.JSON(), nullable=False),
        sa.Column("steps_json", sa.JSON(), nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_deletion_operations_member_id", "deletion_operations", ["member_id"])
    op.create_index("ix_deletion_operations_status", "deletion_operations", ["status"])

    op.create_table(
        "zalo_outbox",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("month_key", sa.String(length=16), nullable=False),
        sa.Column("recipient_id", sa.String(length=128), nullable=False),
        sa.Column("is_critical", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="RESERVED"),
        sa.Column("message_snippet", sa.String(length=255), nullable=False, server_default=""),
        sa.Column("idempotency_scope", sa.String(length=255), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_zalo_outbox_month_status", "zalo_outbox", ["month_key", "status"])


def downgrade() -> None:
    op.drop_index("ix_zalo_outbox_month_status", table_name="zalo_outbox")
    op.drop_table("zalo_outbox")
    op.drop_index("ix_deletion_operations_status", table_name="deletion_operations")
    op.drop_index("ix_deletion_operations_member_id", table_name="deletion_operations")
    op.drop_table("deletion_operations")
    op.drop_index("ix_revoked_tokens_username", table_name="revoked_tokens")
    op.drop_table("revoked_tokens")
    op.drop_index("ix_idempotency_records_expires_at", table_name="idempotency_records")
    op.drop_table("idempotency_records")
    op.drop_index("ix_audit_events_action", table_name="audit_events")
    op.drop_index("ix_audit_events_actor", table_name="audit_events")
    op.drop_index("ix_audit_events_timestamp", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_gpu_reservations_state", table_name="gpu_reservations")
    op.drop_index("ix_gpu_reservations_owner", table_name="gpu_reservations")
    op.drop_index("ix_gpu_reservations_gpu_uuid", table_name="gpu_reservations")
    op.drop_table("gpu_reservations")
    op.drop_index("ix_workspaces_owner", table_name="workspaces")
    op.drop_table("workspaces")
    op.drop_index("ix_members_status", table_name="members")
    op.drop_index("ix_members_username", table_name="members")
    op.drop_table("members")
    op.drop_table("hosts")
