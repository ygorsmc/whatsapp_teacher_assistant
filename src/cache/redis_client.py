import redis.asyncio as redis
from src.shared.config import settings

# Global Redis Connection Pool
redis_client = redis.from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True
)

async def get_redis():
    """Provides the global redis client"""
    return redis_client
