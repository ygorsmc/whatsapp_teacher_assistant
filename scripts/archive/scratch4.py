import asyncio
from sqlalchemy import text
from src.db.database import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("""
            SELECT name, phone, is_active FROM "Students" WHERE name ILIKE '%Lindete%'
        """))
        for row in result.fetchall():
            print(f"Name: {row[0]}, Phone: {row[1]}, is_active: {row[2]}")

asyncio.run(main())
