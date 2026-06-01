import asyncio
from src.db.database import AsyncSessionLocal
from sqlalchemy import text
async def test():
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(text('SELECT count(*) FROM Students'))
            print('Students count:', result.scalar())
    except Exception as e:
        print("ERROR:", e)

asyncio.run(test())
