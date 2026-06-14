from fastapi import FastAPI, Request, Response
from contextlib import asynccontextmanager
import structlog

from src.shared.config import settings
from src.shared.telemetry import setup_telemetry, track_event
from src.queue.factory import get_queue
from src.db.database import init_db
from src.webhook.handlers import router as webhook_router

_logger = structlog.get_logger(__name__)

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


@app.exception_handler(Exception)
async def _global_exception_handler(request: Request, exc: Exception):
    """Invariante (AGENTS.md): a Meta/Telegram NUNCA podem receber status != 2xx
    no webhook — um não-2xx dispara flood de retries (mensagens reprocessadas).
    Falhas inesperadas (ex.: Redis fora) nas rotas de webhook degradam para 200
    (a mensagem é perdida, mas sem flood). As demais rotas (Partner API, health)
    mantêm o comportamento padrão de erro. `HTTPException` tem handler próprio do
    FastAPI e não passa por aqui — os 401/503 intencionais da Partner API valem.
    """
    if request.url.path.startswith("/v1/webhook/"):
        _logger.error("webhook_unhandled_exception", path=request.url.path, error=str(exc)[:300])
        return Response(status_code=200)
    _logger.error("unhandled_exception", path=request.url.path, error=str(exc)[:300])
    return Response(status_code=500)

# H3.3 — Partner API (B2B). Desativada por padrão: todos os endpoints exigem
# X-API-Key == PARTNER_API_KEY; sem a env var, respondem 503.
from src.webhook.partner import router as partner_router  # noqa: E402
app.include_router(partner_router)

@app.get("/health")
async def health_check():
    return {"status": "ok"}
