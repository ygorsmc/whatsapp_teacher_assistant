import asyncio
from src.workers.scheduler_worker import process_pending_schedules

async def run():
    print("Forcing scheduler trigger...")
    await process_pending_schedules()
    print("Done!")

asyncio.run(run())
