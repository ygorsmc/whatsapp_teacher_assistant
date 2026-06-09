import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from src.shared.config import settings

async def fix():
    engine = create_async_engine(settings.DATABASE_URL)
    async with engine.begin() as conn:
        try:
            await conn.execute(text('ALTER TABLE "Teachers" ADD COLUMN is_admin BOOLEAN DEFAULT FALSE'))
            print("Added is_admin to Teachers")
        except Exception as e:
            print("is_admin error:", e)
        
        try:
            await conn.execute(text('ALTER TABLE "Students" ADD COLUMN preferred_channel VARCHAR(255) DEFAULT \'whatsapp\''))
            print("Added preferred_channel to Students")
        except Exception as e:
            print("preferred_channel error:", e)
            
        try:
            await conn.execute(text('ALTER TABLE "Classes" ADD COLUMN subject_id VARCHAR(255)'))
            print("Added subject_id to Classes")
        except Exception as e:
            print("subject_id error:", e)
            
asyncio.run(fix())
