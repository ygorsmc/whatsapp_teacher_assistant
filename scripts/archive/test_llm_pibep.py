import asyncio
from src.llm.factory import get_provider
from src.rag.prompt_builder import build_system_prompt
from src.db.database import AsyncSessionLocal
from sqlalchemy import text
from src.rag.retriever import retrieve_context

async def main():
    async with AsyncSessionLocal() as session:
        # Pega as informacoes do PIBEP
        res = await session.execute(text("""
            SELECT cl.course_id, c.name as course_name, t.id as teacher_id, t.name as teacher_name, t.subject as teacher_subject, t.assistant_persona, t.out_of_scope_message, subj.name as subject_name, cl.current_lesson_number
            FROM "Students" s
            JOIN "StudentEnrollment" se ON s.id = se.student_id
            JOIN "Classes" cl ON cl.id = se.class_id
            JOIN "Courses" c ON c.id = cl.course_id
            JOIN "Teachers" t ON t.id = c.teacher_id
            LEFT JOIN "Subjects" subj ON subj.id = cl.subject_id
            WHERE s.telegram_chat_id = '1234364994' AND subj.name = 'PIBEP'
        """))
        row = res.fetchone()

    class DummyTeacher:
        name = row.teacher_name
        subject = row.teacher_subject
        assistant_persona = row.assistant_persona
        out_of_scope_message = row.out_of_scope_message

    class DummyStudent:
        name = "Ygor"

    rag_text = await retrieve_context(
        teacher_id=row.teacher_id,
        course_id=row.course_id,
        subject_name=row.subject_name,
        current_lesson_number=row.current_lesson_number,
        query="Quero fazer uma revisão sobre o PIBEP",
    )

    sys_prompt = await build_system_prompt(
        teacher=DummyTeacher(),
        student=DummyStudent(),
        course_id=row.course_id,
        course_name=row.course_name,
        current_lesson_id=str(row.current_lesson_number),
        subject_name=row.subject_name
    )
    
    sys_prompt += f"\n\nCONTEXTO RECUPERADO DOS MATERIAIS DA AULA:\n{rag_text}"
    
    print("----- SYSTEM PROMPT -----")
    print(sys_prompt)
    
    llm = get_provider()
    response = await llm.agenerate(
        prompt="Quero fazer uma revisão sobre o PIBEP",
        system_message=sys_prompt,
        history=[]
    )
    
    print("\n----- RESPONSE -----")
    print(response)

asyncio.run(main())
