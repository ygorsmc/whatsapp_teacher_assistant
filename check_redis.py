import asyncio
from src.cache.redis_client import get_redis

async def check():
    redis = await get_redis()
    keys = await redis.keys("service_window:*")
    print("Keys found in Redis:", keys)
    for k in keys:
        val = await redis.get(k)
        ttl = await redis.ttl(k)
        print(f"{k} = {val} (TTL: {ttl})")

if __name__ == "__main__":
    asyncio.run(check())
