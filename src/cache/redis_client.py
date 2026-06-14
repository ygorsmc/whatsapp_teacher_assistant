import redis.asyncio as redis
from src.shared.config import settings
import structlog

logger = structlog.get_logger(__name__)

_redis_client = None



async def get_redis():
    """Provides the global redis client.
    Fails fast if connection fails, letting the app crash so it can be restarted.
    """
    global _redis_client

    if _redis_client is not None:
        return _redis_client

    from redis.asyncio.retry import Retry
    from redis.backoff import ExponentialBackoff
    from redis.exceptions import ConnectionError, TimeoutError

    client = redis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
        socket_timeout=5.0,
        socket_connect_timeout=5.0,
        retry_on_timeout=True,
        retry_on_error=[ConnectionError, TimeoutError],
        retry=Retry(ExponentialBackoff(cap=2, base=1), 3),
        health_check_interval=30
    )
    await client.ping()
    _redis_client = client
    logger.info("redis_connected", url=settings.REDIS_URL)
    return _redis_client
