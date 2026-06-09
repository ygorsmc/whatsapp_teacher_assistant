import base64
code = """
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from src.shared.config import settings

async def fix():
    engine = create_async_engine(settings.DATABASE_URL)
    async with engine.begin() as conn:
        try:
            await conn.execute(text('ALTER TABLE "Teachers" ADD COLUMN is_admin BOOLEAN DEFAULT FALSE'))
        except Exception as e:
            pass
        try:
            await conn.execute(text('ALTER TABLE "Students" ADD COLUMN preferred_channel VARCHAR(255) DEFAULT \\'whatsapp\\''))
        except Exception as e:
            pass
        try:
            await conn.execute(text('ALTER TABLE "Classes" ADD COLUMN subject_id VARCHAR(255)'))
        except Exception as e:
            pass
    print("done")
asyncio.run(fix())
"""
print(base64.b64encode(code.encode()).decode())
