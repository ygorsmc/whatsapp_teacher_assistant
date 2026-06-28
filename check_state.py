import asyncio
from sqlalchemy import text
from src.db.database import AsyncSessionLocal

async def main():
    async with AsyncSessionLocal() as session:
        # Estado da turma
        result = await session.execute(text("""
            SELECT id, name, current_lesson_number, last_advanced_date, start_date, schedule_map, course_id, teacher_id
            FROM "Classes" WHERE id = 'cls_ff52be93'
        """))
        cls = result.fetchone()
        print("=== TURMA ===")
        print(dict(cls._mapping))
        
        # Alunos matriculados
        result = await session.execute(text("""
            SELECT s.id, s.name, s.phone, s.telegram_chat_id, s.preferred_channel, s.is_active
            FROM "Students" s
            JOIN "StudentEnrollment" e ON s.id = e.student_id
            WHERE e.class_id = 'cls_ff52be93'
        """))
        print("\n=== ALUNOS MATRICULADOS ===")
        for row in result:
            print(dict(row._mapping))
        
        # PendingQuizzes
        result = await session.execute(text("""
            SELECT COUNT(*) as cnt FROM "PendingQuizzes"
        """))
        print(f"\n=== PENDING QUIZZES: {result.scalar()} ===")

asyncio.run(main())
