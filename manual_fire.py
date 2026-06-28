"""Dispara manualmente o advance_classes_pointer e mostra o que acontece."""
import asyncio
import structlog
from src.workers.scheduler_worker import advance_classes_pointer
from sqlalchemy import text
from src.db.database import AsyncSessionLocal

logger = structlog.get_logger()

async def main():
    # Mostra estado antes
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("""
            SELECT current_lesson_number, last_advanced_date, schedule_map
            FROM "Classes" WHERE id = 'cls_ff52be93'
        """))
        row = result.fetchone()
        print(f"ANTES: lesson={row.current_lesson_number}, last_adv={row.last_advanced_date}")
    
    # Roda a função
    print("\n--- Rodando advance_classes_pointer() ---")
    await advance_classes_pointer()
    
    # Mostra estado depois
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("""
            SELECT current_lesson_number, last_advanced_date, schedule_map
            FROM "Classes" WHERE id = 'cls_ff52be93'
        """))
        row = result.fetchone()
        print(f"\nDEPOIS: lesson={row.current_lesson_number}, last_adv={row.last_advanced_date}")

asyncio.run(main())
