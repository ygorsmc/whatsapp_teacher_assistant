import asyncio
from sqlalchemy import text
from src.db.database import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as session:
        await session.execute(text("""
            UPDATE "Classes" 
            SET current_lesson_number = 0, last_advanced_date = NULL 
            WHERE id = 'cls_ff52be93'
        """))
        await session.commit()
        
        result = await session.execute(text("""
            SELECT id, name, current_lesson_number, last_advanced_date, start_date, schedule_map
            FROM "Classes" WHERE id = 'cls_ff52be93'
        """))
        for row in result:
            print(dict(row._mapping))

asyncio.run(main())
