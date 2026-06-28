import asyncio
from sqlalchemy import text
from src.db.database import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT id, name, sender_id, channel_type FROM \"Students\" WHERE id = '00000000-0000-0000-0000-000000000003'"))
        for row in result:
            print(dict(row._mapping))

asyncio.run(main())
