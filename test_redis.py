import asyncio
from src.queue.factory import get_queue

async def main():
    q = get_queue()
    # If using Redis queue
    from src.cache.redis_client import get_redis
    redis = await get_redis()
    messages = await redis.xrange("queue:command_messages", "-", "+")
    print("Command messages queue length:", len(messages))
    if messages:
        print("First message:", messages[0])
        print("Last message:", messages[-1])
        
    outbound = await redis.xrange("queue:outbound_messages", "-", "+")
    print("Outbound messages queue length:", len(outbound))

asyncio.run(main())
