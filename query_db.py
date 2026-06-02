import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from src.shared.config import settings

async def main():
    engine = create_async_engine(settings.DATABASE_URL)
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT c.name, cc.current_lesson_number FROM Courses c JOIN Classes cc ON c.id = cc.course_id"))
        for row in result:
            print(row)
            
asyncio.run(main())
