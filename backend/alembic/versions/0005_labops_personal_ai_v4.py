"""Add LabOps Personal AI Agent V4 additive tables (v4_*).

Revision ID: 0005_labops_personal_ai_v4
Revises: 0004_labops_mram_v1
Create Date: 2026-11-25 22:05:00
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0005_labops_personal_ai_v4"
down_revision = "0004_labops_mram_v1"
branch_labels = None
depends_on = None


def _ensure_index(insp: sa.Inspector, index_name: str, table_name: str, columns: list[str]) -> None:
    existing = {idx.get("name") for idx in insp.get_indexes(table_name)}
    if index_name not in existing:
        op.create_index(index_name, table_name, columns)


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    if "v4_event_store" not in tables:
        op.create_table(
            "v4_event_store",
            sa.Column("event_id", sa.String(length=64), primary_key=True),
            sa.Column("source", sa.String(length=64), nullable=False),
            sa.Column("source_event_id", sa.String(length=200), nullable=False),
            sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("event_type", sa.String(length=96), nullable=False),
            sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="aiot-lab"),
            sa.Column("scope_type", sa.String(length=32), nullable=False, server_default="lab"),
            sa.Column("scope_id", sa.String(length=128), nullable=False, server_default="global"),
            sa.Column("resource_id", sa.String(length=255), nullable=False),
            sa.Column("actor_id", sa.String(length=128), nullable=True),
            sa.Column("source_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("payload_redacted_json", sa.JSON(), nullable=False),
            sa.Column("correlation_id", sa.String(length=96), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("source", "source_event_id", name="uq_v4_event_source_eid"),
        )
    _ensure_index(insp, "ix_v4_event_type_observed", "v4_event_store", ["event_type", "observed_at"])
    _ensure_index(insp, "ix_v4_event_store_resource", "v4_event_store", ["resource_id"])
    _ensure_index(insp, "ix_v4_event_store_corr", "v4_event_store", ["correlation_id"])

    if "v4_event_consumer_receipts" not in tables:
        op.create_table(
            "v4_event_consumer_receipts",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("consumer", sa.String(length=64), nullable=False),
            sa.Column("event_id", sa.String(length=64), nullable=False),
            sa.Column("source", sa.String(length=64), nullable=False),
            sa.Column("source_event_id", sa.String(length=200), nullable=False),
            sa.Column("resource_id", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("source_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("result_status", sa.String(length=32), nullable=False, server_default="PROCESSED"),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("consumer", "event_id", name="uq_v4_consumer_event"),
        )
    _ensure_index(insp, "ix_v4_receipt_consumer_res", "v4_event_consumer_receipts", ["consumer", "resource_id", "observed_at"])

    if "v4_message_inbox" not in tables:
        op.create_table(
            "v4_message_inbox",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("channel", sa.String(length=32), nullable=False, server_default="zalo"),
            sa.Column("bot_id", sa.String(length=64), nullable=False, server_default="default"),
            sa.Column("external_message_id", sa.String(length=160), nullable=False),
            sa.Column("update_id", sa.Integer(), nullable=True),
            sa.Column("chat_id", sa.String(length=128), nullable=False),
            sa.Column("sender_id", sa.String(length=128), nullable=False),
            sa.Column("principal_id", sa.String(length=128), nullable=True),
            sa.Column("payload_redacted_json", sa.JSON(), nullable=False),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("state", sa.String(length=32), nullable=False, server_default="RECEIVED"),
            sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("claimed_by", sa.String(length=128), nullable=True),
            sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("fence", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("error_detail", sa.Text(), nullable=True),
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("channel", "bot_id", "external_message_id", name="uq_v4_inbox_channel_bot_msg"),
        )
    _ensure_index(insp, "ix_v4_inbox_state_lease", "v4_message_inbox", ["state", "lease_until"])

    if "v4_worker_cursors" not in tables:
        op.create_table(
            "v4_worker_cursors",
            sa.Column("cursor_name", sa.String(length=96), primary_key=True),
            sa.Column("cursor_value", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    if "v4_notification_outbox" not in tables:
        op.create_table(
            "v4_notification_outbox",
            sa.Column("notification_id", sa.String(length=64), primary_key=True),
            sa.Column("dedupe_key", sa.String(length=255), nullable=False, unique=True),
            sa.Column("event_id", sa.String(length=64), nullable=True),
            sa.Column("task_id", sa.String(length=64), nullable=True),
            sa.Column("recipient_id", sa.String(length=128), nullable=False),
            sa.Column("channel", sa.String(length=32), nullable=False, server_default="zalo"),
            sa.Column("priority", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("severity", sa.String(length=16), nullable=False, server_default="P1"),
            sa.Column("event_type", sa.String(length=96), nullable=False),
            sa.Column("body_version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("policy_version", sa.String(length=32), nullable=False, server_default="v4.0"),
            sa.Column("is_critical", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("chunk_index", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("chunk_total", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("body_text", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="PENDING"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("claimed_by", sa.String(length=128), nullable=True),
            sa.Column("fence", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("provider_message_id", sa.String(length=128), nullable=True),
            sa.Column("last_error_class", sa.String(length=64), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("correlation_id", sa.String(length=96), nullable=False, server_default=""),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    _ensure_index(insp, "ix_v4_outbox_claim", "v4_notification_outbox", ["status", "priority", "not_before"])
    _ensure_index(insp, "ix_v4_outbox_lease", "v4_notification_outbox", ["status", "lease_until"])
    _ensure_index(insp, "ix_v4_outbox_recipient", "v4_notification_outbox", ["recipient_id"])

    if "v4_delivery_attempts" not in tables:
        op.create_table(
            "v4_delivery_attempts",
            sa.Column("attempt_id", sa.String(length=64), primary_key=True),
            sa.Column("outbox_id", sa.String(length=64), nullable=False),
            sa.Column("recipient_id", sa.String(length=128), nullable=False),
            sa.Column("channel", sa.String(length=32), nullable=False, server_default="zalo"),
            sa.Column("attempt_number", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("fence", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("result_class", sa.String(length=48), nullable=False, server_default="IN_FLIGHT"),
            sa.Column("provider_message_id", sa.String(length=128), nullable=True),
            sa.Column("redacted_error", sa.Text(), nullable=True),
            sa.Column("duration_ms", sa.Integer(), nullable=False, server_default="0"),
        )
    _ensure_index(insp, "ix_v4_del_att_outbox", "v4_delivery_attempts", ["outbox_id"])

    if "v4_incident_meta" not in tables:
        op.create_table(
            "v4_incident_meta",
            sa.Column("incident_id", sa.String(length=64), primary_key=True),
            sa.Column("parent_incident_id", sa.String(length=64), nullable=True),
            sa.Column("correlation_group_key", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("episode_number", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("last_reminded_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("reminder_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("next_reminder_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("suppressed_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("suppression_reason", sa.String(length=128), nullable=True),
            sa.Column("shadow_only", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    _ensure_index(insp, "ix_v4_inc_meta_parent", "v4_incident_meta", ["parent_incident_id"])
    _ensure_index(insp, "ix_v4_inc_meta_rem", "v4_incident_meta", ["next_reminder_at"])

    if "v4_assistant_tasks" not in tables:
        op.create_table(
            "v4_assistant_tasks",
            sa.Column("task_id", sa.String(length=64), primary_key=True),
            sa.Column("owner_id", sa.String(length=128), nullable=False),
            sa.Column("owner_role", sa.String(length=32), nullable=False, server_default="RESEARCHER"),
            sa.Column("task_type", sa.String(length=48), nullable=False),
            sa.Column("title", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("validated_spec_json", sa.JSON(), nullable=False),
            sa.Column("missed_run_policy", sa.String(length=32), nullable=False, server_default="coalesce"),
            sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Asia/Ho_Chi_Minh"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
            sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("run_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("claimed_by", sa.String(length=128), nullable=True),
            sa.Column("fence", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("cancellation_requested", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("idempotency_key", sa.String(length=200), nullable=False, unique=True),
            sa.Column("metadata_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    _ensure_index(insp, "ix_v4_tasks_due", "v4_assistant_tasks", ["status", "next_run_at"])
    _ensure_index(insp, "ix_v4_tasks_owner", "v4_assistant_tasks", ["owner_id", "status"])

    if "v4_watch_rules" not in tables:
        op.create_table(
            "v4_watch_rules",
            sa.Column("rule_id", sa.String(length=64), primary_key=True),
            sa.Column("task_id", sa.String(length=64), nullable=False),
            sa.Column("owner_id", sa.String(length=128), nullable=False),
            sa.Column("target_id", sa.String(length=128), nullable=False),
            sa.Column("metric", sa.String(length=64), nullable=False),
            sa.Column("comparator", sa.String(length=16), nullable=False),
            sa.Column("threshold", sa.Float(), nullable=False),
            sa.Column("unit", sa.String(length=32), nullable=False, server_default=""),
            sa.Column("sustained_seconds", sa.Integer(), nullable=False, server_default="300"),
            sa.Column("mode", sa.String(length=16), nullable=False, server_default="once"),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
            sa.Column("condition_since", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_sample_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_sample_value", sa.Float(), nullable=True),
            sa.Column("last_state", sa.String(length=32), nullable=False, server_default="INIT"),
            sa.Column("triggered_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_triggered_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    _ensure_index(insp, "ix_v4_watches_status_target", "v4_watch_rules", ["status", "target_id"])
    _ensure_index(insp, "ix_v4_watches_owner", "v4_watch_rules", ["owner_id"])

    if "v4_action_proposals" not in tables:
        op.create_table(
            "v4_action_proposals",
            sa.Column("action_id", sa.String(length=64), primary_key=True),
            sa.Column("actor_id", sa.String(length=128), nullable=False),
            sa.Column("actor_role", sa.String(length=32), nullable=False),
            sa.Column("session_id", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("chat_id", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("action_type", sa.String(length=64), nullable=False),
            sa.Column("risk_tier", sa.String(length=16), nullable=False, server_default="R2"),
            sa.Column("target_type", sa.String(length=48), nullable=False),
            sa.Column("target_id", sa.String(length=255), nullable=False),
            sa.Column("target_version", sa.String(length=64), nullable=False, server_default="1"),
            sa.Column("canonical_args_json", sa.JSON(), nullable=False),
            sa.Column("canonical_args_hash", sa.String(length=128), nullable=False),
            sa.Column("nonce_hash", sa.String(length=128), nullable=True),
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("state", sa.String(length=32), nullable=False, server_default="PROPOSED"),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("idempotency_key", sa.String(length=200), nullable=False, unique=True),
            sa.Column("core_result_ref", sa.Text(), nullable=True),
            sa.Column("error_detail", sa.Text(), nullable=True),
            sa.Column("reason", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    _ensure_index(insp, "ix_v4_proposals_actor_state", "v4_action_proposals", ["actor_id", "state", "expires_at"])

    if "v4_chat_sessions" not in tables:
        op.create_table(
            "v4_chat_sessions",
            sa.Column("session_id", sa.String(length=64), primary_key=True),
            sa.Column("scope_key", sa.String(length=255), nullable=False, unique=True),
            sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="aiot-lab"),
            sa.Column("principal_id", sa.String(length=128), nullable=False),
            sa.Column("channel", sa.String(length=32), nullable=False, server_default="zalo"),
            sa.Column("bot_id", sa.String(length=64), nullable=False, server_default="default"),
            sa.Column("chat_id", sa.String(length=128), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
            sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("lease_owner", sa.String(length=128), nullable=True),
            sa.Column("sequence_no", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("summary_text", sa.Text(), nullable=False, server_default=""),
            sa.Column("last_active_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    _ensure_index(insp, "ix_v4_chat_sess_principal", "v4_chat_sessions", ["principal_id", "last_active_at"])

    if "v4_chat_messages" not in tables:
        op.create_table(
            "v4_chat_messages",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("session_id", sa.String(length=64), nullable=False),
            sa.Column("seq", sa.Integer(), nullable=False),
            sa.Column("role", sa.String(length=24), nullable=False),
            sa.Column("tool_call_id", sa.String(length=128), nullable=True),
            sa.Column("tool_name", sa.String(length=96), nullable=True),
            sa.Column("content_redacted", sa.Text(), nullable=False, server_default=""),
            sa.Column("payload_json", sa.JSON(), nullable=False),
            sa.Column("token_estimate", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
    _ensure_index(insp, "ix_v4_chat_msg_session_seq", "v4_chat_messages", ["session_id", "seq"])

    if "v4_user_preferences" not in tables:
        op.create_table(
            "v4_user_preferences",
            sa.Column("principal_id", sa.String(length=128), primary_key=True),
            sa.Column("timezone", sa.String(length=64), nullable=False, server_default="Asia/Ho_Chi_Minh"),
            sa.Column("notification_prefs_json", sa.JSON(), nullable=False),
            sa.Column("opted_in_preferences_json", sa.JSON(), nullable=False),
            sa.Column("authorized_subscriptions_json", sa.JSON(), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    if "v4_knowledge_documents" not in tables:
        op.create_table(
            "v4_knowledge_documents",
            sa.Column("doc_id", sa.String(length=64), primary_key=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("owner_id", sa.String(length=128), nullable=False, server_default="admin"),
            sa.Column("scope", sa.String(length=32), nullable=False, server_default="shared"),
            sa.Column("source", sa.String(length=128), nullable=False, server_default="reviewed_runbook"),
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("reviewed_by", sa.String(length=128), nullable=False, server_default="admin"),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="ACTIVE"),
            sa.Column("content_markdown", sa.Text(), nullable=False),
            sa.Column("content_hash", sa.String(length=128), nullable=False),
            sa.Column("tags_json", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        )
    _ensure_index(insp, "ix_v4_kb_scope_status", "v4_knowledge_documents", ["scope", "status"])

    if "v4_investigations" not in tables:
        op.create_table(
            "v4_investigations",
            sa.Column("run_id", sa.String(length=64), primary_key=True),
            sa.Column("incident_id", sa.String(length=64), nullable=False),
            sa.Column("episode_key", sa.String(length=200), nullable=False, unique=True),
            sa.Column("runbook_id", sa.String(length=96), nullable=False, server_default="generic"),
            sa.Column("state", sa.String(length=32), nullable=False, server_default="COMPLETED"),
            sa.Column("summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("facts_json", sa.JSON(), nullable=False),
            sa.Column("evidence_refs_json", sa.JSON(), nullable=False),
            sa.Column("hypotheses_json", sa.JSON(), nullable=False),
            sa.Column("verified_root_cause", sa.Text(), nullable=True),
            sa.Column("missing_information_json", sa.JSON(), nullable=False),
            sa.Column("recommendations_json", sa.JSON(), nullable=False),
            sa.Column("model_route", sa.String(length=128), nullable=False, server_default="deterministic"),
            sa.Column("model_requests_used", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("tool_calls_used", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("reviewed_by", sa.String(length=128), nullable=True),
            sa.Column("review_feedback", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        )
    _ensure_index(insp, "ix_v4_inv_incident", "v4_investigations", ["incident_id"])

    if "v4_connector_states" not in tables:
        op.create_table(
            "v4_connector_states",
            sa.Column("connector_id", sa.String(length=64), primary_key=True),
            sa.Column("owner_id", sa.String(length=128), nullable=False, server_default="admin"),
            sa.Column("connector_type", sa.String(length=48), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="NOT_CONFIGURED"),
            sa.Column("capabilities_json", sa.JSON(), nullable=False),
            sa.Column("cursor", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.Text(), nullable=True),
            sa.Column("config_redacted_json", sa.JSON(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    if "v4_webhook_deliveries" not in tables:
        op.create_table(
            "v4_webhook_deliveries",
            sa.Column("id", sa.String(length=64), primary_key=True),
            sa.Column("connector_id", sa.String(length=64), nullable=False),
            sa.Column("delivery_id", sa.String(length=160), nullable=False),
            sa.Column("event_type", sa.String(length=96), nullable=False),
            sa.Column("signature_verified", sa.Boolean(), nullable=False, server_default=sa.text("0")),
            sa.Column("status", sa.String(length=32), nullable=False, server_default="ACCEPTED"),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("connector_id", "delivery_id", name="uq_v4_webhook_connector_delivery"),
        )
    _ensure_index(insp, "ix_v4_wh_conn", "v4_webhook_deliveries", ["connector_id"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())
    for tbl in (
        "v4_webhook_deliveries",
        "v4_connector_states",
        "v4_investigations",
        "v4_knowledge_documents",
        "v4_user_preferences",
        "v4_chat_messages",
        "v4_chat_sessions",
        "v4_action_proposals",
        "v4_watch_rules",
        "v4_assistant_tasks",
        "v4_incident_meta",
        "v4_delivery_attempts",
        "v4_notification_outbox",
        "v4_worker_cursors",
        "v4_message_inbox",
        "v4_event_consumer_receipts",
        "v4_event_store",
    ):
        if tbl in tables:
            op.drop_table(tbl)
