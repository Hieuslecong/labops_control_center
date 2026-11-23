"""Add LabOps MRAM v1.0 tables (web credentials, member profiles, supervisor assignments, SSH requests, recommendations, approvals, invitations, capacity reservations, provisioning operations, credential claims, policy acceptances).

Revision ID: 0004_labops_mram_v1
Revises: 0003_labops_ai_sre_agent_v2
Create Date: 2026-11-23 10:30:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004_labops_mram_v1"
down_revision = "0003_labops_ai_sre_agent_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    if "member_web_credentials" not in tables:
        op.create_table(
            "member_web_credentials",
            sa.Column("member_id", sa.String(length=64), primary_key=True),
            sa.Column("username", sa.String(length=64), nullable=False, unique=True),
            sa.Column("password_hash", sa.String(length=512), nullable=False),
            sa.Column("hash_algorithm", sa.String(length=32), nullable=False, server_default="scrypt"),
            sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_member_web_credentials_username", "member_web_credentials", ["username"])

    if "member_profiles" not in tables:
        op.create_table(
            "member_profiles",
            sa.Column("member_id", sa.String(length=64), primary_key=True),
            sa.Column("username", sa.String(length=64), nullable=False),
            sa.Column("full_name", sa.String(length=160), nullable=False, server_default=""),
            sa.Column("email", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("member_code", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("member_category", sa.String(length=32), nullable=False, server_default="STUDENT"),
            sa.Column("department_class", sa.String(length=160), nullable=False, server_default=""),
            sa.Column("primary_supervisor_id", sa.String(length=64), nullable=True),
            sa.Column("direct_manager_id", sa.String(length=64), nullable=True),
            sa.Column("web_account_status", sa.String(length=32), nullable=False, server_default="WEB_ACTIVE"),
            sa.Column("ssh_access_state", sa.String(length=48), nullable=False, server_default="NONE"),
            sa.Column("ssh_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("verified_by", sa.String(length=128), nullable=True),
            sa.Column("created_by", sa.String(length=128), nullable=False, server_default="admin"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_member_profiles_username", "member_profiles", ["username"])
        op.create_index("ix_member_profiles_member_code", "member_profiles", ["member_code"])
        op.create_index("ix_member_profiles_category", "member_profiles", ["member_category"])
        op.create_index("ix_member_profiles_primary_sup", "member_profiles", ["primary_supervisor_id"])
        op.create_index("ix_member_profiles_ssh_state", "member_profiles", ["ssh_access_state"])

    if "supervisor_assignments" not in tables:
        op.create_table(
            "supervisor_assignments",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("student_member_id", sa.String(length=64), nullable=False),
            sa.Column("supervisor_member_id", sa.String(length=64), nullable=False),
            sa.Column("direct_manager_id", sa.String(length=64), nullable=True),
            sa.Column("assignment_role", sa.String(length=32), nullable=False, server_default="PRIMARY"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("assigned_by", sa.String(length=128), nullable=False, server_default="admin"),
            sa.Column("change_reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
            sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_sup_assign_student_active", "supervisor_assignments", ["student_member_id", "is_active"])
        op.create_index("ix_sup_assign_sup_active", "supervisor_assignments", ["supervisor_member_id", "is_active"])

    if "server_access_requests" not in tables:
        op.create_table(
            "server_access_requests",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("request_type", sa.String(length=32), nullable=False, server_default="INITIAL"),
            sa.Column("member_id", sa.String(length=64), nullable=False),
            sa.Column("username", sa.String(length=64), nullable=False),
            sa.Column("member_category", sa.String(length=32), nullable=False, server_default="STUDENT"),
            sa.Column("primary_supervisor_id", sa.String(length=64), nullable=True),
            sa.Column("direct_manager_id", sa.String(length=64), nullable=True),
            sa.Column("project_title", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("registration_reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("ssh_purpose", sa.Text(), nullable=False, server_default=""),
            sa.Column("expected_tasks", sa.Text(), nullable=False, server_default=""),
            sa.Column("requested_duration_days", sa.Integer(), nullable=True),
            sa.Column("gpu_requested", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("gpu_justification", sa.Text(), nullable=False, server_default=""),
            sa.Column("policy_version", sa.String(length=32), nullable=False, server_default="mram-v1.0"),
            sa.Column("status", sa.String(length=48), nullable=False, server_default="PENDING_APPROVAL"),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("review_expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("decided_by", sa.String(length=128), nullable=True),
            sa.Column("decision_reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("parent_request_id", sa.String(length=64), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_srv_req_member_status", "server_access_requests", ["member_id", "status"])
        op.create_index("ix_srv_req_status_exp", "server_access_requests", ["status", "review_expires_at"])

    if "request_resource_recommendations" not in tables:
        op.create_table(
            "request_resource_recommendations",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("request_id", sa.String(length=64), nullable=False),
            sa.Column("request_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("recommended_host_id", sa.String(length=64), nullable=True),
            sa.Column("recommended_pool_id", sa.String(length=128), nullable=True),
            sa.Column("recommended_filesystem_uuid", sa.String(length=128), nullable=True),
            sa.Column("recommended_mount_point", sa.String(length=255), nullable=True),
            sa.Column("cpu_cores", sa.Integer(), nullable=False, server_default="4"),
            sa.Column("ram_gb", sa.Integer(), nullable=False, server_default="16"),
            sa.Column("storage_quota_gb", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("gpu_mode", sa.String(length=32), nullable=False, server_default="NONE"),
            sa.Column("gpu_uuid", sa.String(length=128), nullable=True),
            sa.Column("duration_days", sa.Integer(), nullable=True),
            sa.Column("manual_review_required", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("rationale_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_req_rec_request_id", "request_resource_recommendations", ["request_id"])

    if "request_approvals" not in tables:
        op.create_table(
            "request_approvals",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("request_id", sa.String(length=64), nullable=False),
            sa.Column("request_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("decision", sa.String(length=32), nullable=False),
            sa.Column("approver_username", sa.String(length=128), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("approved_config_json", sa.JSON(), nullable=False),
            sa.Column("config_hash", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("policy_version", sa.String(length=32), nullable=False, server_default="mram-v1.0"),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_req_approvals_request_id", "request_approvals", ["request_id"])

    if "approval_invitations" not in tables:
        op.create_table(
            "approval_invitations",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("request_id", sa.String(length=64), nullable=False),
            sa.Column("request_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("admin_username", sa.String(length=64), nullable=False, server_default="admin"),
            sa.Column("admin_zalo_id", sa.String(length=128), nullable=False, server_default="0c00ed813eccd7928edd"),
            sa.Column("token_hash", sa.String(length=128), nullable=False, unique=True),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("issued_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_app_inv_request_id", "approval_invitations", ["request_id"])
        op.create_index("ix_app_inv_token_hash", "approval_invitations", ["token_hash"])

    if "capacity_reservations" not in tables:
        op.create_table(
            "capacity_reservations",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("request_id", sa.String(length=64), nullable=False),
            sa.Column("approval_id", sa.String(length=64), nullable=False),
            sa.Column("member_id", sa.String(length=64), nullable=False),
            sa.Column("host_id", sa.String(length=64), nullable=False),
            sa.Column("pool_id", sa.String(length=128), nullable=False),
            sa.Column("filesystem_uuid", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("mount_point", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("storage_quota_gb", sa.Integer(), nullable=False, server_default="100"),
            sa.Column("cpu_cores", sa.Integer(), nullable=False, server_default="4"),
            sa.Column("ram_gb", sa.Integer(), nullable=False, server_default="16"),
            sa.Column("gpu_uuid", sa.String(length=128), nullable=True),
            sa.Column("gpu_mode", sa.String(length=32), nullable=False, server_default="NONE"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="RESERVED"),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_cap_res_host_pool_status", "capacity_reservations", ["host_id", "pool_id", "status"])

    if "provisioning_operations" not in tables:
        op.create_table(
            "provisioning_operations",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("idempotency_key", sa.String(length=128), nullable=False, unique=True),
            sa.Column("request_id", sa.String(length=64), nullable=False),
            sa.Column("approval_id", sa.String(length=64), nullable=False),
            sa.Column("approval_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("member_id", sa.String(length=64), nullable=False),
            sa.Column("config_hash", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=48), nullable=False, server_default="QUEUED"),
            sa.Column("steps_json", sa.JSON(), nullable=False),
            sa.Column("verification_json", sa.JSON(), nullable=False),
            sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("correlation_id", sa.String(length=96), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_prov_ops_request_id", "provisioning_operations", ["request_id"])

    if "credential_claims" not in tables:
        op.create_table(
            "credential_claims",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("member_id", sa.String(length=64), nullable=False),
            sa.Column("request_id", sa.String(length=64), nullable=True),
            sa.Column("provisioning_op_id", sa.String(length=64), nullable=True),
            sa.Column("claim_type", sa.String(length=48), nullable=False, server_default="SSH_TEMP_PASSWORD"),
            sa.Column("claimed_by", sa.String(length=64), nullable=False),
            sa.Column("step_up_jti", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="CLAIMED"),
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_cred_claims_member_id", "credential_claims", ["member_id"])

    if "policy_acceptances" not in tables:
        op.create_table(
            "policy_acceptances",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("member_id", sa.String(length=64), nullable=False),
            sa.Column("request_id", sa.String(length=64), nullable=False),
            sa.Column("policy_version", sa.String(length=32), nullable=False, server_default="mram-v1.0"),
            sa.Column("policy_hash", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("client_ip", sa.String(length=64), nullable=False, server_default=""),
        )
        op.create_index("ix_policy_acc_member_id", "policy_acceptances", ["member_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    for tbl in (
        "policy_acceptances",
        "credential_claims",
        "provisioning_operations",
        "capacity_reservations",
        "approval_invitations",
        "request_approvals",
        "request_resource_recommendations",
        "server_access_requests",
        "supervisor_assignments",
        "member_profiles",
        "member_web_credentials",
    ):
        if tbl in tables:
            op.drop_table(tbl)
