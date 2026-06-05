import asyncio
from src.shared.config import settings
from src.drive.spreadsheet_sync import sync_users_from_sheet

async def main():
    print("Running sync...")
    stats = await sync_users_from_sheet()
    print("Stats:", stats)

if __name__ == "__main__":
    asyncio.run(main())
