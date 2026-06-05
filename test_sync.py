import asyncio
from src.drive.spreadsheet_sync import sync_users_from_sheet
import structlog
structlog.configure(
    processors=[
        structlog.processors.JSONRenderer()
    ]
)

async def main():
    try:
        stats = await sync_users_from_sheet()
        print(stats)
    except Exception as e:
        import traceback
        traceback.print_exc()

asyncio.run(main())
