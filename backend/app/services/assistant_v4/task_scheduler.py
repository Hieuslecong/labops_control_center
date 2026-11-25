"""
LabOps Personal AI Agent V4 — Persistent Task Manager, Declarative Watch Engine & Independent Watchdog.
- Strictly typed WatchSpec schema (allowlisted metrics, comparators, finite bounds, no eval/shell/SQL).
- Persistent tasks & watch rules surviving restarts with missed-run policy (skip, coalesce, catch_up_bounded).
- Freshness verification: stale/missing samples immediately break watch hold windows (never false completion).
- Live re-authorization on tick: revoked owners have active tasks cancelled & pending deliveries suppressed.
- Independent Healthchecks-style Watchdog checking actual worker/event/delivery/freshness progress.
"""
from __future__ import annotations

import logging
import os
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Literal, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.db.models import (
    V4AssistantTaskRecord,
    V4EventStoreRecord,
    V4EventConsumerReceiptRecord,
    V4NotificationOutboxRecord,
    V4WatchRuleRecord,
    V4WorkerCursorRecord,
    ZaloIdentityLinkRecord,
)
from app.db.session import DatabaseManager
from app.services.assistant_v4.config import TasksV4Config, WatchdogV4Config
from app.services.assistant_v4.event_store import EventEnvelope, insert_event_in_session
from app.services.assistant_v4.inbox_outbox import OutboxV4Service

logger = logging.getLogger("labops.v4.tasks")
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return _utcnow()
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class WatchSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str = Field(min_length=1, max_length=128)
    metric: Literal[
        "gpu_temperature_c",
        "gpu_util_percent",
        "storage_percent",
        "cpu_percent",
        "ram_percent",
        "node_status",
        "job_status",
    ]
    comparator: Literal["lt", "lte", "gt", "gte", "eq"]
    threshold: float = Field(allow_inf_nan=False)
    sustained_seconds: int = Field(default=300, ge=0, le=86400)
    once: bool = True
    expires_hours: int = Field(default=168, ge=1, le=720)
    unit: str = Field(default="", max_length=32)

    @model_validator(mode="after")
    def _validate_bounds_and_hold(self) -> "WatchSpec":
        pct_metrics = {"gpu_util_percent", "storage_percent", "cpu_percent", "ram_percent"}
        discrete_metrics = {"node_status", "job_status"}
        if self.metric in pct_metrics and not (0.0 <= self.threshold <= 100.0):
            raise ValueError(f"Percentage metric {self.metric} requires 0 <= threshold <= 100")
        if self.metric == "gpu_temperature_c" and not (0.0 <= self.threshold <= 130.0):
            raise ValueError("gpu_temperature_c threshold must be between 0 and 130 °C")
        if self.metric in discrete_metrics and self.threshold not in (0.0, 1.0):
            raise ValueError(f"{self.metric} requires threshold 1.0 (ONLINE/COMPLETED) or 0.0 (OFFLINE/FAILED)")
        if self.metric not in discrete_metrics and self.sustained_seconds < 60:
            raise ValueError(f"Continuous metric watch {self.metric} requires sustained_seconds >= 60")
        if not self.unit:
            self.unit = {
                "gpu_temperature_c": "°C",
                "gpu_util_percent": "%",
                "storage_percent": "%",
                "cpu_percent": "%",
                "ram_percent": "%",
                "node_status": "state",
                "job_status": "state",
            }[self.metric]
        return self


class TaskService:
    VALID_TYPES = {
        "one_time_reminder",
        "recurring_report",
        "metric_watch",
        "incident_recovery_watch",
        "request_sla",
        "job_completion",
        "connector_watch",
    }

    def __init__(self, db_manager: DatabaseManager, *, cfg: Optional[TasksV4Config] = None):
        self.db = db_manager
        self.cfg = cfg or TasksV4Config()

    def create_reminder(
        self,
        *,
        owner_id: str,
        owner_role: str,
        title: str,
        run_at: datetime,
        cadence_minutes: Optional[int] = None,
        missed_run_policy: str = "coalesce",
        recipient_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        if cadence_minutes is not None and cadence_minutes * 60 < self.cfg.min_interval_seconds:
            raise ValueError(f"Minimum recurring interval is {self.cfg.min_interval_seconds} seconds")
        if missed_run_policy not in {"skip", "coalesce", "catch_up_bounded"}:
            raise ValueError("Invalid missed_run_policy")

        now = _utcnow()
        run_utc = _as_utc(run_at)
        max_exp = now + timedelta(days=self.cfg.max_expiry_days)
        idem = idempotency_key or f"rem:{owner_id}:{uuid.uuid4().hex[:10]}"

        with self.db.session_scope() as session:
            existing = session.execute(
                select(V4AssistantTaskRecord).where(V4AssistantTaskRecord.idempotency_key == idem)
            ).scalar_one_or_none()
            if existing is not None:
                return self._serialize_task(existing)

            active_cnt = len(
                session.execute(
                    select(V4AssistantTaskRecord.task_id).where(
                        V4AssistantTaskRecord.owner_id == owner_id,
                        V4AssistantTaskRecord.status.in_(["ACTIVE", "WAITING_CONDITION"]),
                    )
                )
                .scalars()
                .all()
            )
            if active_cnt >= self.cfg.max_active_per_user:
                raise ValueError(f"Exceeded max active tasks ({self.cfg.max_active_per_user}) for user {owner_id}")

            tid = f"tsk4-{uuid.uuid4().hex[:12]}"
            t_type = "recurring_report" if cadence_minutes else "one_time_reminder"
            rec = V4AssistantTaskRecord(
                task_id=tid,
                owner_id=owner_id,
                owner_role=owner_role.upper(),
                task_type=t_type,
                title=title[:255],
                validated_spec_json={
                    "title": title[:255],
                    "cadence_minutes": cadence_minutes,
                    "recipient_id": recipient_id or owner_id,
                },
                missed_run_policy=missed_run_policy,
                timezone="Asia/Ho_Chi_Minh",
                status="ACTIVE",
                next_run_at=run_utc,
                deadline_at=run_utc,
                expires_at=max_exp,
                idempotency_key=idem,
                created_at=now,
                updated_at=now,
            )
            session.add(rec)
            session.flush()
            return self._serialize_task(rec)

    def create_watch(
        self,
        *,
        owner_id: str,
        owner_role: str,
        spec: WatchSpec,
        title: str = "",
        recipient_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        # Scope check: non-admin cannot create watches for other users' private workspaces/jobs unless public node or own target
        if owner_role.upper() != "ADMIN" and spec.target_id.startswith("ws-") and spec.target_id != f"ws-{owner_id}":
            raise PermissionError("Không được phép đặt watch cho workspace của thành viên khác.")

        now = _utcnow()
        exp = min(now + timedelta(hours=spec.expires_hours), now + timedelta(days=self.cfg.max_expiry_days))
        idem = idempotency_key or f"watch:{owner_id}:{uuid.uuid4().hex[:10]}"

        with self.db.session_scope() as session:
            existing = session.execute(
                select(V4AssistantTaskRecord).where(V4AssistantTaskRecord.idempotency_key == idem)
            ).scalar_one_or_none()
            if existing is not None:
                rule = session.execute(
                    select(V4WatchRuleRecord).where(V4WatchRuleRecord.task_id == existing.task_id)
                ).scalar_one_or_none()
                res = self._serialize_task(existing)
                if rule:
                    res["watch_rule"] = self._serialize_rule(rule)
                return res

            active_cnt = len(
                session.execute(
                    select(V4AssistantTaskRecord.task_id).where(
                        V4AssistantTaskRecord.owner_id == owner_id,
                        V4AssistantTaskRecord.status.in_(["ACTIVE", "WAITING_CONDITION"]),
                    )
                )
                .scalars()
                .all()
            )
            if active_cnt >= self.cfg.max_active_per_user:
                raise ValueError(f"Exceeded max active tasks ({self.cfg.max_active_per_user})")

            tid = f"tsk4-{uuid.uuid4().hex[:12]}"
            rid = f"wrl4-{uuid.uuid4().hex[:12]}"
            t_title = title or f"Theo dõi {spec.target_id}: {spec.metric} {spec.comparator} {spec.threshold}{spec.unit}"
            task = V4AssistantTaskRecord(
                task_id=tid,
                owner_id=owner_id,
                owner_role=owner_role.upper(),
                task_type="incident_recovery_watch" if spec.metric == "node_status" else "metric_watch",
                title=t_title[:255],
                validated_spec_json={**spec.model_dump(), "recipient_id": recipient_id or owner_id},
                missed_run_policy="skip",
                timezone="Asia/Ho_Chi_Minh",
                status="WAITING_CONDITION",
                next_run_at=now,
                expires_at=exp,
                idempotency_key=idem,
                created_at=now,
                updated_at=now,
            )
            rule = V4WatchRuleRecord(
                rule_id=rid,
                task_id=tid,
                owner_id=owner_id,
                target_id=spec.target_id,
                metric=spec.metric,
                comparator=spec.comparator,
                threshold=float(spec.threshold),
                unit=spec.unit,
                sustained_seconds=int(spec.sustained_seconds),
                mode="once" if spec.once else "repeat",
                expires_at=exp,
                status="ACTIVE",
                last_state="INIT",
                updated_at=now,
            )
            session.add(task)
            session.add(rule)
            session.flush()
            out = self._serialize_task(task)
            out["watch_rule"] = self._serialize_rule(rule)
            return out

    def list_tasks(self, *, owner_id: Optional[str] = None, is_admin: bool = False, status: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.db.session_scope() as session:
            stmt = select(V4AssistantTaskRecord)
            if not is_admin:
                stmt = stmt.where(V4AssistantTaskRecord.owner_id == owner_id)
            elif owner_id:
                stmt = stmt.where(V4AssistantTaskRecord.owner_id == owner_id)
            if status:
                stmt = stmt.where(V4AssistantTaskRecord.status == status.upper())
            stmt = stmt.order_by(V4AssistantTaskRecord.created_at.desc()).limit(100)
            rows = session.execute(stmt).scalars().all()
            out = []
            for r in rows:
                item = self._serialize_task(r)
                w = session.execute(select(V4WatchRuleRecord).where(V4WatchRuleRecord.task_id == r.task_id)).scalar_one_or_none()
                if w:
                    item["watch_rule"] = self._serialize_rule(w)
                out.append(item)
            return out

    def cancel_task(self, task_id: str, *, actor_id: str, actor_role: str, reason: str = "USER_CANCELLED") -> Dict[str, Any]:
        now = _utcnow()
        with self.db.session_scope() as session:
            task = session.get(V4AssistantTaskRecord, task_id)
            if task is None:
                raise LookupError(f"Task '{task_id}' not found")
            if actor_role.upper() != "ADMIN" and task.owner_id != actor_id:
                raise PermissionError("Bạn không có quyền hủy task của người khác.")
            task.status = "CANCELLED"
            task.cancellation_requested = True
            task.version = int(task.version) + 1
            task.updated_at = now
            meta = dict(task.metadata_json or {})
            meta["cancel_reason"] = reason
            task.metadata_json = meta

            w = session.execute(select(V4WatchRuleRecord).where(V4WatchRuleRecord.task_id == task_id)).scalar_one_or_none()
            if w is not None:
                w.status = "CANCELLED"
                w.updated_at = now

            # Suppress any queued unsent outbox rows belonging to this task
            session.execute(
                update(V4NotificationOutboxRecord)
                .where(
                    V4NotificationOutboxRecord.task_id == task_id,
                    V4NotificationOutboxRecord.status.in_(["PENDING", "FAILED_RETRYABLE"]),
                )
                .values(status="SUPPRESSED", last_error_class="TASK_CANCELLED", last_error=reason[:200], updated_at=now)
            )
            session.flush()
            return self._serialize_task(task)

    def revoke_owner_access(self, owner_id: str) -> int:
        """Disable all active tasks, watches, and pending deliveries when owner access is revoked."""
        now = _utcnow()
        cancelled = 0
        with self.db.session_scope() as session:
            tasks = (
                session.execute(
                    select(V4AssistantTaskRecord).where(
                        V4AssistantTaskRecord.owner_id == owner_id,
                        V4AssistantTaskRecord.status.in_(["ACTIVE", "WAITING_CONDITION", "RUNNING"]),
                    )
                )
                .scalars()
                .all()
            )
            for t in tasks:
                t.status = "CANCELLED"
                t.cancellation_requested = True
                t.version = int(t.version) + 1
                t.updated_at = now
                cancelled += 1
                session.execute(
                    update(V4NotificationOutboxRecord)
                    .where(
                        V4NotificationOutboxRecord.task_id == t.task_id,
                        V4NotificationOutboxRecord.status.in_(["PENDING", "FAILED_RETRYABLE"]),
                    )
                    .values(status="SUPPRESSED", last_error_class="OWNER_REVOKED", last_error="Owner access revoked", updated_at=now)
                )
            session.execute(
                update(V4WatchRuleRecord)
                .where(V4WatchRuleRecord.owner_id == owner_id, V4WatchRuleRecord.status == "ACTIVE")
                .values(status="CANCELLED", updated_at=now)
            )
        return cancelled

    @staticmethod
    def _serialize_task(r: V4AssistantTaskRecord) -> Dict[str, Any]:
        return {
            "task_id": r.task_id,
            "owner_id": r.owner_id,
            "owner_role": r.owner_role,
            "task_type": r.task_type,
            "title": r.title,
            "spec": dict(r.validated_spec_json or {}),
            "missed_run_policy": r.missed_run_policy,
            "status": r.status,
            "next_run_at": _as_utc(r.next_run_at).isoformat() if r.next_run_at else None,
            "last_run_at": _as_utc(r.last_run_at).isoformat() if r.last_run_at else None,
            "run_count": int(r.run_count or 0),
            "expires_at": _as_utc(r.expires_at).isoformat() if r.expires_at else None,
            "version": int(r.version),
            "created_at": _as_utc(r.created_at).isoformat(),
        }

    @staticmethod
    def _serialize_rule(w: V4WatchRuleRecord) -> Dict[str, Any]:
        return {
            "rule_id": w.rule_id,
            "task_id": w.task_id,
            "target_id": w.target_id,
            "metric": w.metric,
            "comparator": w.comparator,
            "threshold": w.threshold,
            "unit": w.unit,
            "sustained_seconds": w.sustained_seconds,
            "mode": w.mode,
            "status": w.status,
            "last_state": w.last_state,
            "last_sample_value": w.last_sample_value,
            "condition_since": _as_utc(w.condition_since).isoformat() if w.condition_since else None,
            "triggered_count": int(w.triggered_count or 0),
        }


class PersistentScheduler:
    """
    Deterministic task & watch tick evaluator.
    Survives restarts cleanly; rechecks authorization before dispatch; verifies fresh telemetry for watches.
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        *,
        cfg: Optional[TasksV4Config] = None,
        owner_authorized_fn: Optional[Callable[[Session, str, str], bool]] = None,
    ):
        self.db = db_manager
        self.cfg = cfg or TasksV4Config()
        self.tasks = TaskService(db_manager, cfg=self.cfg)
        self.outbox = OutboxV4Service(db_manager)
        self.owner_authorized_fn = owner_authorized_fn or self._default_owner_auth

    @staticmethod
    def _default_owner_auth(session: Session, owner_id: str, owner_role: str) -> bool:
        if owner_id == "admin" and owner_role.upper() == "ADMIN":
            return True
        active = (
            session.execute(
                select(ZaloIdentityLinkRecord).where(
                    ((ZaloIdentityLinkRecord.username == owner_id) | (ZaloIdentityLinkRecord.zalo_user_id == owner_id)),
                    ZaloIdentityLinkRecord.status == "ACTIVE",
                )
            )
            .scalars()
            .first()
        )
        # Allow system-created non-Zalo dashboard accounts unless explicitly revoked row exists
        if active is not None:
            return True
        revoked = (
            session.execute(
                select(ZaloIdentityLinkRecord).where(
                    ((ZaloIdentityLinkRecord.username == owner_id) | (ZaloIdentityLinkRecord.zalo_user_id == owner_id)),
                    ZaloIdentityLinkRecord.status == "REVOKED",
                )
            )
            .scalars()
            .first()
        )
        return revoked is None

    @staticmethod
    def _compare(val: float, comparator: str, threshold: float) -> bool:
        if comparator == "lt":
            return val < threshold
        if comparator == "lte":
            return val <= threshold
        if comparator == "gt":
            return val > threshold
        if comparator == "gte":
            return val >= threshold
        if comparator == "eq":
            return abs(val - threshold) < 1e-6
        return False

    @staticmethod
    def _extract_fresh_metric(snapshot: Dict[str, Any], target_id: str, metric: str) -> Optional[float]:
        """Returns float metric ONLY when sample is fresh (ONLINE/DEGRADED). Returns None if missing or stale."""
        hosts = snapshot.get("hosts") or []
        host_by_id = {str(h.get("host_id") or "").lower(): h for h in hosts}
        t_low = target_id.lower()

        if metric == "gpu_temperature_c" or metric == "gpu_util_percent":
            for g in snapshot.get("gpus") or []:
                g_key = str(g.get("gpu_key") or "").lower()
                g_hid = str(g.get("host_id") or "").lower()
                if g_key == t_low or g_hid == t_low or f"{t_low}:" in g_key:
                    host = host_by_id.get(g_hid, {})
                    if not g.get("host_online") or str(host.get("freshness_status") or "").upper() not in {"ONLINE", "DEGRADED"}:
                        return None
                    raw = g.get("temp_celsius") if metric == "gpu_temperature_c" else g.get("utilization_gpu")
                    return float(raw) if raw is not None else None
            return None

        if metric in {"storage_percent", "cpu_percent", "ram_percent", "node_status"}:
            h = host_by_id.get(t_low)
            if not h:
                return None
            fresh = str(h.get("freshness_status") or "").upper()
            st = str(h.get("status") or "").upper()
            if metric == "node_status":
                if fresh in {"ONLINE", "DEGRADED"} and st == "ONLINE":
                    return 1.0
                if fresh in {"OFFLINE", "STALE"}:
                    return 0.0
                return None
            if fresh not in {"ONLINE", "DEGRADED"}:
                return None
            key_map = {"storage_percent": "disk_usage_percent", "cpu_percent": "cpu_usage_percent", "ram_percent": "ram_usage_percent"}
            raw = h.get(key_map[metric])
            return float(raw) if raw is not None else None

        if metric == "job_status":
            for j in snapshot.get("jobs") or []:
                if str(j.get("id") or "").lower() == t_low:
                    st = str(j.get("status") or "").upper()
                    if st == "COMPLETED":
                        return 1.0
                    if st == "FAILED":
                        return 0.0
            return None

        return None

    def tick(self, *, snapshot: Optional[Dict[str, Any]] = None, now_override: Optional[datetime] = None, shadow_mode: bool = False) -> Dict[str, int]:
        now = _as_utc(now_override)
        stats = {"evaluated_tasks": 0, "fired": 0, "watches_triggered": 0, "expired": 0, "revoked": 0, "shadow_suppressed": 0}
        snap = snapshot or {}

        with self.db.session_scope() as session:
            # 1. Watch rules
            rules = (
                session.execute(
                    select(V4WatchRuleRecord).where(V4WatchRuleRecord.status == "ACTIVE")
                )
                .scalars()
                .all()
            )
            for w in rules:
                task = session.get(V4AssistantTaskRecord, w.task_id)
                if task is None or task.cancellation_requested or task.status == "CANCELLED":
                    w.status = "CANCELLED"
                    continue
                if not self.owner_authorized_fn(session, task.owner_id, task.owner_role):
                    w.status = "CANCELLED"
                    task.status = "CANCELLED"
                    stats["revoked"] += 1
                    continue
                if now >= _as_utc(w.expires_at):
                    w.status = "EXPIRED"
                    task.status = "EXPIRED"
                    stats["expired"] += 1
                    continue

                val = self._extract_fresh_metric(snap, w.target_id, w.metric)
                if val is None:
                    # Stale / missing telemetry STRICTLY resets hold window!
                    w.condition_since = None
                    w.last_state = "STALE_OR_MISSING"
                    w.updated_at = now
                    continue

                w.last_sample_at = now
                w.last_sample_value = val
                matched = self._compare(val, w.comparator, w.threshold)
                if not matched:
                    w.condition_since = None
                    w.last_state = "NOT_MET"
                    w.updated_at = now
                    continue

                if w.condition_since is None:
                    w.condition_since = now
                    w.last_state = "HOLDING"
                    w.updated_at = now
                    held_s = 0.0
                else:
                    held_s = max(0.0, (now - _as_utc(w.condition_since)).total_seconds())

                if held_s >= float(w.sustained_seconds):
                    old_w_ver = int(w.version or 1)
                    next_status = "COMPLETED" if w.mode == "once" else w.status
                    w_cas = session.execute(
                        update(V4WatchRuleRecord)
                        .where(
                            V4WatchRuleRecord.rule_id == w.rule_id,
                            V4WatchRuleRecord.status == "ACTIVE",
                            V4WatchRuleRecord.version == old_w_ver,
                        )
                        .values(
                            triggered_count=int(w.triggered_count or 0) + 1,
                            last_triggered_at=now,
                            condition_since=None,
                            last_state="TRIGGERED",
                            status=next_status,
                            version=old_w_ver + 1,
                            updated_at=now,
                        )
                    )
                    if getattr(w_cas, "rowcount", 0) != 1:
                        continue
                    session.refresh(w)
                    if w.mode == "once":
                        task.status = "COMPLETED"
                    task.last_run_at = now
                    task.run_count = int(task.run_count or 0) + 1
                    recipient = str((task.validated_spec_json or {}).get("recipient_id") or task.owner_id)
                    msg = (
                        f"🔔 [ĐIỀU KIỆN WATCH ĐÃ ĐẠT] {task.title}\n"
                        f"• Mục tiêu: {w.target_id} | {w.metric} = {val}{w.unit} (ngưỡng {w.comparator} {w.threshold}{w.unit})\n"
                        f"• Duy trì: {int(held_s)}s | Quan sát lúc: {now.astimezone(VN_TZ).strftime('%H:%M %d/%m')}"
                    )
                    insert_event_in_session(
                        session,
                        EventEnvelope(
                            source="task-scheduler",
                            source_event_id=f"watch:{w.rule_id}:occ:{w.triggered_count}",
                            event_type="task.condition_met",
                            scope_type="user",
                            scope_id=task.owner_id,
                            resource_id=w.rule_id,
                            actor_id=task.owner_id,
                            occurred_at=now,
                            observed_at=now,
                            payload={"task_id": task.task_id, "rule_id": w.rule_id, "value": val},
                        ),
                    )
                    if shadow_mode:
                        stats["shadow_suppressed"] += 1
                    else:
                        self.outbox.enqueue_in_session(
                            session,
                            dedupe_key=f"watch:{w.rule_id}:occ:{w.triggered_count}:{recipient}",
                            recipient_id=recipient,
                            task_id=task.task_id,
                            event_type="task.condition_met",
                            severity="P1",
                            body_text=msg,
                        )
                    stats["watches_triggered"] += 1

            # 2. Scheduled reminders, recurring tasks, SLA tasks
            sched_tasks = (
                session.execute(
                    select(V4AssistantTaskRecord).where(
                        V4AssistantTaskRecord.status == "ACTIVE",
                        V4AssistantTaskRecord.next_run_at.is_not(None),
                        V4AssistantTaskRecord.next_run_at <= now,
                    )
                )
                .scalars()
                .all()
            )
            verified_admins = [
                z
                for z in dict.fromkeys(
                    session.execute(
                        select(ZaloIdentityLinkRecord.zalo_user_id).where(
                            ZaloIdentityLinkRecord.status == "ACTIVE",
                            ZaloIdentityLinkRecord.role == "ADMIN",
                        )
                    )
                    .scalars()
                    .all()
                )
                if z
            ]
            for t in sched_tasks:
                stats["evaluated_tasks"] += 1
                if t.cancellation_requested:
                    t.status = "CANCELLED"
                    continue
                if not self.owner_authorized_fn(session, t.owner_id, t.owner_role):
                    t.status = "CANCELLED"
                    t.cancellation_requested = True
                    stats["revoked"] += 1
                    continue
                if t.expires_at and now >= _as_utc(t.expires_at):
                    t.status = "EXPIRED"
                    stats["expired"] += 1
                    continue
                if t.lease_until and _as_utc(t.lease_until) > now and t.claimed_by:
                    continue

                old_fence = int(t.fence or 0)
                lease_res = session.execute(
                    update(V4AssistantTaskRecord)
                    .where(
                        V4AssistantTaskRecord.task_id == t.task_id,
                        V4AssistantTaskRecord.status == "ACTIVE",
                        V4AssistantTaskRecord.fence == old_fence,
                    )
                    .values(
                        claimed_by="scheduler-v4",
                        lease_until=now + timedelta(seconds=60),
                        fence=old_fence + 1,
                        updated_at=now,
                    )
                )
                if getattr(lease_res, "rowcount", 0) != 1:
                    continue
                session.refresh(t)

                spec = dict(t.validated_spec_json or {})
                cadence_min = spec.get("cadence_minutes")
                explicit_rec = str(spec.get("recipient_id") or "").strip()
                if explicit_rec and explicit_rec != "admin":
                    task_recipients = [explicit_rec]
                elif t.task_type == "request_sla":
                    task_recipients = list(verified_admins) if verified_admins else [t.owner_id]
                else:
                    task_recipients = [explicit_rec or t.owner_id]
                due_at = _as_utc(t.next_run_at)

                if cadence_min:
                    step = timedelta(minutes=int(cadence_min))
                    missed_slots: List[datetime] = []
                    probe = due_at
                    while probe <= now and len(missed_slots) < 200:
                        missed_slots.append(probe)
                        probe += step
                    next_future = probe

                    if t.missed_run_policy == "skip" and (now - due_at) > step:
                        t.next_run_at = next_future
                        t.claimed_by = None
                        t.lease_until = None
                        t.updated_at = now
                        continue
                    fire_slots = missed_slots[-1:] if t.missed_run_policy == "coalesce" else missed_slots[-self.cfg.catch_up_max_runs :]
                    t.next_run_at = next_future
                else:
                    fire_slots = [due_at]
                    t.status = "COMPLETED"
                    t.next_run_at = None

                for slot_dt in fire_slots:
                    t.run_count = int(t.run_count or 0) + 1
                    t.last_run_at = now
                    msg = f"⏰ [NHẮC VIỆC / TASK] {t.title}\n• Mã task: {t.task_id}\n• Mốc: {slot_dt.astimezone(VN_TZ).strftime('%H:%M %d/%m/%Y (ICT)')}"
                    if shadow_mode:
                        stats["shadow_suppressed"] += 1
                    else:
                        for recipient in task_recipients:
                            self.outbox.enqueue_in_session(
                                session,
                                dedupe_key=f"task:{t.task_id}:occ:{int(slot_dt.timestamp())}:{recipient}",
                                recipient_id=recipient,
                                task_id=t.task_id,
                                event_type="task.due",
                                severity="P1" if t.task_type == "request_sla" else "P2",
                                body_text=msg,
                            )
                    stats["fired"] += 1
                t.claimed_by = None
                t.lease_until = None
                t.updated_at = now

            # Record scheduler completion progress heartbeat
            cur = session.get(V4WorkerCursorRecord, "scheduler:last_tick")
            if cur is None:
                session.add(V4WorkerCursorRecord(cursor_name="scheduler:last_tick", cursor_value=now.isoformat(), metadata_json=stats, updated_at=now))
            else:
                cur.cursor_value = now.isoformat()
                cur.metadata_json = stats
                cur.updated_at = now

        return stats


class WatchdogAdapter:
    """
    Independent dead-man watchdog (Healthchecks-style).
    Only pings when REAL work progress & DB checks succeed (never merely because process/HTTP responds).
    Secret ping URL is read from env and never written to logs/API responses.
    """

    def __init__(
        self,
        db_manager: DatabaseManager,
        *,
        cfg: Optional[WatchdogV4Config] = None,
        http_ping_fn: Optional[Callable[[str], None]] = None,
        require_scheduler_tick: bool = True,
    ):
        self.db = db_manager
        self.cfg = cfg or WatchdogV4Config()
        self.http_ping_fn = http_ping_fn or self._default_http_ping
        self.require_scheduler_tick = bool(require_scheduler_tick)

    @staticmethod
    def _default_http_ping(url: str) -> None:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=5.0):
            pass

    def evaluate_progress_checks(self, *, now_override: Optional[datetime] = None) -> Dict[str, Any]:
        now = _as_utc(now_override)
        checks: Dict[str, Dict[str, Any]] = {}
        try:
            with self.db.session_scope() as session:
                session.execute(text("SELECT 1"))
                checks["db_reachability"] = {"ok": True}

                # Check oldest unconsumed event
                oldest_ev = (
                    session.execute(select(V4EventStoreRecord).order_by(V4EventStoreRecord.observed_at.asc()))
                    .scalars()
                    .first()
                )
                if oldest_ev is not None:
                    has_rcp = session.execute(
                        select(V4EventConsumerReceiptRecord.id).where(V4EventConsumerReceiptRecord.event_id == oldest_ev.event_id)
                    ).scalar_one_or_none()
                    ev_age = (now - _as_utc(oldest_ev.observed_at)).total_seconds()
                    checks["event_consumer_progress"] = {
                        "ok": (has_rcp is not None) or ev_age <= self.cfg.grace_seconds,
                        "oldest_unprocessed_age_s": round(ev_age, 1) if has_rcp is None else 0.0,
                    }
                else:
                    checks["event_consumer_progress"] = {"ok": True, "oldest_unprocessed_age_s": 0.0}

                # Check oldest stuck pending notification
                pending_out = (
                    session.execute(
                        select(V4NotificationOutboxRecord)
                        .where(V4NotificationOutboxRecord.status.in_(["PENDING", "FAILED_RETRYABLE"]))
                        .order_by(V4NotificationOutboxRecord.created_at.asc())
                    )
                    .scalars()
                    .first()
                )
                if pending_out is not None:
                    q_age = (now - _as_utc(pending_out.created_at)).total_seconds()
                    checks["notification_worker_progress"] = {
                        "ok": q_age <= self.cfg.grace_seconds,
                        "oldest_pending_age_s": round(q_age, 1),
                    }
                else:
                    checks["notification_worker_progress"] = {"ok": True, "oldest_pending_age_s": 0.0}

                # Check scheduler completion recency (or worker cycle heartbeat when task_manager is disabled)
                sched_cur = session.get(V4WorkerCursorRecord, "scheduler:last_tick")
                if sched_cur and sched_cur.cursor_value:
                    tick_dt = datetime.fromisoformat(sched_cur.cursor_value.replace("Z", "+00:00"))
                    lag = (now - _as_utc(tick_dt)).total_seconds()
                    checks["scheduler_completion"] = {"ok": lag <= self.cfg.grace_seconds, "lag_seconds": round(lag, 1)}
                elif not self.require_scheduler_tick:
                    wk_cur = session.get(V4WorkerCursorRecord, "worker:v4:last_cycle")
                    if wk_cur and wk_cur.cursor_value:
                        wk_dt = datetime.fromisoformat(wk_cur.cursor_value.replace("Z", "+00:00"))
                        lag = (now - _as_utc(wk_dt)).total_seconds()
                        checks["scheduler_completion"] = {
                            "ok": lag <= self.cfg.grace_seconds,
                            "lag_seconds": round(lag, 1),
                            "mode": "worker_cycle_only",
                        }
                    else:
                        checks["scheduler_completion"] = {"ok": False, "reason": "NO_WORKER_CYCLE_RECORDED"}
                else:
                    checks["scheduler_completion"] = {"ok": False, "reason": "NO_TICK_RECORDED"}
        except Exception as exc:
            checks["db_reachability"] = {"ok": False, "error": str(exc)[:150]}

        all_ok = all(c.get("ok") for c in checks.values())
        ping_url = os.getenv(self.cfg.ping_url_env, "").strip()
        configured = bool(self.cfg.enabled and ping_url)
        return {
            "configured": configured,
            "coverage_status": "ACTIVE" if configured else "NOT_CONFIGURED_DEGRADED",
            "healthy": all_ok,
            "checks": checks,
            "checked_at": now.isoformat(),
        }

    def maybe_ping(self, *, now_override: Optional[datetime] = None) -> Dict[str, Any]:
        status = self.evaluate_progress_checks(now_override=now_override)
        ping_url = os.getenv(self.cfg.ping_url_env, "").strip()
        if not status["configured"]:
            return {**status, "pinged": False, "reason": "WATCHDOG_NOT_CONFIGURED"}
        if not status["healthy"]:
            return {**status, "pinged": False, "reason": "PROGRESS_CHECK_FAILED"}
        try:
            self.http_ping_fn(ping_url)
            return {**status, "pinged": True}
        except Exception as exc:
            return {**status, "pinged": False, "reason": f"PING_FAILED: {type(exc).__name__}"}
