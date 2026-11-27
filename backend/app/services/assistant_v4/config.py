"""
Validated startup configuration for LabOps Personal AI Agent V4.
All disruptive/proactive V4 feature flags default to OFF so existing production V2
workers and tests remain completely untouched unless explicitly opted in.
"""
from __future__ import annotations

import os
from typing import List, Literal, Optional
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ModelBudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: str = Field(default="model_free", min_length=1, max_length=128)
    paid_fallback: bool = False
    request_limit: int = Field(default=4, ge=1, le=20)
    tool_limit: int = Field(default=8, ge=1, le=32)
    wall_timeout_s: int = Field(default=45, ge=5, le=180)
    daily_user_request_limit: int = Field(default=120, ge=5, le=2000)
    circuit_breaker_failures: int = Field(default=3, ge=1, le=20)
    circuit_breaker_cooldown_s: int = Field(default=120, ge=10, le=3600)


class InvestigationBudgetConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_limit: int = Field(default=8, ge=1, le=20)
    tool_limit: int = Field(default=16, ge=1, le=40)
    wall_timeout_s: int = Field(default=120, ge=10, le=300)
    episode_cooldown_minutes: int = Field(default=30, ge=1, le=1440)


class MemoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idle_ttl_minutes: int = Field(default=60, ge=5, le=1440)
    max_messages: int = Field(default=20, ge=2, le=100)
    max_context_tokens: int = Field(default=4096, ge=512, le=65536)
    raw_chat_retention_days: int = Field(default=7, ge=1, le=90)


class NotificationsV4Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    monthly_budget: int = Field(default=3000, ge=100, le=1000000)
    critical_reserve: int = Field(default=600, ge=0, le=100000)
    soft_percent: int = Field(default=80, ge=10, le=99)
    high_percent: int = Field(default=90, ge=20, le=100)
    target_chars: int = Field(default=1800, ge=400, le=2000)
    p0_unacked_repeat_minutes: int = Field(default=30, ge=5, le=1440)
    p0_acked_repeat_minutes: int = Field(default=360, ge=30, le=1440)
    p1_repeat_hours: int = Field(default=24, ge=1, le=168)
    p1_stale_repeat_hours: int = Field(default=48, ge=1, le=336)
    p2_repeat_hours: int = Field(default=48, ge=1, le=336)
    max_attempts: int = Field(default=5, ge=1, le=20)
    fallback_channel: str = Field(default="dashboard_inbox", min_length=1, max_length=64)

    @field_validator("high_percent")
    @classmethod
    def _validate_percent_order(cls, v: int, info) -> int:
        soft = info.data.get("soft_percent", 80)
        if v < soft:
            raise ValueError("high_percent must be >= soft_percent")
        return v


class SchedulerV4Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    morning: str = "08:00"
    evening: str = "17:00"
    evening_only_if_changed: bool = True
    weekly: str = "MON 08:00"
    weekly_replaces_daily_on_monday: bool = True


class TasksV4Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_active_per_user: int = Field(default=20, ge=1, le=100)
    min_interval_seconds: int = Field(default=60, ge=30, le=86400)
    max_expiry_days: int = Field(default=30, ge=1, le=365)
    catch_up_max_runs: int = Field(default=3, ge=1, le=20)


class WatchdogV4Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    period_seconds: int = Field(default=60, ge=15, le=3600)
    grace_seconds: int = Field(default=180, ge=30, le=7200)
    ping_url_env: str = Field(default="ASSISTANT_V4_WATCHDOG_PING_URL", min_length=1, max_length=128)


class ExternalConnectorsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    github: bool = False
    web_search: bool = False
    email: bool = False
    calendar: bool = False
    documents: bool = False
    request_timeout_s: float = Field(default=10.0, ge=1.0, le=60.0)
    allowed_github_repos: List[str] = Field(default_factory=list)
    allowed_web_domains: List[str] = Field(
        default_factory=lambda: [
            "arxiv.org",
            "export.arxiv.org",
            "api.semanticscholar.org",
            "docs.python.org",
            "github.com",
        ]
    )


class RetentionV4Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    dry_run: bool = True
    run_interval_hours: int = Field(default=24, ge=1, le=720)
    batch_size: int = Field(default=500, ge=10, le=5000)
    max_batches_per_run: int = Field(default=10, ge=1, le=100)
    min_dedupe_safety_hours: int = Field(default=168, ge=24, le=8760)
    chat_messages_days: int = Field(default=7, ge=1, le=365)
    chat_sessions_days: int = Field(default=30, ge=7, le=365)
    delivery_attempts_days: int = Field(default=30, ge=7, le=365)
    terminal_outbox_days: int = Field(default=30, ge=7, le=365)
    inbox_processed_days: int = Field(default=30, ge=7, le=365)
    event_store_days: int = Field(default=90, ge=7, le=730)
    event_receipts_days: int = Field(default=90, ge=7, le=730)
    terminal_proposals_days: int = Field(default=90, ge=7, le=730)
    webhook_deliveries_days: int = Field(default=30, ge=7, le=365)
    terminal_tasks_days: int = Field(default=90, ge=7, le=730)


class AssistantV4Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    event_engine: bool = False
    notification_v2: bool = False
    shadow_mode: bool = False
    task_manager: bool = False
    natural_language: bool = False
    agent_runtime: Literal["native", "pydantic_ai"] = "native"
    proactive_investigation: bool = False
    write_actions: bool = False
    zalo_r3_actions_enabled: bool = False
    zalo_r4_actions_enabled: bool = False
    zalo_secret_delivery_mode: Literal["vault_only", "dm_once"] = "vault_only"
    zalo_short_otp_confirm: bool = False
    ingress_mode: Literal["polling", "webhook"] = "polling"
    timezone: str = "Asia/Ho_Chi_Minh"

    model: ModelBudgetConfig = Field(default_factory=ModelBudgetConfig)
    investigation: InvestigationBudgetConfig = Field(default_factory=InvestigationBudgetConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    notifications: NotificationsV4Config = Field(default_factory=NotificationsV4Config)
    scheduler: SchedulerV4Config = Field(default_factory=SchedulerV4Config)
    tasks: TasksV4Config = Field(default_factory=TasksV4Config)
    watchdog: WatchdogV4Config = Field(default_factory=WatchdogV4Config)
    external_connectors: ExternalConnectorsConfig = Field(default_factory=ExternalConnectorsConfig)
    retention: RetentionV4Config = Field(default_factory=RetentionV4Config)

    @field_validator("timezone")
    @classmethod
    def _validate_tz(cls, v: str) -> str:
        ZoneInfo(v)
        return v

    def enforce_paid_policy(self) -> None:
        if self.model.paid_fallback:
            raise ValueError("paid_fallback=true is prohibited without explicit cost authorization")
        if self.notifications.critical_reserve > self.notifications.monthly_budget:
            raise ValueError("critical_reserve cannot exceed monthly_budget")


def _env_bool(name: str) -> Optional[bool]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    val = raw.strip().lower()
    if val in {"1", "true", "yes", "on"}:
        return True
    if val in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean for {name}: {raw!r}")


def _env_int(name: str) -> Optional[int]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return None
    return int(raw.strip())


def load_assistant_v4_config(config_path: Optional[str] = None) -> AssistantV4Config:
    """
    Load Assistant V4 config safely.
    - Reads optional YAML file if configured (`ASSISTANT_V4_CONFIG_PATH`).
    - Applies `ASSISTANT_V4_*` env overrides.
    - All rollout flags default to OFF (`False`).
    """
    raw_data: dict = {}
    candidate = (config_path or os.getenv("ASSISTANT_V4_CONFIG_PATH", "")).strip()
    if candidate and os.path.exists(candidate):
        with open(candidate, "r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
        if isinstance(loaded, dict):
            raw_data = loaded.get("assistant_v4", loaded) if "assistant_v4" in loaded else loaded
            if not isinstance(raw_data, dict):
                raise ValueError("assistant_v4 YAML root must be a mapping")

    cfg = AssistantV4Config.model_validate(raw_data)

    bool_overrides = {
        "enabled": "ASSISTANT_V4_ENABLED",
        "event_engine": "ASSISTANT_V4_EVENT_ENGINE",
        "notification_v2": "ASSISTANT_V4_NOTIFICATION_V2",
        "shadow_mode": "ASSISTANT_V4_SHADOW_MODE",
        "task_manager": "ASSISTANT_V4_TASK_MANAGER",
        "natural_language": "ASSISTANT_V4_NATURAL_LANGUAGE",
        "proactive_investigation": "ASSISTANT_V4_PROACTIVE_INVESTIGATION",
        "write_actions": "ASSISTANT_V4_WRITE_ACTIONS",
        "zalo_r3_actions_enabled": "ASSISTANT_V4_ZALO_R3_ENABLED",
        "zalo_r4_actions_enabled": "ASSISTANT_V4_ZALO_R4_ENABLED",
        "zalo_short_otp_confirm": "ASSISTANT_V4_SHORT_OTP_CONFIRM",
    }
    for attr, env_name in bool_overrides.items():
        bval = _env_bool(env_name)
        if bval is not None:
            setattr(cfg, attr, bval)

    delivery_mode = (os.getenv("ASSISTANT_V4_SECRET_DELIVERY_MODE") or "").strip().lower()
    if delivery_mode:
        if delivery_mode not in {"vault_only", "dm_once"}:
            raise ValueError(f"Unsupported ASSISTANT_V4_SECRET_DELIVERY_MODE: {delivery_mode!r}")
        cfg.zalo_secret_delivery_mode = delivery_mode  # type: ignore[assignment]

    rt = (os.getenv("ASSISTANT_V4_AGENT_RUNTIME") or "").strip().lower()
    if rt:
        if rt not in {"native", "pydantic_ai"}:
            raise ValueError(f"Unsupported ASSISTANT_V4_AGENT_RUNTIME: {rt!r}")
        cfg.agent_runtime = rt  # type: ignore[assignment]

    ingress = (os.getenv("ASSISTANT_V4_INGRESS_MODE") or "").strip().lower()
    if ingress:
        if ingress not in {"polling", "webhook"}:
            raise ValueError(f"Unsupported ASSISTANT_V4_INGRESS_MODE: {ingress!r}")
        cfg.ingress_mode = ingress  # type: ignore[assignment]

    wd_enabled = _env_bool("ASSISTANT_V4_WATCHDOG_ENABLED")
    if wd_enabled is not None:
        cfg.watchdog.enabled = wd_enabled

    ret_enabled = _env_bool("ASSISTANT_V4_RETENTION_ENABLED")
    if ret_enabled is not None:
        cfg.retention.enabled = ret_enabled
    ret_dry_run = _env_bool("ASSISTANT_V4_RETENTION_DRY_RUN")
    if ret_dry_run is not None:
        cfg.retention.dry_run = ret_dry_run

    paid = _env_bool("ASSISTANT_V4_PAID_FALLBACK")
    if paid is not None:
        cfg.model.paid_fallback = paid

    budget = _env_int("ASSISTANT_V4_MONTHLY_BUDGET")
    if budget is not None:
        cfg.notifications.monthly_budget = budget
    reserve = _env_int("ASSISTANT_V4_CRITICAL_RESERVE")
    if reserve is not None:
        cfg.notifications.critical_reserve = reserve

    alias = (os.getenv("ASSISTANT_V4_MODEL_ALIAS") or os.getenv("ROUTER_MODEL") or "").strip()
    if alias:
        cfg.model.alias = alias

    cfg.enforce_paid_policy()
    return cfg
