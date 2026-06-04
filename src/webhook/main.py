from fastapi import FastAPI
from contextlib import asynccontextmanager

from src.shared.config import settings
from src.shared.telemetry import setup_telemetry, track_event
from src.queue.factory import get_queue
from src.db.database import init_db
from src.webhook.handlers import router as webhook_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup structlog
    setup_telemetry(settings.LOG_LEVEL)
    track_event("app_startup", {"environment": settings.ENVIRONMENT})
    
    # Init DB tables (for local dev SQLite)
    await init_db()
    
    yield
    
    # Teardown
    await get_queue().close()
    track_event("app_shutdown")

app = FastAPI(
    title="Teacher Assistant Edge",
    lifespan=lifespan,
    docs_url=None, # Disable Swagger in production
    redoc_url=None
)

app.include_router(webhook_router)

@app.get("/health")
async def health_check():
    return {"status": "ok"}
