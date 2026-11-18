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

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
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
    revoked_before_epoch: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    reason: Mapped[str] = mapped_column(String(128), nullable=False, default="revoked")
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_utcnow)


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
