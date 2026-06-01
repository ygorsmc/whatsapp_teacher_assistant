import asyncio
from src.workers.llm_worker import main_worker, process_inbound
from src.shared.config import settings

async def debug():
    print("Testing inbound for Olá...")
    phone = "5513991719595"
    text = "Olá"
    correlation_id = "debug-123"
    
    try:
        await process_inbound(phone, text, correlation_id)
        print("Success processing inbound!")
    except Exception as e:
        import traceback
        traceback.print_exc()

asyncio.run(debug())
