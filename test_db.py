import asyncio
from src.db.database import AsyncSessionLocal
from sqlalchemy import text

async def main():
    async with AsyncSessionLocal() as session:
        print("--- STUDENTS ---")
        res = await session.execute(text("SELECT id, name, phone FROM \"Students\""))
        for row in res.fetchall():
            print(row)
            
        print("--- COURSES ---")
        res = await session.execute(text("SELECT id, name FROM \"Courses\""))
        for row in res.fetchall():
            print(row)
            
        print("--- SCHEDULES ---")
        res = await session.execute(text("SELECT schedule_id, student_id, course_id, preferred_hour FROM \"StudentSchedule\""))
        for row in res.fetchall():
            print(row)

asyncio.run(main())
