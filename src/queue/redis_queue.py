import os
import json
import asyncio
import structlog
import uuid
from typing import Callable, Awaitable
import redis.asyncio as redis
from redis.exceptions import ResponseError, ConnectionError, TimeoutError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from src.shared.config import settings
from src.shared.telemetry import track_event
from src.queue.interfaces import IMessageQueue, MessageContext

logger = structlog.get_logger(__name__)

class RedisStreamContext(MessageContext):
    def __init__(self, client: redis.Redis, queue: str, group: str, message_id: bytes):
        self.client = client
        self.queue = queue
        self.group = group
        self.message_id = message_id

    async def complete(self):
        await self.client.xack(self.queue, self.group, self.message_id)
        await self.client.xdel(self.queue, self.message_id)

    async def dead_letter(self, reason: str, description: str):
        # In Redis Streams, dead letters can be moved to a specific dead-letter stream
        dlq_name = f"{self.queue}_dlq"
        # Read the message to copy it
        events = await self.client.xrange(self.queue, self.message_id, self.message_id)
        if events:
            _, fields = events[0]
            fields[b"error_reason"] = reason.encode()
            fields[b"error_description"] = description[:500].encode()
            await self.client.xadd(dlq_name, fields)
            
        await self.complete() # Acknowledge from main queue

    async def renew_lock(self):
        # Redis streams PEL doesn't expire implicitly, so no renewal is needed
        pass


class RedisStreamsQueue(IMessageQueue):
    def __init__(self):
        self.client = redis.from_url(
            settings.REDIS_URL, 
            decode_responses=False,
            socket_timeout=5.0,
            socket_connect_timeout=5.0,
            retry_on_timeout=True,
            health_check_interval=30
        )
        self.group_name = "messaging_workers"
        self.consumer_name = os.getenv("HOSTNAME") or f"worker_{uuid.uuid4().hex[:8]}"

    async def _ensure_group(self, queue: str):
        try:
            await self.client.xgroup_create(queue, self.group_name, id="0", mkstream=True)
        except ResponseError as e:
            if "BUSYGROUP Consumer Group name already exists" not in str(e):
                raise

    async def _reap_pending(self, queue: str, handler: Callable[[dict, MessageContext], Awaitable[None]], min_idle_ms: int = 60000):
        """Reivindica mensagens presas na PEL de consumidores mortos e tenta reprocessar."""
        start_id = "0-0"
        while True:
            try:
                # XAUTOCLAIM transfere a posse das mensagens que estão pendentes há > min_idle_ms
                next_id, claimed, _ = await self.client.xautoclaim(
                    queue, self.group_name, self.consumer_name,
                    min_idle_time=min_idle_ms, start_id=start_id, count=10
                )
                for message_id, fields in claimed:
                    payload_bytes = fields.get(b"payload")
                    if not payload_bytes:
                        await self.client.xack(queue, self.group_name, message_id)
                        continue
                        
                    payload = json.loads(payload_bytes.decode('utf-8'))
                    ctx = RedisStreamContext(self.client, queue, self.group_name, message_id)
                    try:
                        await handler(payload, ctx)
                    except Exception as e:
                        # Nunca deixar mensagem órfã, dead-letter imediato no reap failure
                        await ctx.dead_letter("ReapFailure", str(e)[:500])
                        
                start_id = next_id
                if next_id == b"0-0":
                    break
            except Exception as e:
                logger.error("redis_reap_pending_error", error=str(e)[:500])
                break

    @retry(
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((ConnectionError, TimeoutError))
    )
    async def enqueue(self, queue: str, payload: dict, session_id: str | None = None):
        fields = {
            "payload": json.dumps(payload),
            "session_id": session_id or ""
        }
        await self.client.xadd(queue, fields)
            
        track_event("message_enqueued", {
            "queue": queue,
            "session_id": session_id,
            "correlation_id": payload.get("correlation_id")
        })

    async def consume_loop(self, queue: str, handler: Callable[[dict, MessageContext], Awaitable[None]], use_sessions: bool = False):
        await self._ensure_group(queue)
        logger.info("redis_consumer_started", queue=queue, consumer=self.consumer_name)
        
        while True:
            try:
                await self._reap_pending(queue, handler)
                
                # Block for up to 5 seconds
                streams = await self.client.xreadgroup(
                    self.group_name,
                    self.consumer_name,
                    {queue: ">"},
                    count=10,
                    block=5000
                )
                
                if not streams:
                    continue
                    
                for stream_name, messages in streams:
                    for message_id, fields in messages:
                        payload_bytes = fields.get(b"payload")
                        if not payload_bytes:
                            await self.client.xack(queue, self.group_name, message_id)
                            continue
                            
                        payload = json.loads(payload_bytes.decode('utf-8'))
                        ctx = RedisStreamContext(self.client, queue, self.group_name, message_id)
                        session_id = fields.get(b"session_id", b"").decode("utf-8")
                        
                        try:
                            if use_sessions and session_id:
                                # Bug 11 FIX: Lock distributed for FIFO by session
                                session_lock = self.client.lock(f"session_lock:{session_id}", timeout=120, sleep=0.5)
                                async with session_lock:
                                    await handler(payload, ctx)
                            else:
                                await handler(payload, ctx)
                        except Exception as e:
                            logger.error("redis_handler_error", error=str(e))
                            # Obriga o envio para DLQ e ACK da fila principal (Impede que vire mensagem órfã na PEL)
                            await ctx.dead_letter("HandlerError", str(e)[:500])
                            
            except Exception as e:
                err_str = str(e)
                if "Timeout" in err_str:
                    continue
                if "NOGROUP" in err_str:
                    logger.warning("redis_nogroup_detected", queue=queue, msg="Recreating consumer group...")
                    await self._ensure_group(queue)
                    continue
                logger.error("redis_consume_error", error=err_str[:500])
                await asyncio.sleep(5)

    async def close(self):
        await self.client.aclose()
