"""Add LabOps AI SRE Agent v2 tables (identity links, outbox, incidents, telemetry history, reports, leases, tool audit).

Revision ID: 0003_labops_ai_sre_agent_v2
Revises: 0002_member_username_reuse
Create Date: 2026-11-23 00:25:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003_labops_ai_sre_agent_v2"
down_revision = "0002_member_username_reuse"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    if "zalo_identity_links" not in tables:
        op.create_table(
            "zalo_identity_links",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("zalo_user_id", sa.String(length=128), nullable=True),
            sa.Column("member_id", sa.String(length=64), nullable=False),
            sa.Column("username", sa.String(length=64), nullable=False),
            sa.Column("role", sa.String(length=32), nullable=False, server_default="RESEARCHER"),
            sa.Column("display_name", sa.String(length=160), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING_CODE"),
            sa.Column("link_code_hash", sa.String(length=128), nullable=True),
            sa.Column("code_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("linked_by", sa.String(length=128), nullable=False, server_default="admin"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_zalo_identity_links_zalo_user_id", "zalo_identity_links", ["zalo_user_id"])
        op.create_index("ix_zalo_identity_links_member_id", "zalo_identity_links", ["member_id"])
        op.create_index("ix_zalo_identity_links_username", "zalo_identity_links", ["username"])
        op.create_index("ix_zalo_identity_links_status", "zalo_identity_links", ["status"])
        op.create_index("ix_zalo_identity_links_link_code_hash", "zalo_identity_links", ["link_code_hash"])

    if "notification_subscriptions" not in tables:
        op.create_table(
            "notification_subscriptions",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("recipient_zalo_id", sa.String(length=128), nullable=False, unique=True),
            sa.Column("member_id", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("username", sa.String(length=64), nullable=False, server_default=""),
            sa.Column("role", sa.String(length=32), nullable=False, server_default="RESEARCHER"),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("receive_p0", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("receive_p1", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("receive_daily_report", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("receive_weekly_report", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("receive_repo_alerts", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("quiet_hours_start", sa.String(length=8), nullable=True),
            sa.Column("quiet_hours_end", sa.String(length=8), nullable=True),
            sa.Column("bypass_quiet_for_p0", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_notification_subscriptions_member_id", "notification_subscriptions", ["member_id"])

    if "notification_outbox" not in tables:
        op.create_table(
            "notification_outbox",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("idempotency_key", sa.String(length=200), nullable=False),
            sa.Column("recipient_id", sa.String(length=128), nullable=False),
            sa.Column("channel", sa.String(length=32), nullable=False, server_default="zalo"),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("severity", sa.String(length=16), nullable=False, server_default="P1"),
            sa.Column("incident_id", sa.String(length=64), nullable=True),
            sa.Column("report_id", sa.String(length=64), nullable=True),
            sa.Column("is_critical", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("payload_hash", sa.String(length=128), nullable=False),
            sa.Column("message_text", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("provider_message_id", sa.String(length=128), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("correlation_id", sa.String(length=96), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("idempotency_key", "recipient_id", name="uq_notif_outbox_idem_recipient"),
        )
        op.create_index("ix_notification_outbox_recipient_id", "notification_outbox", ["recipient_id"])
        op.create_index("ix_notification_outbox_event_type", "notification_outbox", ["event_type"])
        op.create_index("ix_notification_outbox_incident_id", "notification_outbox", ["incident_id"])
        op.create_index("ix_notification_outbox_report_id", "notification_outbox", ["report_id"])
        op.create_index("ix_notification_outbox_status", "notification_outbox", ["status"])
        op.create_index("ix_notif_outbox_status_next_retry", "notification_outbox", ["status", "next_retry_at"])

    if "notification_delivery_attempts" not in tables:
        op.create_table(
            "notification_delivery_attempts",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("outbox_id", sa.String(length=64), nullable=False),
            sa.Column("recipient_id", sa.String(length=128), nullable=False),
            sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("provider_message_id", sa.String(length=128), nullable=True),
            sa.Column("error_detail", sa.Text(), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_notification_delivery_attempts_outbox_id", "notification_delivery_attempts", ["outbox_id"])
        op.create_index("ix_notification_delivery_attempts_recipient_id", "notification_delivery_attempts", ["recipient_id"])
        op.create_index("ix_notification_delivery_attempts_attempted_at", "notification_delivery_attempts", ["attempted_at"])

    if "incidents" not in tables:
        op.create_table(
            "incidents",
            sa.Column("incident_id", sa.String(length=64), primary_key=True),
            sa.Column("event_type", sa.String(length=96), nullable=False),
            sa.Column("severity", sa.String(length=16), nullable=False, server_default="P1"),
            sa.Column("confidence", sa.String(length=24), nullable=False, server_default="CONFIRMED"),
            sa.Column("source", sa.String(length=64), nullable=False),
            sa.Column("resource_id", sa.String(length=255), nullable=False),
            sa.Column("host_id", sa.String(length=64), nullable=True),
            sa.Column("owner_id", sa.String(length=64), nullable=True),
            sa.Column("fingerprint", sa.String(length=255), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="OPEN"),
            sa.Column("occurrence_count", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("reopen_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("evidence_json", sa.JSON(), nullable=False),
            sa.Column("recommended_action", sa.Text(), nullable=False, server_default=""),
            sa.Column("acknowledged_by", sa.String(length=128), nullable=True),
            sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("resolved_by", sa.String(length=128), nullable=True),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("correlation_id", sa.String(length=96), nullable=False, server_default=""),
            sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("last_observed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_incidents_event_type", "incidents", ["event_type"])
        op.create_index("ix_incidents_severity", "incidents", ["severity"])
        op.create_index("ix_incidents_source", "incidents", ["source"])
        op.create_index("ix_incidents_resource_id", "incidents", ["resource_id"])
        op.create_index("ix_incidents_host_id", "incidents", ["host_id"])
        op.create_index("ix_incidents_owner_id", "incidents", ["owner_id"])
        op.create_index("ix_incidents_fingerprint", "incidents", ["fingerprint"])
        op.create_index("ix_incidents_status", "incidents", ["status"])
        op.create_index("ix_incidents_detected_at", "incidents", ["detected_at"])
        op.create_index("ix_incidents_status_severity", "incidents", ["status", "severity"])
        op.create_index("ix_incidents_owner_status", "incidents", ["owner_id", "status"])

    if "incident_events" not in tables:
        op.create_table(
            "incident_events",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("incident_id", sa.String(length=64), nullable=False),
            sa.Column("event_kind", sa.String(length=48), nullable=False),
            sa.Column("actor", sa.String(length=128), nullable=False, server_default="system"),
            sa.Column("from_status", sa.String(length=32), nullable=True),
            sa.Column("to_status", sa.String(length=32), nullable=True),
            sa.Column("details_json", sa.JSON(), nullable=False),
            sa.Column("correlation_id", sa.String(length=96), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_incident_events_incident_id", "incident_events", ["incident_id"])
        op.create_index("ix_incident_events_created_at", "incident_events", ["created_at"])

    if "report_runs" not in tables:
        op.create_table(
            "report_runs",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("report_type", sa.String(length=32), nullable=False),
            sa.Column("period_key", sa.String(length=32), nullable=False),
            sa.Column("window_start_utc", sa.DateTime(timezone=True), nullable=False),
            sa.Column("window_end_utc", sa.DateTime(timezone=True), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="GENERATED"),
            sa.Column("author", sa.String(length=128), nullable=False, server_default="LabOps AI SRE v2"),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("zalo_summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("markdown_content", sa.Text(), nullable=False, server_default=""),
            sa.Column("ai_insights_json", sa.JSON(), nullable=False),
            sa.Column("delivery_state_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("report_type", "period_key", name="uq_report_runs_type_period"),
        )
        op.create_index("ix_report_runs_report_type", "report_runs", ["report_type"])
        op.create_index("ix_report_runs_period_key", "report_runs", ["period_key"])
        op.create_index("ix_report_runs_created_at", "report_runs", ["created_at"])

    if "daily_report_snapshots" not in tables:
        op.create_table(
            "daily_report_snapshots",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("report_run_id", sa.String(length=64), nullable=False),
            sa.Column("host_id", sa.String(length=64), nullable=False, server_default="cluster"),
            sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("overall_health", sa.String(length=32), nullable=False, server_default="UNKNOWN"),
            sa.Column("kpis_json", sa.JSON(), nullable=False),
            sa.Column("raw_snapshot_json", sa.JSON(), nullable=False),
        )
        op.create_index("ix_daily_report_snapshots_report_run_id", "daily_report_snapshots", ["report_run_id"])

    if "telemetry_history_samples" not in tables:
        op.create_table(
            "telemetry_history_samples",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("host_id", sa.String(length=64), nullable=False),
            sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("freshness_status", sa.String(length=32), nullable=False, server_default="UNKNOWN"),
            sa.Column("source", sa.String(length=64), nullable=False, server_default="unknown"),
            sa.Column("cpu_percent", sa.Float(), nullable=True),
            sa.Column("ram_used_gb", sa.Float(), nullable=True),
            sa.Column("ram_total_gb", sa.Float(), nullable=True),
            sa.Column("disk_used_tb", sa.Float(), nullable=True),
            sa.Column("disk_total_tb", sa.Float(), nullable=True),
            sa.Column("gpus_json", sa.JSON(), nullable=False),
            sa.Column("services_json", sa.JSON(), nullable=False),
            sa.Column("workspaces_json", sa.JSON(), nullable=False),
            sa.Column("jobs_json", sa.JSON(), nullable=False),
        )
        op.create_index("ix_telemetry_history_samples_host_id", "telemetry_history_samples", ["host_id"])
        op.create_index("ix_telemetry_history_samples_collected_at", "telemetry_history_samples", ["collected_at"])
        op.create_index("ix_telemetry_samples_host_ts", "telemetry_history_samples", ["host_id", "collected_at"])

    if "agent_tool_audit" not in tables:
        op.create_table(
            "agent_tool_audit",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("correlation_id", sa.String(length=96), nullable=False),
            sa.Column("actor_username", sa.String(length=128), nullable=False),
            sa.Column("actor_role", sa.String(length=32), nullable=False),
            sa.Column("zalo_user_id", sa.String(length=128), nullable=True),
            sa.Column("source", sa.String(length=32), nullable=False, server_default="zalo"),
            sa.Column("question_snippet", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("tool_name", sa.String(length=96), nullable=False),
            sa.Column("tool_args_json", sa.JSON(), nullable=False),
            sa.Column("rbac_allowed", sa.Boolean(), nullable=False, server_default=sa.text("1")),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="SUCCESS"),
            sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("result_summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_agent_tool_audit_correlation_id", "agent_tool_audit", ["correlation_id"])
        op.create_index("ix_agent_tool_audit_actor_username", "agent_tool_audit", ["actor_username"])
        op.create_index("ix_agent_tool_audit_tool_name", "agent_tool_audit", ["tool_name"])
        op.create_index("ix_agent_tool_audit_created_at", "agent_tool_audit", ["created_at"])

    if "scheduler_leases" not in tables:
        op.create_table(
            "scheduler_leases",
            sa.Column("lease_name", sa.String(length=96), primary_key=True),
            sa.Column("owner_id", sa.String(length=128), nullable=False),
            sa.Column("pid", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("hostname", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
        )
        op.create_index("ix_scheduler_leases_expires_at", "scheduler_leases", ["expires_at"])


def downgrade() -> None:
    for tbl in (
        "scheduler_leases",
        "agent_tool_audit",
        "telemetry_history_samples",
        "daily_report_snapshots",
        "report_runs",
        "incident_events",
        "incidents",
        "notification_delivery_attempts",
        "notification_outbox",
        "notification_subscriptions",
        "zalo_identity_links",
    ):
        op.drop_table(tbl)
