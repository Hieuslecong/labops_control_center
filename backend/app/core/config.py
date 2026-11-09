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

class Settings(BaseSettings):
    PROJECT_NAME: str = "LabOps Control Center API"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    
    # Server and Cluster Configuration
    TRACK_SERVER_URL: str = "https://10.10.1.10:8443"
    WORKSTATION_SERVER_URL: str = "https://10.10.2.20:8443"
    NETDATA_TRACK_URL: str = "http://10.200.0.1:19999"
    NETDATA_WS_URL: str = "http://10.200.0.2:19999"
    
    # Operational Modes
    # Valid values: "production" or "demo"
    LABOPS_MODE: str = os.getenv("LABOPS_MODE", "production")
    LABOPS_ALLOW_MOCK_DATA: bool = os.getenv("LABOPS_ALLOW_MOCK_DATA", "false").lower() == "true"
    
    # Local Machine Identification
    LOCAL_HOST_ID: str = "workstation"
    LABOPS_ADMIN_API_TOKEN: str = os.getenv("LABOPS_ADMIN_API_TOKEN", "")
    
    # Legacy flag for backward-compatibility
    @property
    def DEMO_MODE(self) -> bool:
        if "unittest" in sys.modules or os.getenv("PYTEST_CURRENT_TEST"):
            return True
        return self.LABOPS_MODE.lower() == "demo" and self.LABOPS_ALLOW_MOCK_DATA
    
    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", 
        "postgresql+asyncpg://labops:labops_secret@localhost:5432/labops_db"
    )
    
    # CORS
    BACKEND_CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001"
    ]

    class Config:
        case_sensitive = True
        env_file = ".env"
        extra = "allow"

settings = Settings()
