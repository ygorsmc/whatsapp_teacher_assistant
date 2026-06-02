import asyncio
import os
from azure.servicebus.aio import ServiceBusClient

async def main():
    conn_str = "Endpoint=sb://whatsapp-assistant-bus-prod.servicebus.windows.net/;SharedAccessKeyName=RootManageSharedAccessKey;SharedAccessKey=YOUR_KEY"
    # Wait, we use Managed Identity in production!
    from azure.identity.aio import DefaultAzureCredential
    
    sb_namespace = os.popen('az servicebus namespace list -g rg-whatsapp-assistant-prod --query "[0].name" -o tsv').read().strip()
    credential = DefaultAzureCredential()
    client = ServiceBusClient(f"{sb_namespace}.servicebus.windows.net", credential=credential)
    
    async with client:
        receiver = client.get_queue_receiver(queue_name="inbound_messages", sub_queue="deadletter")
        async with receiver:
            messages = await receiver.receive_messages(max_message_count=3, max_wait_time=5)
            for msg in messages:
                print(f"Dead letter reason: {msg.dead_letter_reason}")
                print(f"Dead letter desc: {msg.dead_letter_error_description}")
                print(f"Body: {b''.join(msg.body).decode()}")
                print("-" * 40)

if __name__ == "__main__":
    asyncio.run(main())
