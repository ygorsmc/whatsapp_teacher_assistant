import asyncio
from src.db.database import Base, engine
import src.db.models

async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        print("All tables dropped")

if __name__ == "__main__":
    asyncio.run(main())
