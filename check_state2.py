import asyncio
from sqlalchemy import text
from src.db.database import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("""
            SELECT id, name, current_lesson_number, last_advanced_date, start_date, schedule_map
            FROM "Classes"
        """))
        for row in result:
            d = dict(row._mapping)
            print(d)
            print()

asyncio.run(main())
