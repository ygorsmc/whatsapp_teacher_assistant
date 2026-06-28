import asyncio
from sqlalchemy import text
from src.db.database import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("""
            SELECT id, name, course_id, start_date, schedule_map, current_lesson_number, last_advanced_date
            FROM "Classes" WHERE id = 'cls_ff52be93'
        """))
        for row in result:
            d = dict(row._mapping)
            print(d)

asyncio.run(main())
