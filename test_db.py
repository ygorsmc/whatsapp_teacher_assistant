import asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

async def main():
    url = "postgresql+asyncpg://admin_whatsapp:w%40tsapp_admin2026@wa-db-server.postgres.database.azure.com:5432/whatsapp_assistant?ssl=require"
    engine = create_async_engine(url)
    async with AsyncSession(engine) as session:
        print("--- STUDENTS ---")
        res = await session.execute(text('SELECT id, name, phone, telegram_chat_id, is_active FROM "Students" WHERE telegram_chat_id IS NOT NULL LIMIT 5'))
        for r in res.fetchall():
            print(r)
            
        print("\n--- ENROLLMENTS ---")
        res = await session.execute(text('SELECT student_id, class_id FROM "StudentEnrollment" LIMIT 5'))
        for r in res.fetchall():
            print(r)
            
        print("\n--- COURSES ---")
        res = await session.execute(text('SELECT id, name FROM "Courses" LIMIT 5'))
        for r in res.fetchall():
            print(r)
            
        print("\n--- CLASSES ---")
        res = await session.execute(text('SELECT id, course_id, name FROM "Classes" LIMIT 5'))
        for r in res.fetchall():
            print(r)

asyncio.run(main())
