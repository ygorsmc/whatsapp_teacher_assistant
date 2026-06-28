import asyncio
from sqlalchemy import text
from src.db.database import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("""
            SELECT id, name, current_lesson_number, last_advanced_date, start_date, schedule_map
            FROM "Classes" WHERE id = 'cls_ff52be93'
        """))
        row = result.fetchone()
        d = dict(row._mapping)
        print(f"lesson={d['current_lesson_number']}, last_adv={d['last_advanced_date']}")
        print(f"start_date={d['start_date']}, schedule_map={d['schedule_map']}")

asyncio.run(main())
