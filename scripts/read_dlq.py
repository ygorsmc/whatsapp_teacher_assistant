import asyncio
from azure.servicebus.aio import ServiceBusClient
from azure.identity.aio import DefaultAzureCredential
import json

async def main():
    credential = DefaultAzureCredential()
    client = ServiceBusClient("sb-wa-bdn4p6wsfy.servicebus.windows.net", credential=credential)
    
    async with client:
        receiver = client.get_queue_receiver(queue_name="inbound_messages", sub_queue="deadletter")
        async with receiver:
            msgs = await receiver.receive_messages(max_message_count=3, max_wait_time=5)
            for msg in msgs:
                print(f"DeadLetterReason: {msg.dead_letter_reason}")
                print(f"DeadLetterErrorDescription: {msg.dead_letter_error_description}")
                print("-" * 40)

if __name__ == "__main__":
    asyncio.run(main())
