import os
import json
import asyncio
import structlog
from typing import Callable, Awaitable
from azure.servicebus.aio import ServiceBusClient, ServiceBusReceiver
from azure.servicebus import ServiceBusMessage, NEXT_AVAILABLE_SESSION
from azure.servicebus.exceptions import OperationTimeoutError
from azure.identity.aio import DefaultAzureCredential
from src.shared.config import settings
from src.shared.telemetry import track_event
from src.queue.interfaces import IMessageQueue, MessageContext

logger = structlog.get_logger(__name__)

class ServiceBusContext(MessageContext):
    def __init__(self, receiver: ServiceBusReceiver, message):
        self.receiver = receiver
        self.message = message

    async def complete(self):
        await self.receiver.complete_message(self.message)

    async def dead_letter(self, reason: str, description: str):
        await self.receiver.dead_letter_message(self.message, reason=reason, error_description=description[:500])

    async def renew_lock(self):
        await self.receiver.renew_message_lock(self.message)

class AzureServiceBusQueue(IMessageQueue):
    def __init__(self):
        self.client = None
        self.credential = None
        self.senders = {}

    async def get_client(self) -> ServiceBusClient:
        if not self.client:
            if settings.SERVICEBUS_CONNECTION_STRING:
                logger.info("sb_using_connection_string")
                self.client = ServiceBusClient.from_connection_string(
                    conn_str=settings.SERVICEBUS_CONNECTION_STRING,
                    retry_total=10,
                    retry_backoff_factor=1,
                    retry_backoff_max=30,
                    keep_alive_interval=30
                )
            else:
                logger.info("sb_using_managed_identity", fqdn=settings.SERVICEBUS_FQDN)
                self.credential = DefaultAzureCredential()
                self.client = ServiceBusClient(
                    fully_qualified_namespace=settings.SERVICEBUS_FQDN,
                    credential=self.credential,
                    keep_alive_interval=30
                )
        return self.client

    async def enqueue(self, queue: str, payload: dict, session_id: str | None = None):
        is_dev = os.getenv("ENVIRONMENT", "development") in ("dev", "development")
        client = await self.get_client()
        
        if queue not in self.senders:
            self.senders[queue] = client.get_queue_sender(queue_name=queue)
        sender = self.senders[queue]
        
        message_body = json.dumps(payload)
        effective_session_id = session_id if not is_dev else None
        message = ServiceBusMessage(message_body, session_id=effective_session_id)
        
        await sender.send_messages(message)
            
        track_event("message_enqueued", {
            "queue": queue,
            "session_id": effective_session_id,
            "correlation_id": payload.get("correlation_id")
        })

    async def consume_loop(self, queue: str, handler: Callable[[dict, MessageContext], Awaitable[None]], use_sessions: bool = False):
        is_dev = os.getenv("ENVIRONMENT", "development") in ("dev", "development")
        client = await self.get_client()
        logger.info("service_bus_consumer_started", queue=queue, use_sessions=use_sessions, is_dev=is_dev)
        
        async with client:
            poll_count = 0
            while True:
                try:
                    poll_count += 1
                    if is_dev or not use_sessions:
                        receiver = client.get_queue_receiver(queue_name=queue, max_wait_time=10)
                        async with receiver:
                            messages = await receiver.receive_messages(max_message_count=10, max_wait_time=5)
                            for msg in messages:
                                ctx = ServiceBusContext(receiver, msg)
                                payload = json.loads(str(msg))
                                await handler(payload, ctx)
                    else:
                        # Production: session-enabled FIFO
                        receiver = client.get_queue_receiver(
                            queue_name=queue, 
                            session_id=NEXT_AVAILABLE_SESSION,
                            max_wait_time=10
                        )
                        try:
                            async with receiver:
                                messages = await receiver.receive_messages(max_message_count=10, max_wait_time=5)
                                for msg in messages:
                                    ctx = ServiceBusContext(receiver, msg)
                                    payload = json.loads(str(msg))
                                    await handler(payload, ctx)
                        except OperationTimeoutError:
                            pass
                            
                except OperationTimeoutError:
                    await asyncio.sleep(2)
                except Exception as e:
                    logger.error("queue_consume_error", error=str(e)[:500])
                    await asyncio.sleep(5)

    async def close(self):
        for sender in self.senders.values():
            await sender.close()
        self.senders.clear()
        if self.client:
            await self.client.close()
        if self.credential:
            await self.credential.close()
