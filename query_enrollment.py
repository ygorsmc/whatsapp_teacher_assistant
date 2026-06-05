import asyncio
from sqlalchemy import text
from src.db.database import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as session:
        # Check student
        print("--- Students ---")
        res = await session.execute(text('SELECT name, phone, is_active FROM "Students"'))
        for r in res.fetchall():
            print(r)
        
        # Check enrollments
        print("\n--- Enrollments ---")
        res = await session.execute(text('SELECT student_id, class_id FROM "StudentEnrollment"'))
        for r in res.fetchall():
            print(r)
            
        print("\n--- Classes ---")
        res = await session.execute(text('SELECT id, course_id, name FROM "Classes"'))
        for r in res.fetchall():
            print(r)

asyncio.run(main())
