from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class HostNodeRecord(Base):
    __tablename__ = "hosts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False, default="COMPUTE_NODE")
    management_ip: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    vpn_ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    agent_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="NOT_CONFIGURED")
    freshness_status: Mapped[str] = mapped_column(String(32), nullable=False, default="OFFLINE")
    last_heartbeat_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class MemberRecord(Base):
    __tablename__ = "members"
    __table_args__ = (
        Index("ix_members_username", "username", unique=False),
        Index(
            "uq_members_active_username",
            "username",
            unique=True,
            sqlite_where=text("status != 'DELETED' OR deleted_at IS NULL"),
            postgresql_where=text("status != 'DELETED' OR deleted_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    full_name: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="RESEARCHER")
    status: Mapped[str] = mapped_column(String(48), nullable=False, default="ACTIVE", index=True)
    provisioning_state: Mapped[str] = mapped_column(String(64), nullable=False, default="PENDING")
    assigned_server: Mapped[str] = mapped_column(String(64), nullable=False, default="workstation")
    workspace_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    workspace_dir: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ssh_port: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    storage_quota_gb: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    cpu_limit_cores: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    ram_limit_gb: Mapped[int] = mapped_column(Integer, nullable=False, default=16)
    vram_quota_gb: Mapped[int] = mapped_column(Integer, nullable=False, default=12)
    gpu_permissions_json: Mapped[List[str]] = mapped_column(JSON, nullable=False, default=list)
    archived_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    archive_manifest_json: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    deleted_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    payload_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class WorkspaceRecord(Base):
    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    owner: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    host_id: Mapped[str] = mapped_column(String(64), nullable=False)
    container_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    runtime_type: Mapped[str] = mapped_column(String(32), nullable=False, default="DOCKER")
    image_ref: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    ssh_host_port: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    lifecycle_state: Mapped[str] = mapped_column(String(48), nullable=False, default="PENDING_RETRY")
    observed_state: Mapped[str] = mapped_column(String(48), nullable=False, default="NOT_VERIFIED")
    non_privileged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    docker_socket_mounted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    limits_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class GpuReservationRecord(Base):
    __tablename__ = "gpu_reservations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    gpu_uuid: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    gpu_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    host_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    owner: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    requested_by: Mapped[str] = mapped_column(String(64), nullable=False)
    job_name: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    mode: Mapped[str] = mapped_column(String(64), nullable=False, default="FULL_GPU_EXCLUSIVE")
    requested_vram_gb: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    runtime_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reserved_at: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    ended_at: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)


class AuditEventRecord(Base):
    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    timestamp: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str] = mapped_column(String(255), nullable=False)
    result: Mapped[str] = mapped_column(String(48), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(96), nullable=False, default="corr-legacy")
    details: Mapped[str] = mapped_column(Text, nullable=False, default="")


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("actor", "endpoint", "idempotency_key", name="uq_idem_actor_endpoint_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(160), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    response_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class RevokedTokenRecord(Base):
    __tablename__ = "revoked_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    jti: Mapped[Optional[str]] = mapped_column(String(128), unique=True, nullable=True)
    username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    member_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    revoked_before_epoch: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(String(128), nullable=False, default="revoked")
    permanent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class PersistenceRecoveryRecord(Base):
    __tablename__ = "persistence_recovery_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(48), nullable=False, default="PERSISTENCE")
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING_RECOVERY", index=True)
    error_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class DeletionOperationRecord(Base):
    __tablename__ = "deletion_operations"

    op_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    precheck_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    steps_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ZaloOutboxRecord(Base):
    __tablename__ = "zalo_outbox"
    __table_args__ = (
        Index("ix_zalo_outbox_month_status", "month_key", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    month_key: Mapped[str] = mapped_column(String(16), nullable=False)
    recipient_id: Mapped[str] = mapped_column(String(128), nullable=False)
    is_critical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="RESERVED")
    message_snippet: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    idempotency_scope: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    error_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ZaloIdentityLinkRecord(Base):
    __tablename__ = "zalo_identity_links"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    zalo_user_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    member_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="RESEARCHER")
    display_name: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING_CODE", index=True)
    link_code_hash: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    code_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    linked_by: Mapped[str] = mapped_column(String(128), nullable=False, default="admin")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class NotificationSubscriptionRecord(Base):
    __tablename__ = "notification_subscriptions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    recipient_zalo_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    member_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="RESEARCHER")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    receive_p0: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    receive_p1: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    receive_daily_report: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    receive_weekly_report: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    receive_repo_alerts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quiet_hours_start: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    quiet_hours_end: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    bypass_quiet_for_p0: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class NotificationOutboxRecord(Base):
    __tablename__ = "notification_outbox"
    __table_args__ = (
        UniqueConstraint("idempotency_key", "recipient_id", name="uq_notif_outbox_idem_recipient"),
        Index("ix_notif_outbox_status_next_retry", "status", "next_retry_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    recipient_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    channel: Mapped[str] = mapped_column(String(32), nullable=False, default="zalo")
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="P1")
    incident_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    report_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    is_critical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    payload_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PENDING", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(96), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class NotificationDeliveryAttemptRecord(Base):
    __tablename__ = "notification_delivery_attempts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    outbox_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    recipient_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    error_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)


class IncidentRecord(Base):
    __tablename__ = "incidents"
    __table_args__ = (
        Index("ix_incidents_status_severity", "status", "severity"),
        Index("ix_incidents_owner_status", "owner_id", "status"),
    )

    incident_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="P1", index=True)
    confidence: Mapped[str] = mapped_column(String(24), nullable=False, default="CONFIRMED")
    source: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    host_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    owner_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    fingerprint: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="OPEN", index=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reopen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evidence_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    recommended_action: Mapped[str] = mapped_column(Text, nullable=False, default="")
    acknowledged_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cooldown_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(96), nullable=False, default="")
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class IncidentEventRecord(Base):
    __tablename__ = "incident_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_kind: Mapped[str] = mapped_column(String(48), nullable=False)
    actor: Mapped[str] = mapped_column(String(128), nullable=False, default="system")
    from_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    to_status: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    details_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    correlation_id: Mapped[str] = mapped_column(String(96), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)


class ReportRunRecord(Base):
    __tablename__ = "report_runs"
    __table_args__ = (
        UniqueConstraint("report_type", "period_key", name="uq_report_runs_type_period"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    report_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    period_key: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    window_start_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="GENERATED")
    author: Mapped[str] = mapped_column(String(128), nullable=False, default="LabOps AI SRE v2")
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    zalo_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    markdown_content: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ai_insights_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    delivery_state_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)


class DailyReportSnapshotRecord(Base):
    __tablename__ = "daily_report_snapshots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    report_run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    host_id: Mapped[str] = mapped_column(String(64), nullable=False, default="cluster")
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    overall_health: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    kpis_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    raw_snapshot_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class TelemetryHistorySampleRecord(Base):
    __tablename__ = "telemetry_history_samples"
    __table_args__ = (
        Index("ix_telemetry_samples_host_ts", "host_id", "collected_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    host_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    freshness_status: Mapped[str] = mapped_column(String(32), nullable=False, default="UNKNOWN")
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    cpu_percent: Mapped[Optional[float]] = mapped_column(nullable=True)
    ram_used_gb: Mapped[Optional[float]] = mapped_column(nullable=True)
    ram_total_gb: Mapped[Optional[float]] = mapped_column(nullable=True)
    disk_used_tb: Mapped[Optional[float]] = mapped_column(nullable=True)
    disk_total_tb: Mapped[Optional[float]] = mapped_column(nullable=True)
    gpus_json: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    services_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    workspaces_json: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    jobs_json: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)


class AgentToolAuditRecord(Base):
    __tablename__ = "agent_tool_audit"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    correlation_id: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    actor_username: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    actor_role: Mapped[str] = mapped_column(String(32), nullable=False)
    zalo_user_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="zalo")
    question_snippet: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    tool_name: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    tool_args_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    rbac_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="SUCCESS")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)


class SchedulerLeaseRecord(Base):
    __tablename__ = "scheduler_leases"

    lease_name: Mapped[str] = mapped_column(String(96), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    pid: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    hostname: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


# ------------------------------------------------------------------
# LabOps MRAM v1.0 Models (Member & Research Access Management)
# ------------------------------------------------------------------


class MemberWebCredentialRecord(Base):
    __tablename__ = "member_web_credentials"

    member_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    hash_algorithm: Mapped[str] = mapped_column(String(32), nullable=False, default="scrypt")
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    password_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class MemberProfileRecord(Base):
    __tablename__ = "member_profiles"

    member_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    full_name: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    email: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    member_code: Mapped[str] = mapped_column(String(64), nullable=False, default="", index=True)
    member_category: Mapped[str] = mapped_column(String(32), nullable=False, default="STUDENT", index=True)
    department_class: Mapped[str] = mapped_column(String(160), nullable=False, default="")
    primary_supervisor_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    direct_manager_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    web_account_status: Mapped[str] = mapped_column(String(32), nullable=False, default="WEB_ACTIVE", index=True)
    ssh_access_state: Mapped[str] = mapped_column(String(48), nullable=False, default="NONE", index=True)
    ssh_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    verified_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False, default="admin")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class SupervisorAssignmentRecord(Base):
    __tablename__ = "supervisor_assignments"
    __table_args__ = (
        Index("ix_sup_assign_student_active", "student_member_id", "is_active"),
        Index("ix_sup_assign_sup_active", "supervisor_member_id", "is_active"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    student_member_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    supervisor_member_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    direct_manager_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    assignment_role: Mapped[str] = mapped_column(String(32), nullable=False, default="PRIMARY")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    assigned_by: Mapped[str] = mapped_column(String(128), nullable=False, default="admin")
    change_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    effective_to: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ServerAccessRequestRecord(Base):
    __tablename__ = "server_access_requests"
    __table_args__ = (
        Index("ix_srv_req_member_status", "member_id", "status"),
        Index("ix_srv_req_status_exp", "status", "review_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_type: Mapped[str] = mapped_column(String(32), nullable=False, default="INITIAL")
    member_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    member_category: Mapped[str] = mapped_column(String(32), nullable=False, default="STUDENT")
    primary_supervisor_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    direct_manager_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    project_title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    registration_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    ssh_purpose: Mapped[str] = mapped_column(Text, nullable=False, default="")
    expected_tasks: Mapped[str] = mapped_column(Text, nullable=False, default="")
    requested_duration_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    gpu_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    gpu_justification: Mapped[str] = mapped_column(Text, nullable=False, default="")
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False, default="mram-v1.0")
    status: Mapped[str] = mapped_column(String(48), nullable=False, default="PENDING_APPROVAL", index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    review_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    decision_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    parent_request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class RequestResourceRecommendationRecord(Base):
    __tablename__ = "request_resource_recommendations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    request_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    recommended_host_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    recommended_pool_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    recommended_filesystem_uuid: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    recommended_mount_point: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    cpu_cores: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    ram_gb: Mapped[int] = mapped_column(Integer, nullable=False, default=16)
    storage_quota_gb: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    gpu_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="NONE")
    gpu_uuid: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    duration_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    manual_review_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rationale_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class RequestApprovalRecord(Base):
    __tablename__ = "request_approvals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    request_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    approver_username: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    approved_config_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    config_hash: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False, default="mram-v1.0")
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, index=True)


class ApprovalInvitationRecord(Base):
    __tablename__ = "approval_invitations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    request_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    admin_username: Mapped[str] = mapped_column(String(64), nullable=False, default="admin")
    admin_zalo_id: Mapped[str] = mapped_column(String(128), nullable=False, default="0c00ed813eccd7928edd")
    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="ACTIVE", index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class CapacityReservationRecord(Base):
    __tablename__ = "capacity_reservations"
    __table_args__ = (
        Index("ix_cap_res_host_pool_status", "host_id", "pool_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    approval_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    member_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    host_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    pool_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    filesystem_uuid: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    mount_point: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    storage_quota_gb: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    cpu_cores: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    ram_gb: Mapped[int] = mapped_column(Integer, nullable=False, default=16)
    gpu_uuid: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    gpu_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="NONE")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="RESERVED", index=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class ProvisioningOperationRecord(Base):
    __tablename__ = "provisioning_operations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    approval_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    approval_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    member_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    config_hash: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(48), nullable=False, default="QUEUED", index=True)
    steps_json: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    verification_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(96), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow)


class CredentialClaimRecord(Base):
    __tablename__ = "credential_claims"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    request_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    provisioning_op_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    claim_type: Mapped[str] = mapped_column(String(48), nullable=False, default="SSH_TEMP_PASSWORD")
    claimed_by: Mapped[str] = mapped_column(String(64), nullable=False)
    step_up_jti: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="CLAIMED")
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


class PolicyAcceptanceRecord(Base):
    __tablename__ = "policy_acceptances"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    member_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False, default="mram-v1.0")
    policy_hash: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)
    client_ip: Mapped[str] = mapped_column(String(64), nullable=False, default="")
