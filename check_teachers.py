import asyncio
from src.db.database import AsyncSessionLocal
from src.db.models import Teacher
from sqlalchemy import select

async def main():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Teacher))
        teachers = result.scalars().all()
        print(f"Total teachers: {len(teachers)}")
        for t in teachers:
            print(f"- {t.name}: {t.phone} (Active: {t.is_active})")

if __name__ == "__main__":
    asyncio.run(main())
