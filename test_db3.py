import asyncio
from sqlalchemy import text
from src.db.database import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'Students';"))
        for row in result:
            print(row[0])

asyncio.run(main())
