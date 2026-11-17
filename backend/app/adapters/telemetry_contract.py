from datetime import datetime, timezone
from typing import Optional


MAX_TELEMETRY_AGE_SECONDS = 90


def is_fresh_timestamp(value: Optional[str], max_age_seconds: int = MAX_TELEMETRY_AGE_SECONDS) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - observed.astimezone(timezone.utc)).total_seconds()
        return 0 <= age <= max_age_seconds
    except (TypeError, ValueError, OverflowError):
        return False
