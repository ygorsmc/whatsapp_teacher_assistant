import redis.asyncio as redis
from src.shared.config import settings
import structlog

logger = structlog.get_logger(__name__)

_redis_client = None
_use_fallback = False


class InMemoryRedis:
    """Minimal in-memory fallback when Redis is unavailable (e.g., no Redis provisioned yet).
    Provides the same async interface as redis.asyncio for basic get/set/delete operations.
    NOT suitable for production — no TTL expiry, no persistence, no distributed state.
    """
    def __init__(self):
        self._store: dict[str, str] = {}
        self._lists: dict[str, list[str]] = {}

    async def get(self, key: str):
        return self._store.get(key)

    async def set(self, key: str, value, nx: bool = False, ex: int | None = None):
        if nx and key in self._store:
            return False
        self._store[key] = str(value)
        if ex:
            import asyncio
            async def _expire():
                await asyncio.sleep(ex)
                self._store.pop(key, None)
            asyncio.create_task(_expire())
        return True

    async def incr(self, key: str):
        val = int(self._store.get(key, 0)) + 1
        self._store[key] = str(val)
        return val

    async def expire(self, key: str, seconds: int):
        import asyncio
        async def _expire():
            await asyncio.sleep(seconds)
            self._store.pop(key, None)
            self._lists.pop(key, None)
        asyncio.create_task(_expire())
        return True

    async def exists(self, key: str):
        return 1 if key in self._store or key in self._lists else 0

    async def ttl(self, key: str):
        return -1

    async def delete(self, *keys):
        for k in keys:
            self._store.pop(k, None)
            self._lists.pop(k, None)

    async def rpush(self, key: str, *values):
        if key not in self._lists:
            self._lists[key] = []
        self._lists[key].extend(str(v) for v in values)
        return len(self._lists[key])

    async def lrange(self, key: str, start: int, end: int):
        lst = self._lists.get(key, [])
        if end == -1:
            return lst[start:]
        return lst[start:end + 1]

    async def ltrim(self, key: str, start: int, end: int):
        lst = self._lists.get(key, [])
        if end == -1:
            self._lists[key] = lst[start:]
        else:
            self._lists[key] = lst[start:end + 1]

    async def eval(self, script, numkeys, *keys_and_args):
        """Emulates the debounce drain Lua script: LRANGE + DEL buffer + SET lock."""
        keys = list(keys_and_args[:numkeys])
        args = list(keys_and_args[numkeys:])

        buffer_key = keys[0] if len(keys) > 0 else None
        lock_key = keys[1] if len(keys) > 1 else None
        cooldown = int(args[0]) if args else 1

        # Drain the buffer
        messages = self._lists.pop(buffer_key, []) if buffer_key else []

        # Reset the debounce lock with a short TTL
        if lock_key:
            self._store[lock_key] = "1"
            import asyncio
            async def _expire():
                await asyncio.sleep(cooldown / 1000.0)
                self._store.pop(lock_key, None)
            asyncio.create_task(_expire())

        return messages

    def pipeline(self, transaction: bool = True):
        # Return an object that acts as an async context manager and supports execute()
        class DummyPipeline:
            def __init__(self, parent):
                self.parent = parent
            
            async def __aenter__(self):
                return self
                
            async def __aexit__(self, exc_type, exc_val, exc_tb):
                pass
                
            def __getattr__(self, name):
                def method(*args, **kwargs):
                    import asyncio
                    asyncio.create_task(getattr(self.parent, name)(*args, **kwargs))
                    
                    # Return an awaitable so 'await pipe.method()' doesn't raise TypeError
                    class AwaitableDummy:
                        def __await__(self):
                            async def _dummy():
                                return None
                            return _dummy().__await__()
                    return AwaitableDummy()
                return method
                
            async def execute(self):
                return []
                
        return DummyPipeline(self)

    async def ping(self):
        return True


async def get_redis():
    """Provides the global redis client, falling back to in-memory if Redis is unavailable."""
    global _redis_client, _use_fallback

    if _use_fallback:
        return _redis_client

    if _redis_client is not None:
        return _redis_client

    try:
        client = redis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_timeout=5.0,
            socket_connect_timeout=5.0
        )
        await client.ping()
        _redis_client = client
        logger.info("redis_connected", url=settings.REDIS_URL)
        return _redis_client
    except Exception as e:
        logger.warning("redis_unavailable_using_fallback", error=str(e)[:200])
        _redis_client = InMemoryRedis()
        _use_fallback = True
        return _redis_client
