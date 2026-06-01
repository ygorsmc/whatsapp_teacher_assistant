import asyncio
from azure.servicebus.aio import ServiceBusClient

async def check():
    connection_str = "Endpoint=sb://127.0.0.1:5672;SharedAccessKeyName=RootManageSharedAccessKey;SharedAccessKey=12345;UseDevelopmentEmulator=true"
    client = ServiceBusClient.from_connection_string(connection_str)
    
    async with client:
        receiver = client.get_queue_receiver(queue_name="inbound_messages", max_wait_time=2)
        async with receiver:
            msgs = await receiver.receive_messages(max_message_count=10)
            for msg in msgs:
                print(b"".join(msg.body).decode("utf-8"))

if __name__ == "__main__":
    asyncio.run(check())
