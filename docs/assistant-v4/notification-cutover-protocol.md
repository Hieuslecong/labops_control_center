# LabOps Personal AI Agent V4 — Notification Ownership & V2-to-V4 Cutover Protocol

**Document Version:** V4 Final Hardening  
**Authoritative Modules:** [notification_policy.py](file:///home/aiot-ws1/projects/labops_control_center/backend/app/services/assistant_v4/notification_policy.py), [task_scheduler.py](file:///home/aiot-ws1/projects/labops_control_center/backend/app/services/assistant_v4/task_scheduler.py), [worker.py](file:///home/aiot-ws1/projects/labops_control_center/backend/app/services/zalo_assistant/worker.py), [repo_alert_monitor.py](file:///home/aiot-ws1/projects/labops_control_center/backend/app/services/zalo_assistant/repo_alert_monitor.py), [mram_service.py](file:///home/aiot-ws1/projects/labops_control_center/backend/app/services/mram_service.py)  
**External Delivery Semantics:** **At-least-once with bounded `UNKNOWN_DELIVERY` ambiguity.** The Zalo Bot Platform API does not support provider-side idempotency keys; therefore, **strictly guaranteed exactly-once external delivery is not claimed.**

---

## 1. Authoritative Ownership State Machine (`resolve_notification_owner`)

External outbound notification ownership is computed deterministically by [`resolve_notification_owner()`](file:///home/aiot-ws1/projects/labops_control_center/backend/app/services/assistant_v4/notification_policy.py):

| `ASSISTANT_V4_ENABLED` | `ASSISTANT_V4_SHADOW_MODE` | `ASSISTANT_V4_NOTIFICATION_V2` | Effective Owner | External Zalo Alert Sender |
|---|---|---|---|---|
| `false` | `false` / `true` | `false` / `true` | **`V2_LEGACY`** | Legacy V2 (`NotificationDispatcher`, `RepoSecurityAlertMonitor`, `MramService`) |
| `true` | `true` | `false` / `true` | **`V2_LEGACY_SHADOW`** | Legacy V2 only (`V4` performs **zero** external deliveries) |
| `true` | `false` | `false` | **`V2_LEGACY`** | Legacy V2 only (`V4` inbox/events may run if enabled) |
| `true` | `false` | `true` | **`V4_ACTIVE`** | V4 (`EventPolicyEngine` + `OutboxV4Service` + `DigestSchedulerV4`) |

> [!IMPORTANT]
> Even if `ASSISTANT_V4_NOTIFICATION_V2=true` is set alongside `ASSISTANT_V4_SHADOW_MODE=true`, `shadow_mode=true` strictly wins: `worker._run_v4_worker_step`, `mram_service`, and `repo_alert_monitor` keep external notification ownership on V2.

---

## 2. Shadow-Mode Semantics (`V2_LEGACY_SHADOW`)

Verified by `test_shadow_mode_zero_external_sends_zero_quota_and_no_cutover_backlog`:

1. **Event producers stay active:** `mram_service`, `incident_engine`, `repositories`, and `task_scheduler` continue emitting immutable `v4_event_store` records inside DB transactions / `SAVEPOINT`s.
2. **Policy runs in dry-run:** `EventPolicyEngine._enqueue_for_audience()` evaluates verified admin/owner/supervisor routing, subscription category flags (`receive_p0`, `receive_p1`, `receive_repo_alerts`, `receive_daily_report`, `receive_weekly_report`), ICT quiet hours (`22:00–07:00`, P0 bypass), and `QuotaGovernorV4.decide_delivery()` **read-only**.
3. **Structured decisions persisted with reasons:** Every shadow evaluation appends `{event_id, event_type, resource_id, severity, recipients, would_channel, reason, chunk_count, evaluated_at}` (`WOULD_SEND_ZALO`, `WOULD_DEFER_QUIET_HOURS`, `WOULD_COALESCE_DIGEST`, `WOULD_FALLBACK_QUOTA_EXHAUSTED`, `WOULD_SEND_DASHBOARD_ONLY`, `FILTERED_NO_ELIGIBLE_RECIPIENTS`, or `SHADOW_REMINDER_DRY_RUN`) to `v4_worker_cursors["shadow:policy:metrics"]`.
4. **Zero V4 external sends & zero quota consumption:** `v4_notification_outbox` receives **0 rows**; `QuotaGovernorV4.record_usage()` and `add_shed_digest_item()` are **not called**; `PersistentScheduler.tick(..., shadow_mode=True)` suppresses outbox enqueue (`shadow_suppressed`).
5. **No delayed cutover backlog or reminder storm:** Shadow events are committed in `v4_event_consumer_receipts` with `result_status = "SHADOW_DRY_RUN"` (or `SHADOW_CORRELATED:*`), and shadow incident metadata is marked `V4IncidentMetaRecord.shadow_only = True`.

---

## 3. V2-to-V4 Cutover Protocol (`V2_LEGACY` / `V2_LEGACY_SHADOW` → `V4_ACTIVE`)

When flags transition to `V4_ACTIVE`, `AssistantV4Facade.run_worker_cycle()` and `reload_config()` execute `EventPolicyEngine.sync_notification_ownership("V4_ACTIVE")` (or an operator invokes `v4.prepare_notification_cutover()`):

1. **Audit & Watermark Recording:**
   - Records `AuditEventRecord(action="V4_NOTIFICATION_OWNERSHIP_CHANGED", target_type="NOTIFICATION_OWNER", target_id="V4_ACTIVE")`.
   - Persists `{owner: "V4_ACTIVE", previous_owner, v4_active_since, skip_reconciled_before, last_transition_at, transition_count}` into `v4_worker_cursors["notification_cutover:state"]`.
2. **Pre-Cutover Historical Events (`SKIPPED_PRE_CUTOVER`):**
   - Existing unconsumed `v4_event_store` records prior to `v4_active_since` are recorded in `v4_event_consumer_receipts` with `result_status = "SKIPPED_PRE_CUTOVER"` and **0 outbox rows**, while open incidents receive initialized `V4IncidentMetaRecord` entries (`shadow_only=False`, future `next_reminder_at`) so recovery detection and future reminders continue seamlessly.
   - Subsequent `EventReconciler.reconcile()` runs skip backfilling notifications for reconciled pre-cutover items (`payload.reconciled == True` and `occurred_at < skip_reconciled_before`).
3. **Shadow-Era Reminder Reset:**
   - All `V4IncidentMetaRecord` rows with `shadow_only == True` have `shadow_only` cleared and `next_reminder_at` shifted forward by `now + delta` (or cleared if resolved) to prevent a post-cutover reminder flood.
4. **Legacy V2 Pending / Retry Drain & `DELIVERY_UNKNOWN` Handling:**
   - `ZaloPollingWorker._flush_notification_outbox(bot)` continues running every cycle to drain any pre-existing V2 `PENDING` and `FAILED_RETRYABLE` rows cleanly (`notification_outbox`).
   - Legacy `DELIVERY_UNKNOWN` rows and V4 `UNKNOWN_DELIVERY` rows are **never** automatically retried by workers; they require manual inspection / dashboard review.
5. **In-Flight Fencing During External I/O:**
   - `OutboxV4Service.claim_batch()` commits `(status='CLAIMED', claimed_by, lease_until, fence += 1)` **before** any external HTTP call.
   - `record_attempt_start()` commits `V4DeliveryAttemptRecord(result_class='IN_FLIGHT')` **before** calling `bot.send_message()`.
   - Worker crash or timeout before finalization transitions `IN_FLIGHT` claims to `UNKNOWN_DELIVERY` in `reap_expired_leases()`; stale workers cannot finalize with an outdated `fence`.
6. **Logical Event Deduplication:**
   - Producers: `UNIQUE(source, source_event_id)` on `v4_event_store`.
   - Consumer: `UNIQUE(consumer, event_id)` on `v4_event_consumer_receipts`.
   - Outbox: `UNIQUE(dedupe_key)` per logical recipient + chunk on `v4_notification_outbox`.
7. **Legacy Daily (`08:00 ICT`) & Weekly (`MON 08:00` / `FRI 17:00 ICT`) Handover:**
   - Under `V4_ACTIVE`, `ZaloPollingWorker` suppresses legacy `_check_daily_report_schedule()` and `_check_weekly_report_schedule()`.
   - `DigestSchedulerV4.evaluate_due_digests()` additionally inspects legacy `report_runs` (`period_key == today_ict`, `status IN ('SENT','DELIVERY_UNKNOWN','SENDING')`) and `notification_outbox` (`idempotency_key == 'weekly-report:<YYYY-WWW>'`) and writes `COVERED_BY_V2_LEGACY` cursors so cutover mid-day cannot double-send a brief or weekly report.
8. **MRAM & Repository Security Ownership:**
   - `MramService._issue_and_notify_zalo_invitation()` sees `resolve_notification_owner == "V4_ACTIVE"` and returns `DELEGATED_TO_V4_POLICY`, skipping V2 `notification_dispatcher.enqueue()` and `MRAM_IMMEDIATE_ZALO_SEND`.
   - `ZaloPollingWorker` calls `repo_alert_monitor.check_and_notify(bot, suppress_direct_send=True)`, keeping `_record_incident_for_alert()` active (emitting `incident.created` into `v4_event_store`) while skipping V2 direct sends and logging `DELEGATED_TO_V4_POLICY` in `zalo_repo_alerts_state.json`.

---

## 4. Rollback Protocol (`V4_ACTIVE` → `V2_LEGACY` or `V2_LEGACY_SHADOW`)

1. Edit `.env.local` (or `ASSISTANT_V4_CONFIG_PATH` YAML): set `ASSISTANT_V4_NOTIFICATION_V2=false` (optionally `ASSISTANT_V4_SHADOW_MODE=true` or `ASSISTANT_V4_ENABLED=false`).
2. **Restart processes:** Environment variable changes in `.env.local` **do not take effect automatically** in running processes; restart backend (`scripts/deploy-zero-downtime.sh --backend-only`) and `labops-ai-sre-worker`. (If using `ASSISTANT_V4_CONFIG_PATH` YAML only, Admin may also call `POST /api/v1/ai-assistant/v4/config/reload`.)
3. On next worker step:
   - `resolve_notification_owner(config)` immediately returns `V2_LEGACY` (or `V2_LEGACY_SHADOW`).
   - V4 stops claiming/delivering external notifications (`worker._run_v4_worker_step` skips `deliver_claimed_batch`).
   - `sync_notification_ownership()` records the rollback transition in `audit_events` (`V4_NOTIFICATION_OWNERSHIP_CHANGED`).
   - V2 `_check_repo_security_alerts(suppress_direct_send=False)`, `_check_infra_telemetry_and_incidents(suppress_v2_enqueue=False)`, daily/weekly schedulers, and MRAM V2 notifications resume immediately.
4. **Data & Audit Preservation:** Zero rows are deleted from `v4_notification_outbox`, `v4_delivery_attempts`, `v4_event_store`, `v4_assistant_tasks`, or `audit_events`. Any unsent `PENDING` V4 rows remain quarantined in `v4_notification_outbox` until re-enabled or pruned past retention/expiry.
