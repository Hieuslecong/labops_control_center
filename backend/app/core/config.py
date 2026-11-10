import os
import sys
from typing import List

try:
    from pydantic_settings import BaseSettings
except ImportError:
    try:
        from pydantic import BaseSettings
    except ImportError:
        class BaseSettings:
            def __init__(self, **kwargs):
                for k, v in kwargs.items():
                    setattr(self, k, v)


def _detect_local_host_id() -> str:
    env_val = os.getenv("LABOPS_LOCAL_HOST_ID") or os.getenv("LOCAL_HOST_ID")
    if env_val:
        return env_val.strip().lower()
    try:
        if os.path.exists("/etc/hostname"):
            with open("/etc/hostname", "r", encoding="utf-8") as _hf:
                h = _hf.read().strip().lower()
            if "rack" in h or "track" in h:
                return "track"
            if "ws" in h or "workstation" in h:
                return "workstation"
    except Exception:
        pass
    return "track"


class Settings(BaseSettings):
    PROJECT_NAME: str = "LabOps Control Center API"
    VERSION: str = "2.0.0"
    API_V1_STR: str = "/api/v1"

    # Server and Cluster Configuration
    TRACK_SERVER_URL: str = "https://192.168.167.250:8443"
    WORKSTATION_SERVER_URL: str = "https://10.10.2.20:8443"
    NETDATA_TRACK_URL: str = "http://10.200.0.1:19999"
    NETDATA_WS_URL: str = "http://10.200.0.2:19999"

    # Operational Modes ("production" or "demo")
    LABOPS_MODE: str = os.getenv("LABOPS_MODE", "production")
    LABOPS_ALLOW_MOCK_DATA: bool = os.getenv("LABOPS_ALLOW_MOCK_DATA", "false").lower() == "true"

    # Local Machine Identification (auto-detects aiotlab-rack -> "track")
    LOCAL_HOST_ID: str = _detect_local_host_id()
    LABOPS_ADMIN_API_TOKEN: str = os.getenv("LABOPS_ADMIN_API_TOKEN", "")
    LABOPS_NODE_AGENT_TOKEN: str = os.getenv("LABOPS_NODE_AGENT_TOKEN", "")

    @property
    def DEMO_MODE(self) -> bool:
        return self.LABOPS_MODE.lower() == "demo" and self.LABOPS_ALLOW_MOCK_DATA

    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://labops:labops_secret@localhost:5432/labops_db"
    )

    # CORS
    BACKEND_CORS_ORIGINS: List[str] = [
        o.strip() for o in os.getenv(
            "LABOPS_CORS_ORIGINS",
            "http://localhost:3000,http://127.0.0.1:3000"
        ).split(",") if o.strip()
    ]

    class Config:
        case_sensitive = True
        env_file = ".env"
        extra = "allow"


settings = Settings()
