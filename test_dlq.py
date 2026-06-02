import asyncio
from azure.servicebus.aio import ServiceBusClient
from azure.identity.aio import DefaultAzureCredential

async def main():
    credential = DefaultAzureCredential()
    client = ServiceBusClient("sb-wa-bdn4p6wsfy.servicebus.windows.net", credential=credential)
    async with client:
        receiver = client.get_queue_receiver(queue_name="inbound_messages", sub_queue="deadletter")
        async with receiver:
            msgs = await receiver.receive_messages(max_message_count=10, max_wait_time=5)
            for msg in msgs:
                print(f"Reason: {msg.dead_letter_reason}, Desc: {msg.dead_letter_error_description}")

asyncio.run(main())
