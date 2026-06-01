from azure.servicebus.aio import ServiceBusClient
from azure.servicebus import ServiceBusMessage
from azure.identity.aio import DefaultAzureCredential
from src.shared.config import settings
import json
from src.shared.telemetry import track_event

class ServiceBusWrapper:
    def __init__(self):
        self.client = None
        self.credential = None

    async def get_client(self) -> ServiceBusClient:
        if not self.client:
            if settings.SERVICEBUS_CONNECTION_STRING:
                # Local dev only — plain AMQP (emulator doesn't support WebSockets)
                self.client = ServiceBusClient.from_connection_string(
                    conn_str=settings.SERVICEBUS_CONNECTION_STRING,
                    retry_total=10,
                    retry_backoff_factor=1,
                    retry_backoff_max=30
                )
            else:
                # Production Managed Identity
                self.credential = DefaultAzureCredential()
                self.client = ServiceBusClient(
                    fully_qualified_namespace=settings.SERVICEBUS_FQDN,
                    credential=self.credential
                )
        return self.client

    async def enqueue(self, queue: str, payload: dict, session_id: str = None):
        """
        Enqueues a message to Azure Service Bus.
        If session_id is provided, guarantees strict FIFO order per session (student).
        In dev mode (emulator), sessions are disabled.
        """
        import os
        is_dev = os.getenv("ENVIRONMENT", "development") in ("dev", "development")
        
        client = await self.get_client()
        sender = client.get_queue_sender(queue_name=queue)
        
        message_body = json.dumps(payload)
        # Emulator doesn't support sessions — strip session_id in dev
        effective_session_id = session_id if not is_dev else None
        message = ServiceBusMessage(message_body, session_id=effective_session_id)
        
        async with sender:
            await sender.send_messages(message)
            
        track_event("message_enqueued", {
            "queue": queue,
            "session_id": effective_session_id,
            "correlation_id": payload.get("correlation_id")
        })

    async def close(self):
        if self.client:
            await self.client.close()
        if self.credential:
            await self.credential.close()

# Singleton instance
service_bus = ServiceBusWrapper()
