from pydantic_settings import BaseSettings
from typing import List

class Settings(BaseSettings):
    PROJECT_NAME: str = "LabOps Control Center API"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    
    # Server and Cluster Configuration
    TRACK_SERVER_URL: str = "https://10.10.1.10:8443"
    WORKSTATION_SERVER_URL: str = "https://10.10.2.20:8443"
    NETDATA_TRACK_URL: str = "http://10.200.0.1:19999"
    NETDATA_WS_URL: str = "http://10.200.0.2:19999"
    
    # Mode
    DEMO_MODE: bool = True
    
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://labops:labops_secret@localhost:5432/labops_db"
    
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

settings = Settings()

