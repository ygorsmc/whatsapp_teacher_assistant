import hmac
import hashlib
from fastapi import Request
from src.shared.config import settings
from src.cache.redis_client import get_redis

async def verify_meta_signature(body: bytes, signature: str) -> bool:
    """
    Validates HMAC-SHA256 signature from Meta to prevent forged requests.
    """
    if settings.ENVIRONMENT in ["development", "dev"]:
        return True
        
    if not signature:
        return False
        
    expected = "sha256=" + hmac.new(
        settings.META_APP_SECRET.encode('utf-8'), 
        body, 
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(signature, expected)

async def check_rate_limit(phone: str, limit: int = 60, window: int = 60) -> bool:
    """
    Returns True if within limit; False if exceeded.
    Default: 60 requests per minute per phone.
    """
    redis = await get_redis()
    rate_key = f"rate:{phone}"
    
    count = await redis.incr(rate_key)
    if count == 1:
        await redis.expire(rate_key, window)
        
    return count <= limit
