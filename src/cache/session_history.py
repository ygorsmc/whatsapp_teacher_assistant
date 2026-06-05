from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import BaseMessage, messages_from_dict, message_to_dict
from src.cache.redis_client import get_redis
from typing import Sequence
import json

class OptimizedRedisChatMessageHistory(BaseChatMessageHistory):
    """
    Redis-based chat history that aggressively enforces a maximum length natively in Redis via LTRIM,
    preventing Out-of-Memory errors and excessive token usage without needing ConversationTokenBufferMemory.
    
    WARNING: Synchronous methods are disabled. You MUST use async methods.
    """
    def __init__(self, session_id: str, max_messages: int = 40):
        self.session_id = session_id
        self.max_messages = max_messages
        self.key = f"chat_history:{session_id}"
        self.redis_client = None

    async def _get_client(self):
        if not self.redis_client:
            self.redis_client = await get_redis()
        return self.redis_client

    @property
    def messages(self) -> list[BaseMessage]:
        raise NotImplementedError("Use await get_messages() instead. Sync execution is blocked.")

    @messages.setter
    def messages(self, value: list[BaseMessage]) -> None:
        raise NotImplementedError("Use await aadd_messages() instead. Sync execution is blocked.")

    def add_message(self, message: BaseMessage) -> None:
        raise NotImplementedError("Use await aadd_message() instead. Sync execution is blocked.")

    def clear(self) -> None:
        raise NotImplementedError("Use await aclear() instead. Sync execution is blocked.")

    async def aget_messages(self) -> list[BaseMessage]:
        redis = await self._get_client()
        items = await redis.lrange(self.key, 0, -1)
        
        parsed_messages = []
        for item in items:
            try:
                parsed_messages.append(json.loads(item))
            except json.JSONDecodeError:
                continue
                
        return messages_from_dict(parsed_messages)

    async def aadd_messages(self, messages: Sequence[BaseMessage]) -> None:
        redis = await self._get_client()
        
        # Serialize messages
        serialized = [json.dumps(message_to_dict(m)) for m in messages]
        
        # Push to redis and then immediately LTRIM
        # Pipeline ensures this happens efficiently
        async with redis.pipeline(transaction=True) as pipe:
            await pipe.rpush(self.key, *serialized)
            # Keep only the last max_messages
            # LTRIM -N -1 keeps the last N elements
            await pipe.ltrim(self.key, -self.max_messages, -1)
            # Extend TTL (e.g. 30 days)
            await pipe.expire(self.key, 60 * 60 * 24 * 30)
            await pipe.execute()

    async def aadd_message(self, message: BaseMessage) -> None:
        await self.aadd_messages([message])

    async def aclear(self) -> None:
        redis = await self._get_client()
        await redis.delete(self.key)
