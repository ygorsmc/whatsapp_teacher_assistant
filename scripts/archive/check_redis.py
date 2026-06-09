import asyncio
from src.cache.redis_client import get_redis

async def main():
    r = await get_redis()
    print("Inbound Length:", await r.xlen("inbound_messages"))
    groups = await r.xinfo_groups("inbound_messages")
    for g in groups:
        print(g)
    
    pending = await r.xpending("inbound_messages", "worker_llm_group")
    print("Pending info:", pending)

    # See if any consumer is stuck
    consumers = await r.xinfo_consumers("inbound_messages", "worker_llm_group")
    for c in consumers:
        print("Consumer:", c)

asyncio.run(main())
