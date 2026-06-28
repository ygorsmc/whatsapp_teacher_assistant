import asyncio
from sqlalchemy import text
from src.db.database import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT id, course_id, name FROM \"Subjects\""))
        for row in result.fetchall():
            print(f"ID: {row.id} - Course: {row.course_id} - Name: {row.name}")

asyncio.run(main())
