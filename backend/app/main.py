from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.api.v1.api import api_router

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="LabOps Control Center — Production REST API for Multi-Server AI Research Infrastructure",
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    docs_url=f"{settings.API_V1_STR}/docs",
    redoc_url=f"{settings.API_V1_STR}/redoc"
)

# CORS configuration
if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(origin) for origin in settings.BACKEND_CORS_ORIGINS],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(api_router, prefix=settings.API_V1_STR)

@app.get("/healthz", tags=["Health"])
async def healthz():
    return {
        "status": "ok",
        "app": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "mode": "DEMO" if settings.DEMO_MODE else "PRODUCTION"
    }

@app.get("/", tags=["Root"])
async def root():
    return {
        "message": "Welcome to LabOps Control Center API",
        "docs": f"{settings.API_V1_STR}/docs",
        "cluster": {
            "track": "Server 01 (Hà Nội, 3x A30 24GB)",
            "workstation": "Server 02 (TP.HCM, 1x A30 24GB)"
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)

