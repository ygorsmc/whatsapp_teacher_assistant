import asyncio
from src.rag.retriever import retrieve_context
from src.db.database import AsyncSessionLocal
from sqlalchemy import text

async def test_rag():
    async with AsyncSessionLocal() as session:
        # Pega as informacoes do PIBEP
        res = await session.execute(text("""
            SELECT cl.course_id, t.id as teacher_id, subj.name as subject_name, cl.current_lesson_number
            FROM "Students" s
            JOIN "StudentEnrollment" se ON s.id = se.student_id
            JOIN "Classes" cl ON cl.id = se.class_id
            JOIN "Courses" c ON c.id = cl.course_id
            JOIN "Teachers" t ON t.id = c.teacher_id
            LEFT JOIN "Subjects" subj ON subj.id = cl.subject_id
            WHERE s.telegram_chat_id = '1234364994' AND subj.name = 'PIBEP'
        """))
        row = res.fetchone()
        
    print(f"Testing for course: {row.course_id}, teacher: {row.teacher_id}, subject: {row.subject_name}, lesson: {row.current_lesson_number}")
    
    rag_text = await retrieve_context(
        teacher_id=row.teacher_id,
        course_id=row.course_id,
        subject_name=row.subject_name,
        current_lesson_number=row.current_lesson_number,
        query="Quero fazer uma revisão sobre o PIBEP",
    )
    
    print("----- RAG RESULT -----")
    print(rag_text)

asyncio.run(test_rag())
