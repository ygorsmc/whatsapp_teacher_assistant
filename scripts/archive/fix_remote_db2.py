import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DATABASE_URL = "postgresql+asyncpg://pgadmin:phrlN63zr5vZMCobPnVwkw==@whatsapp-bot-pg-bdn4p6wsfyc3i.postgres.database.azure.com:5432/whatsapp_assistant?ssl=require"

async def fix():
    print(f"Connecting to remote DB...")
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as conn:
        try:
            await conn.execute(text('ALTER TABLE "Classes" ADD COLUMN subject_id VARCHAR(255)'))
            print("subject_id added")
        except Exception as e:
            print("subject_id error:", type(e), str(e))
            
    print("done")

asyncio.run(fix())
