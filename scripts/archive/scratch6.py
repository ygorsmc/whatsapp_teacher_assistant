import asyncio
from sqlalchemy import text
from src.db.database import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text('SELECT id, name, phone, is_active FROM "Students"'))
        print("ALL STUDENTS:")
        for row in result.fetchall():
            print(f"ID: {row[0]}, Name: {row[1]}, Phone: {row[2]}, Active: {row[3]}")
            
        result = await session.execute(text('SELECT student_id, class_id FROM "StudentEnrollment"'))
        print("\nENROLLMENTS:")
        for row in result.fetchall():
            print(f"Student: {row[0]}, Class: {row[1]}")
            
        result = await session.execute(text('SELECT id, name FROM "Classes"'))
        print("\nCLASSES:")
        for row in result.fetchall():
            print(f"ID: {row[0]}, Class Name: {row[1]}")

asyncio.run(main())
