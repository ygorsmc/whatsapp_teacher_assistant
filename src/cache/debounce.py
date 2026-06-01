from src.cache.redis_client import get_redis

# Script Lua para drenar atomicamente o buffer e redefinir o lock
DEBOUNCE_DRAIN_SCRIPT = """
local buffer_key = KEYS[1]
local lock_key   = KEYS[2]
local cooldown   = ARGV[1]

-- Lê todas as mensagens do buffer
local messages = redis.call('LRANGE', buffer_key, 0, -1)

-- Deleta o buffer atomicamente
redis.call('DEL', buffer_key)

-- Redefine o lock de debounce com TTL curto (cooldown) para permitir novas interações
redis.call('SET', lock_key, '1', 'EX', cooldown)

return messages
"""

async def drain_buffer(phone: str, cooldown: int = 1) -> list[str]:
    """
    Drains all text currently accumulated in the user's buffer atomically.
    Prevents race conditions where messages are pushed right before DEL.
    """
    redis = await get_redis()
    buffer_key = f"buffer:{phone}"
    lock_key = f"debounce:{phone}"
    
    # Load and execute the Lua script
    raw_messages = await redis.eval(
        DEBOUNCE_DRAIN_SCRIPT,
        2,  # number of keys
        buffer_key, 
        lock_key,
        cooldown # args
    )
    
    if not raw_messages:
        return []
        
    return [msg.decode('utf-8') if isinstance(msg, bytes) else str(msg) for msg in raw_messages]
