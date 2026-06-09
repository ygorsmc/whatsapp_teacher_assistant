import asyncio
from src.cache.redis_client import get_redis
async def main():
    r = await get_redis()
    print("inbound_messages:", await r.xlen("inbound_messages"))
    print("command_messages:", await r.xlen("command_messages"))
    print("consumer groups:", await r.xinfo_groups("inbound_messages"))
asyncio.run(main())
