import asyncio
from src.drive.spreadsheet_sync import sync_users_from_sheet

async def main():
    stats = await sync_users_from_sheet()
    print("SYNC RESULT:", stats)

if __name__ == "__main__":
    asyncio.run(main())
