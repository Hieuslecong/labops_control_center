from typing import Any

from app.core.security import Principal


def visible_jobs(jobs: list[dict[str, Any]], principal: Principal) -> list[dict[str, Any]]:
    if principal.kind != 'user':
        return []
    if principal.is_admin:
        return jobs
    return [job for job in jobs if job.get('username') == principal.username]
