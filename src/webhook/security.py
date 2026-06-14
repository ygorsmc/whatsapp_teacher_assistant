import structlog

from src.cache.redis_client import get_redis

logger = structlog.get_logger(__name__)


async def check_rate_limit(phone: str, limit: int = 60, window: int = 60) -> bool:
    """
    Returns True if within limit; False if exceeded.
    Default: 60 requests per minute per phone.

    Fail-open: se o Redis estiver indisponível, deixa passar (retorna True). O
    rate-limit é proteção contra flood, não um portão de segurança — derrubar a
    mensagem (ou 500 para a Meta) por causa de um Redis instável é pior que
    deixar passar. A idempotência e o gate de role seguem como portões reais.
    """
    try:
        redis = await get_redis()
        rate_key = f"rate:{phone}"

        count = await redis.incr(rate_key)
        if count == 1:
            await redis.expire(rate_key, window)

        return count <= limit
    except Exception as e:
        logger.warning("rate_limit_fail_open", error=str(e)[:200])
        return True
