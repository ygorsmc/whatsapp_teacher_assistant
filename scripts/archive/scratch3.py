import asyncio
from sqlalchemy import text
from src.db.database import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("""
            SELECT s.name, s.phone, s.telegram_chat_id, c.name as class_name 
            FROM "Students" s
            JOIN "StudentEnrollment" se ON s.id = se.student_id
            JOIN "Classes" c ON c.id = se.class_id
            WHERE c.name ILIKE '%101%' AND s.is_active = true
        """))
        for row in result.fetchall():
            print(f"Name: {row[0]}, Phone: {row[1]}, Telegram: {row[2]}, Class: {row[3]}")

asyncio.run(main())
