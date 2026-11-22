"""
Unified Incident Engine for LabOps AI SRE Agent v2
Provides event normalization, configurable rule evaluation, deduplication by fingerprint,
hysteresis (multi-sample confirmation), cooldown, maintenance windows, correlation,
RBAC acknowledgement/resolution, and lifecycle state machine:
  OPEN -> ACKNOWLEDGED -> INVESTIGATING -> RESOLVED (+ reopen on recurrence)
"""
from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select

from app.db.models import IncidentEventRecord, IncidentRecord
from app.db.session import DatabaseManager

logger = logging.getLogger("labops.sre.incident")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class IncidentRuleThresholds:
    gpu_temp_warn_c: float = 85.0
    gpu_temp_crit_c: float = 92.0
    disk_warn_percent: float = 85.0
    disk_crit_percent: float = 95.0
    cpu_sustained_percent: float = 95.0
    ram_crit_percent: float = 95.0
    hysteresis_samples: int = 2
    cooldown_minutes: int = 30
    ssh_auth_fail_threshold: int = 10

    @classmethod
    def from_env(cls) -> "IncidentRuleThresholds":
        def _flt(k: str, d: float) -> float:
            try:
                return float(os.getenv(k, "").strip() or d)
            except ValueError:
                return d

        def _int(k: str, d: int) -> int:
            try:
                return int(os.getenv(k, "").strip() or d)
            except ValueError:
                return d

        return cls(
            gpu_temp_warn_c=_flt("SRE_GPU_TEMP_WARN_C", 85.0),
            gpu_temp_crit_c=_flt("SRE_GPU_TEMP_CRIT_C", 92.0),
            disk_warn_percent=_flt("SRE_DISK_WARN_PCT", 85.0),
            disk_crit_percent=_flt("SRE_DISK_CRIT_PCT", 95.0),
            cpu_sustained_percent=_flt("SRE_CPU_SUSTAINED_PCT", 95.0),
            ram_crit_percent=_flt("SRE_RAM_CRIT_PCT", 95.0),
            hysteresis_samples=max(1, _int("SRE_HYSTERESIS_SAMPLES", 2)),
            cooldown_minutes=max(1, _int("SRE_COOLDOWN_MINUTES", 30)),
            ssh_auth_fail_threshold=max(3, _int("SRE_SSH_AUTH_FAIL_THRESHOLD", 10)),
        )


def _serialize_incident(row: IncidentRecord) -> Dict[str, Any]:
    return {
        "incident_id": row.incident_id,
        "event_type": row.event_type,
        "severity": row.severity,
        "confidence": row.confidence,
        "source": row.source,
        "resource_id": row.resource_id,
        "host_id": row.host_id,
        "owner_id": row.owner_id,
        "fingerprint": row.fingerprint,
        "title": row.title,
        "status": row.status,
        "occurrence_count": int(row.occurrence_count or 1),
        "reopen_count": int(row.reopen_count or 0),
        "evidence": dict(row.evidence_json or {}),
        "recommended_action": row.recommended_action,
        "acknowledged_by": row.acknowledged_by,
        "acknowledged_at": _as_utc(row.acknowledged_at).isoformat() if row.acknowledged_at else None,
        "resolved_by": row.resolved_by,
        "resolved_at": _as_utc(row.resolved_at).isoformat() if row.resolved_at else None,
        "correlation_id": row.correlation_id,
        "detected_at": _as_utc(row.detected_at).isoformat() if row.detected_at else None,
        "last_observed_at": _as_utc(row.last_observed_at).isoformat() if row.last_observed_at else None,
        "updated_at": _as_utc(row.updated_at).isoformat() if row.updated_at else None,
    }


class UnifiedIncidentEngine:
    def __init__(
        self,
        data_dir: str,
        *,
        db_manager: Optional[DatabaseManager] = None,
        thresholds: Optional[IncidentRuleThresholds] = None,
    ):
        self.data_dir = os.path.abspath(data_dir)
        self.db = db_manager or DatabaseManager(self.data_dir)
        self.thresholds = thresholds or IncidentRuleThresholds.from_env()
        self._streak_counts: Dict[str, int] = {}
        self._maintenance_windows: List[Dict[str, Any]] = []

    def set_maintenance_window(self, *, host_id: str, until_utc: datetime, reason: str = "Scheduled Maintenance") -> None:
        self._maintenance_windows.append(
            {"host_id": host_id, "until_utc": _as_utc(until_utc), "reason": reason}
        )

    def _is_in_maintenance(self, host_id: Optional[str], now: datetime) -> bool:
        if not host_id:
            return False
        active = []
        found = False
        for mw in self._maintenance_windows:
            exp = mw.get("until_utc")
            if exp and exp > now:
                active.append(mw)
                if mw.get("host_id") in (host_id, "*"):
                    found = True
        self._maintenance_windows = active
        return found

    def upsert_incident(
        self,
        *,
        event_type: str,
        severity: str,
        confidence: str = "CONFIRMED",
        source: str,
        resource_id: str,
        host_id: Optional[str] = None,
        owner_id: Optional[str] = None,
        fingerprint: str,
        title: str,
        evidence: Optional[Dict[str, Any]] = None,
        recommended_action: str = "",
        correlation_id: str = "",
    ) -> Tuple[Dict[str, Any], str]:
        """Insert or deduplicate an incident by fingerprint.
        Returns (incident_dict, transition_action) where transition_action is
        'CREATED' | 'UPDATED' | 'REOPENED' | 'SUPPRESSED_MAINTENANCE'.
        """
        now = _utcnow()
        sev = str(severity or "P1").upper()
        conf = str(confidence or "CONFIRMED").upper()
        if sev not in {"P0", "P1", "P2"}:
            sev = "P1"
        if conf not in {"DETECTED", "SUSPECTED", "CONFIRMED"}:
            conf = "CONFIRMED"

        if self._is_in_maintenance(host_id, now) and sev != "P0":
            return (
                {
                    "fingerprint": fingerprint,
                    "host_id": host_id,
                    "status": "SUPPRESSED_MAINTENANCE",
                    "severity": sev,
                },
                "SUPPRESSED_MAINTENANCE",
            )

        with self.db.session_scope() as session:
            existing = session.execute(
                select(IncidentRecord)
                .where(IncidentRecord.fingerprint == fingerprint)
                .order_by(IncidentRecord.detected_at.desc())
            ).scalars().first()

            if existing is not None:
                if existing.status in {"OPEN", "ACKNOWLEDGED", "INVESTIGATING"}:
                    existing.last_observed_at = now
                    existing.occurrence_count = int(existing.occurrence_count or 1) + 1
                    existing.confidence = conf
                    if evidence:
                        merged = dict(existing.evidence_json or {})
                        merged.update(evidence)
                        existing.evidence_json = merged
                    # Escalate severity if newly worse
                    rank = {"P0": 0, "P1": 1, "P2": 2}
                    if rank.get(sev, 9) < rank.get(str(existing.severity).upper(), 9):
                        existing.severity = sev
                    existing.updated_at = now
                    session.flush()
                    return _serialize_incident(existing), "UPDATED"

                if existing.status == "RESOLVED":
                    cooldown = _as_utc(existing.cooldown_until)
                    if cooldown is not None and cooldown > now:
                        existing.last_observed_at = now
                        existing.occurrence_count = int(existing.occurrence_count or 1) + 1
                        session.flush()
                        return _serialize_incident(existing), "UPDATED"

                    prev_status = existing.status
                    existing.status = "OPEN"
                    existing.severity = sev
                    existing.confidence = conf
                    existing.reopen_count = int(existing.reopen_count or 0) + 1
                    existing.occurrence_count = int(existing.occurrence_count or 1) + 1
                    existing.last_observed_at = now
                    existing.resolved_at = None
                    existing.resolved_by = None
                    existing.updated_at = now
                    if evidence:
                        existing.evidence_json = dict(evidence)
                    session.add(
                        IncidentEventRecord(
                            id=f"iev-{uuid.uuid4().hex[:12]}",
                            incident_id=existing.incident_id,
                            event_kind="REOPENED",
                            actor="sre_engine",
                            from_status=prev_status,
                            to_status="OPEN",
                            details_json={"reason": "An anomaly with the same fingerprint recurred after resolution."},
                            correlation_id=correlation_id or existing.correlation_id,
                            created_at=now,
                        )
                    )
                    session.flush()
                    return _serialize_incident(existing), "REOPENED"

            inc_id = f"inc-{uuid.uuid4().hex[:10]}"
            corr = correlation_id or f"corr-{uuid.uuid4().hex[:8]}"
            rec = IncidentRecord(
                incident_id=inc_id,
                event_type=event_type,
                severity=sev,
                confidence=conf,
                source=source,
                resource_id=resource_id,
                host_id=host_id,
                owner_id=owner_id,
                fingerprint=fingerprint,
                title=title[:255],
                status="OPEN",
                occurrence_count=1,
                reopen_count=0,
                evidence_json=dict(evidence or {}),
                recommended_action=recommended_action,
                correlation_id=corr,
                detected_at=now,
                last_observed_at=now,
                updated_at=now,
            )
            session.add(rec)
            session.add(
                IncidentEventRecord(
                    id=f"iev-{uuid.uuid4().hex[:12]}",
                    incident_id=inc_id,
                    event_kind="CREATED",
                    actor="sre_engine",
                    from_status=None,
                    to_status="OPEN",
                    details_json={"severity": sev, "confidence": conf, "source": source},
                    correlation_id=corr,
                    created_at=now,
                )
            )
            session.flush()
            return _serialize_incident(rec), "CREATED"

    def _bump_streak(self, fp: str, active: bool) -> int:
        if not active:
            self._streak_counts.pop(fp, None)
            return 0
        self._streak_counts[fp] = self._streak_counts.get(fp, 0) + 1
        return self._streak_counts[fp]

    def evaluate_snapshot(
        self,
        snapshot: Dict[str, Any],
        *,
        recent_repo_clones: Optional[List[Dict[str, Any]]] = None,
        audit_logs: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """Evaluate telemetry snapshot against deterministic rules with hysteresis and correlation."""
        results: List[Dict[str, Any]] = []
        t = self.thresholds
        hosts = snapshot.get("hosts") or []
        gpus = snapshot.get("gpus") or []
        workspaces = snapshot.get("workspaces") or []
        jobs = snapshot.get("jobs") or []
        services = snapshot.get("services") or {}

        # Build workspace -> recent repo mapping for runtime anomaly correlation
        repo_by_owner: Dict[str, List[str]] = {}
        for rc in (recent_repo_clones or []):
            sub = str(rc.get("submitted_by") or "").strip()
            repo_name = str(rc.get("canonical_repo") or rc.get("repo_url") or "")
            if sub and repo_name:
                repo_by_owner.setdefault(sub, []).append(repo_name)

        # 1. Server health & storage rules
        for h in hosts:
            hid = str(h.get("host_id") or "")
            hname = str(h.get("hostname") or hid)
            st = str(h.get("status") or "UNKNOWN").upper()
            desired = str(h.get("desired_state") or "ACTIVE").upper()
            if desired in {"MAINTENANCE", "DISABLED"}:
                continue

            fp_hb = f"host-unreachable:{hid}"
            if st in {"OFFLINE", "STALE", "UNKNOWN"}:
                streak = self._bump_streak(fp_hb, True)
                sev = "P0" if (st == "OFFLINE" and streak >= t.hysteresis_samples) else "P1"
                conf = "CONFIRMED" if streak >= t.hysteresis_samples else "SUSPECTED"
                inc, action = self.upsert_incident(
                    event_type="NODE_HEARTBEAT_LOST" if st != "STALE" else "NODE_HEARTBEAT_STALE",
                    severity=sev,
                    confidence=conf,
                    source="telemetry",
                    resource_id=hid,
                    host_id=hid,
                    fingerprint=fp_hb,
                    title=f"Máy chủ {hname} ({hid}) trạng thái {st}",
                    evidence={
                        "host_id": hid,
                        "status": st,
                        "last_heartbeat_at": h.get("last_heartbeat_at"),
                        "collected_at": h.get("collected_at"),
                        "consecutive_samples": streak,
                        "error": h.get("error"),
                    },
                    recommended_action="Kiểm tra kết nối mạng, WireGuard mTLS Gateway và dịch vụ labops-agent trên node.",
                )
                results.append({"incident": inc, "action": action})
            else:
                self._bump_streak(fp_hb, False)

            # Storage thresholds
            disk_pct = h.get("disk_usage_percent")
            fp_disk = f"storage-pressure:{hid}"
            if disk_pct is not None and float(disk_pct) >= t.disk_warn_percent:
                streak = self._bump_streak(fp_disk, True)
                val = float(disk_pct)
                if val >= t.disk_crit_percent or streak >= t.hysteresis_samples:
                    sev = "P0" if val >= t.disk_crit_percent else "P1"
                    inc, action = self.upsert_incident(
                        event_type="STORAGE_CRITICAL" if val >= t.disk_crit_percent else "STORAGE_HIGH_USAGE",
                        severity=sev,
                        confidence="CONFIRMED" if streak >= t.hysteresis_samples else "DETECTED",
                        source="storage",
                        resource_id=f"{hid}:disk",
                        host_id=hid,
                        fingerprint=fp_disk,
                        title=f"Dung lượng ổ đĩa trên {hname} đạt {val}%",
                        evidence={"disk_usage_percent": val, "disk_used_tb": h.get("disk_used_tb"), "disk_total_tb": h.get("disk_total_tb"), "streak": streak},
                        recommended_action="Kiểm tra dữ liệu workspace cũ/tệp checkpoint lớn bằng script cleanup an toàn.",
                    )
                    results.append({"incident": inc, "action": action})
            else:
                self._bump_streak(fp_disk, False)

            if h.get("filesystem_readonly") or int(h.get("disk_io_errors") or 0) > 0:
                inc, action = self.upsert_incident(
                    event_type="FILESYSTEM_ERROR",
                    severity="P0",
                    confidence="CONFIRMED",
                    source="storage",
                    resource_id=f"{hid}:fs",
                    host_id=hid,
                    fingerprint=f"fs-error:{hid}",
                    title=f"Lỗi I/O hoặc Read-Only Filesystem trên {hname}",
                    evidence={"filesystem_readonly": h.get("filesystem_readonly"), "disk_io_errors": h.get("disk_io_errors")},
                    recommended_action="Kiểm tra dmesg/SMART ngay để tránh mất dữ liệu.",
                )
                results.append({"incident": inc, "action": action})

        # 2. GPU rules (NEVER fire on normal high utilization or high VRAM usage!)
        for g in gpus:
            gkey = str(g.get("gpu_key") or g.get("gpu_id"))
            hid = str(g.get("host_id") or "")
            if not g.get("host_online"):
                continue

            if g.get("gpu_fault") or int(g.get("xid_errors") or 0) > 0 or int(g.get("ecc_uncorrectable") or 0) > 0:
                inc, action = self.upsert_incident(
                    event_type="GPU_HARDWARE_FAULT",
                    severity="P0",
                    confidence="CONFIRMED",
                    source="gpu",
                    resource_id=gkey,
                    host_id=hid,
                    fingerprint=f"gpu-fault:{gkey}",
                    title=f"Lỗi phần cứng / Xid trên GPU {g.get('model')} ({gkey})",
                    evidence={"xid_errors": g.get("xid_errors"), "ecc_uncorrectable": g.get("ecc_uncorrectable"), "gpu_fault": g.get("gpu_fault")},
                    recommended_action="Kiểm tra nvidia-smi -q / kernel dmesg Xid và cô lập tác vụ khỏi GPU bị lỗi.",
                )
                results.append({"incident": inc, "action": action})

            temp = g.get("temp_celsius")
            fp_temp = f"gpu-temp:{gkey}"
            if temp is not None and float(temp) >= t.gpu_temp_warn_c:
                streak = self._bump_streak(fp_temp, True)
                tval = float(temp)
                if tval >= t.gpu_temp_crit_c and streak >= t.hysteresis_samples:
                    sev = "P0"
                elif streak >= t.hysteresis_samples:
                    sev = "P1"
                else:
                    sev = ""
                if sev:
                    inc, action = self.upsert_incident(
                        event_type="GPU_THERMAL_HIGH",
                        severity=sev,
                        confidence="CONFIRMED",
                        source="gpu",
                        resource_id=gkey,
                        host_id=hid,
                        fingerprint=fp_temp,
                        title=f"Nhiệt độ GPU {gkey} cao kéo dài ({tval}°C)",
                        evidence={"temp_celsius": tval, "utilization_gpu": g.get("utilization_gpu"), "streak": streak},
                        recommended_action="Kiểm tra hệ thống tản nhiệt/quạt GPU và tải huấn luyện đang chạy.",
                    )
                    results.append({"incident": inc, "action": action})
            else:
                self._bump_streak(fp_temp, False)

        # 3. Docker & Workspace rules (with repo correlation on OOM / restart loop)
        for ws in workspaces:
            ws_name = str(ws.get("name") or ws.get("id") or "unknown")
            owner = str(ws.get("owner") or "").strip() or None
            hid = ws.get("host_id")
            correlated_repos = repo_by_owner.get(owner or "", [])[:3]

            if ws.get("oom_killed"):
                inc, action = self.upsert_incident(
                    event_type="DOCKER_OOM_KILLED",
                    severity="P1",
                    confidence="CONFIRMED",
                    source="docker",
                    resource_id=ws_name,
                    host_id=hid,
                    owner_id=owner,
                    fingerprint=f"docker-oom:{ws_name}",
                    title=f"Container Workspace {ws_name} (@{owner or 'unknown'}) bị OOMKilled",
                    evidence={
                        "workspace": ws_name,
                        "owner": owner,
                        "ram_mb_limit": ws.get("ram_mb_limit"),
                        "correlated_repos": correlated_repos,
                    },
                    recommended_action="Giảm batch_size / num_workers hoặc kiểm tra memory leak trong tác vụ đang chạy.",
                )
                results.append({"incident": inc, "action": action})

            if int(ws.get("restart_count") or 0) >= 3:
                inc, action = self.upsert_incident(
                    event_type="DOCKER_RESTART_LOOP",
                    severity="P1",
                    confidence="CONFIRMED",
                    source="docker",
                    resource_id=ws_name,
                    host_id=hid,
                    owner_id=owner,
                    fingerprint=f"docker-restart-loop:{ws_name}",
                    title=f"Container {ws_name} khởi động lại liên tiếp ({ws.get('restart_count')} lần)",
                    evidence={"restart_count": ws.get("restart_count"), "exit_code": ws.get("exit_code"), "correlated_repos": correlated_repos},
                    recommended_action="Kiểm tra docker logs và giới hạn tài nguyên của container.",
                )
                results.append({"incident": inc, "action": action})

            if ws.get("docker_socket_mounted"):
                inc, action = self.upsert_incident(
                    event_type="ISOLATION_POLICY_VIOLATION",
                    severity="P0",
                    confidence="CONFIRMED",
                    source="docker",
                    resource_id=ws_name,
                    host_id=hid,
                    owner_id=owner,
                    fingerprint=f"isolation-docker-sock:{ws_name}",
                    title=f"Phát hiện vi phạm cô lập Docker Socket trong {ws_name}",
                    evidence={"workspace": ws_name, "docker_socket_mounted": True},
                    recommended_action="Cô lập và kiểm tra cấu hình container ngay lập tức.",
                )
                results.append({"incident": inc, "action": action})

        # 4. Training Job rules
        for j in jobs:
            jid = str(j.get("id") or "")
            st = str(j.get("status") or "").upper()
            owner = str(j.get("owner") or "").strip() or None
            if st == "FAILED":
                inc, action = self.upsert_incident(
                    event_type="CUDA_OOM" if j.get("cuda_oom") else "TRAINING_JOB_FAILED",
                    severity="P1",
                    confidence="CONFIRMED",
                    source="jobs",
                    resource_id=f"job:{jid}",
                    host_id=j.get("host_id"),
                    owner_id=owner,
                    fingerprint=f"job-failed:{jid}",
                    title=f"Tác vụ {j.get('name')} (@{owner or 'unknown'}) thất bại",
                    evidence={"job_id": jid, "failure_reason": j.get("failure_reason"), "cuda_oom": j.get("cuda_oom")},
                    recommended_action="Kiểm tra log training và mức VRAM trước khi chạy lại.",
                )
                results.append({"incident": inc, "action": action})
            elif st == "RUNNING" and j.get("stalled"):
                inc, action = self.upsert_incident(
                    event_type="JOB_STALLED_SUSPECTED",
                    severity="P2",
                    confidence="SUSPECTED",
                    source="jobs",
                    resource_id=f"job:{jid}",
                    host_id=j.get("host_id"),
                    owner_id=owner,
                    fingerprint=f"job-stalled:{jid}",
                    title=f"Nghi vấn tác vụ {j.get('name')} bị treo (stalled)",
                    evidence={"job_id": jid, "note": "Cần xác minh log/tiến độ huấn luyện thực tế."},
                    recommended_action="Kiểm tra stdout/checkpoint tiến độ trước khi kết luận treo.",
                )
                results.append({"incident": inc, "action": action})

        # 5. Services health
        pg_info = services.get("postgresql")
        if isinstance(pg_info, dict) and pg_info.get("installed") and not pg_info.get("accepting_connections", True):
            inc, action = self.upsert_incident(
                event_type="POSTGRESQL_UNAVAILABLE",
                severity="P0",
                confidence="CONFIRMED",
                source="services",
                resource_id="postgresql",
                host_id="local",
                fingerprint="service-down:postgresql",
                title="PostgreSQL không tiếp nhận kết nối trên cổng 5432",
                evidence=pg_info,
                recommended_action="Kiểm tra trạng thái dịch vụ PostgreSQL và dung lượng đĩa.",
            )
            results.append({"incident": inc, "action": action})

        # 6. Security audit anomalies (e.g. SSH auth failures spike)
        if audit_logs:
            cutoff = _utcnow() - timedelta(minutes=15)
            ssh_failures = 0
            for a in audit_logs:
                act = str(a.get("action") or "").upper()
                if "SSH" in act and ("FAIL" in act or "DENIED" in str(a.get("result") or "").upper()):
                    ssh_failures += 1
            if ssh_failures >= t.ssh_auth_fail_threshold:
                inc, action = self.upsert_incident(
                    event_type="SSH_AUTH_FAILURES_SPIKE",
                    severity="P1",
                    confidence="SUSPECTED",
                    source="security",
                    resource_id="ssh-gateway",
                    host_id="workstation",
                    fingerprint=f"ssh-auth-spike:{cutoff.strftime('%Y-%m-%d-%H')}",
                    title=f"Ghi nhận {ssh_failures} lần xác thực SSH thất bại/từ chối",
                    evidence={"failed_attempts": ssh_failures, "threshold": t.ssh_auth_fail_threshold},
                    recommended_action="Kiểm tra nhật ký SSH Audit để xác minh có quét mật khẩu trái phép hay không.",
                )
                results.append({"incident": inc, "action": action})

        return results

    def list_incidents(
        self,
        *,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        source: Optional[str] = None,
        owner_id: Optional[str] = None,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        with self.db.session_scope() as session:
            stmt = select(IncidentRecord)
            if status:
                stmt = stmt.where(IncidentRecord.status == status.upper())
            if severity:
                stmt = stmt.where(IncidentRecord.severity == severity.upper())
            if source:
                stmt = stmt.where(IncidentRecord.source == source)
            if owner_id:
                stmt = stmt.where(IncidentRecord.owner_id == owner_id)
            stmt = stmt.order_by(IncidentRecord.last_observed_at.desc()).limit(max(1, min(int(limit), 200)))
            rows = session.execute(stmt).scalars().all()
            return [_serialize_incident(r) for r in rows]

    def get_incident(self, incident_id: str) -> Optional[Dict[str, Any]]:
        with self.db.session_scope() as session:
            row = session.get(IncidentRecord, incident_id)
            if row is None:
                return None
            data = _serialize_incident(row)
            events = session.execute(
                select(IncidentEventRecord)
                .where(IncidentEventRecord.incident_id == incident_id)
                .order_by(IncidentEventRecord.created_at.asc())
            ).scalars().all()
            data["timeline"] = [
                {
                    "id": e.id,
                    "event_kind": e.event_kind,
                    "actor": e.actor,
                    "from_status": e.from_status,
                    "to_status": e.to_status,
                    "details": dict(e.details_json or {}),
                    "created_at": _as_utc(e.created_at).isoformat() if e.created_at else None,
                }
                for e in events
            ]
            return data

    def acknowledge_incident(self, incident_id: str, *, actor: str, role: str, note: str = "") -> Dict[str, Any]:
        norm_role = str(role or "").upper()
        now = _utcnow()
        with self.db.session_scope() as session:
            row = session.get(IncidentRecord, incident_id)
            if row is None:
                raise LookupError(f"Không tìm thấy sự cố {incident_id}")
            if norm_role != "ADMIN" and row.owner_id != actor:
                raise PermissionError("Bạn không có quyền xác nhận sự cố của thành viên khác.")

            prev = row.status
            if prev == "RESOLVED":
                raise ValueError("Sự cố đã ở trạng thái RESOLVED.")
            row.status = "ACKNOWLEDGED"
            row.acknowledged_by = actor
            row.acknowledged_at = now
            row.updated_at = now
            session.add(
                IncidentEventRecord(
                    id=f"iev-{uuid.uuid4().hex[:12]}",
                    incident_id=incident_id,
                    event_kind="ACKNOWLEDGED",
                    actor=actor,
                    from_status=prev,
                    to_status="ACKNOWLEDGED",
                    details_json={"note": note},
                    correlation_id=row.correlation_id,
                    created_at=now,
                )
            )
            session.flush()
            return _serialize_incident(row)

    def resolve_incident(self, incident_id: str, *, actor: str, role: str, note: str = "") -> Dict[str, Any]:
        norm_role = str(role or "").upper()
        if norm_role != "ADMIN":
            raise PermissionError("Chỉ Admin mới có quyền đóng (RESOLVED) sự cố.")
        now = _utcnow()
        with self.db.session_scope() as session:
            row = session.get(IncidentRecord, incident_id)
            if row is None:
                raise LookupError(f"Không tìm thấy sự cố {incident_id}")
            prev = row.status
            row.status = "RESOLVED"
            row.resolved_by = actor
            row.resolved_at = now
            row.cooldown_until = now + timedelta(minutes=self.thresholds.cooldown_minutes)
            row.updated_at = now
            session.add(
                IncidentEventRecord(
                    id=f"iev-{uuid.uuid4().hex[:12]}",
                    incident_id=incident_id,
                    event_kind="RESOLVED",
                    actor=actor,
                    from_status=prev,
                    to_status="RESOLVED",
                    details_json={"note": note},
                    correlation_id=row.correlation_id,
                    created_at=now,
                )
            )
            session.flush()
            return _serialize_incident(row)
