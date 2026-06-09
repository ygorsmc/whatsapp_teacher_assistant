import asyncio
from sqlalchemy import select
from src.db.database import AsyncSessionLocal
from src.db.models.user import Teacher, Student

async def main():
    async with AsyncSessionLocal() as session:
        t = await session.execute(select(Teacher))
        for teacher in t.scalars():
            print(f"Teacher: {teacher.name}, Phone: {teacher.phone}, Telegram: {teacher.telegram_id}")
            
        s = await session.execute(select(Student))
        for student in s.scalars():
            print(f"Student: {student.name}, Phone: {student.phone}, Telegram: {student.telegram_id}")

asyncio.run(main())
