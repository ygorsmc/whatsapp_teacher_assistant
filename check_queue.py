import asyncio
from azure.servicebus.aio import ServiceBusClient

async def check():
    connection_str = "Endpoint=sb://127.0.0.1:5672;SharedAccessKeyName=RootManageSharedAccessKey;SharedAccessKey=12345;UseDevelopmentEmulator=true"
    client = ServiceBusClient.from_connection_string(connection_str)
    
    async with client:
        # Check inbound_messages queue
        receiver = client.get_queue_receiver(queue_name="inbound_messages", max_wait_time=5)
        async with receiver:
            msgs = await receiver.receive_messages(max_message_count=10)
            print(f"inbound_messages: found {len(msgs)} stuck messages.")
            for msg in msgs:
                print(f" - {msg.body}")
                
        # Check command_messages queue
        cmd_receiver = client.get_queue_receiver(queue_name="command_messages", max_wait_time=5)
        async with cmd_receiver:
            cmds = await cmd_receiver.receive_messages(max_message_count=10)
            print(f"command_messages: found {len(cmds)} stuck messages.")

if __name__ == "__main__":
    asyncio.run(check())
