import asyncio
from sqlalchemy import text
from src.db.database import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT id, name FROM \"Courses\""))
        for row in result.fetchall():
            print(f"ID: {row.id} - Name: {row.name}")

asyncio.run(main())
