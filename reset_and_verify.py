import asyncio
from sqlalchemy import text
from src.db.database import AsyncSessionLocal
from datetime import datetime, timedelta, timezone

async def main():
    brt = timezone(timedelta(hours=-3))
    now_brt = datetime.now(brt)
    print(f"Hora atual BRT: {now_brt.strftime('%H:%M:%S')}")
    
    async with AsyncSessionLocal() as session:
        # Reset turma
        await session.execute(text("""
            UPDATE "Classes" 
            SET current_lesson_number = 0, last_advanced_date = NULL 
            WHERE id = 'cls_ff52be93'
        """))
        await session.commit()
        
        # Verifica
        result = await session.execute(text("""
            SELECT id, name, current_lesson_number, last_advanced_date, start_date, schedule_map
            FROM "Classes" WHERE id = 'cls_ff52be93'
        """))
        row = result.fetchone()
        d = dict(row._mapping)
        print(f"current_lesson_number: {d['current_lesson_number']}")
        print(f"last_advanced_date: {d['last_advanced_date']}")
        print(f"schedule_map: {d['schedule_map']}")
        print(f"\nColoque o horário da aula na planilha para: {(now_brt + timedelta(minutes=7)).strftime('%H:%M')}")
        print(f"Rode /sinc_alunos e espere. O scheduler bate a cada 5 minutos.")

asyncio.run(main())
