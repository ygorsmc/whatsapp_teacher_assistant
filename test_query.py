import asyncio
from src.db.database import init_db, AsyncSessionLocal
import src.db.models.messaging  # IMPORT MODELS!
from src.drive.spreadsheet_sync import sync_users_from_sheet
from sqlalchemy import text

async def main():
    # 1. Initialize DB
    await init_db()
    
    # 2. Run sync
    print("Sincronizando planilha...")
    stats = await sync_users_from_sheet()
    print("Status da Sincronização:", stats)
    
    # 3. Run the SQL query requested by the other AI
    query = """
    SELECT ss.preferred_hour, ss.status, ss.is_active,
           ss.next_trigger_date, ss.last_sent_at,
           s.name, s.telegram_chat_id, s.is_active AS s_active,
           c.is_active AS c_active
    FROM "StudentSchedule" ss
    JOIN "Students" s ON s.id = ss.student_id
    JOIN "Courses"  c ON c.id = ss.course_id;
    """
    
    print("\nExecutando a query 'por desencargo de consciência':")
    async with AsyncSessionLocal() as session:
        result = await session.execute(text(query))
        rows = result.fetchall()
        
        print(f"Total de linhas retornadas: {len(rows)}")
        for row in rows:
            print(row)

if __name__ == "__main__":
    asyncio.run(main())
