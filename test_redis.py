
import redis.asyncio as redis
import asyncio

async def test():
    client = redis.from_url('redis://redis-cache.internal.greenbush-cf1c0f1d.brazilsouth.azurecontainerapps.io:6379/0', socket_timeout=2.0)
    try:
        res = await client.ping()
        print('PING:', res)
    except Exception as e:
        print('ERROR:', e)

asyncio.run(test())

