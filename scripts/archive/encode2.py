import base64
code = """
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from src.shared.config import settings

async def fix():
    print(f"Connecting to: {settings.DATABASE_URL}")
    engine = create_async_engine(settings.DATABASE_URL)
    async with engine.begin() as conn:
        try:
            await conn.execute(text('ALTER TABLE "Teachers" ADD COLUMN is_admin BOOLEAN DEFAULT FALSE'))
            print("is_admin added")
        except Exception as e:
            print("is_admin error:", type(e), str(e))
        try:
            await conn.execute(text('ALTER TABLE "Students" ADD COLUMN preferred_channel VARCHAR(255) DEFAULT \\'whatsapp\\''))
            print("preferred_channel added")
        except Exception as e:
            print("preferred_channel error:", type(e), str(e))
        try:
            await conn.execute(text('ALTER TABLE "Classes" ADD COLUMN subject_id VARCHAR(255)'))
            print("subject_id added")
        except Exception as e:
            print("subject_id error:", type(e), str(e))
    print("done")
asyncio.run(fix())
"""
print(base64.b64encode(code.encode()).decode())
