import json
from uuid import uuid4
from datetime import datetime, timezone, timedelta
from src.db.database import AsyncSessionLocal
from sqlalchemy import text

from src.rag.quiz_generator import QuizOutput
from src.shared.constants.messages import (
    QUIZ_BODY_PREFIX,
    QUIZ_FOOTER_BUTTONS,
    QUIZ_FOOTER_LIST,
    QUIZ_LIST_BUTTON_TEXT,
    QUIZ_LIST_SECTION_TITLE,
)

async def send_quiz_to_student(channel, student: dict, quiz: QuizOutput) -> str:
    """Salva o quiz no banco e envia ao aluno via mensagem interativa.
    
    Retorna o quiz_id para rastreamento.
    """
    quiz_id = str(uuid4())[:8]  # ID curto para caber no reply_id do WhatsApp
    
    # Persiste no banco para validação futura
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""
                INSERT INTO "PendingQuizzes"
                    (quiz_id, student_id, course_id, lesson_id, question_text,
                     alternatives, correct_answer, explanation, expires_at, status)
                VALUES
                    (:quiz_id, :student_id, :course_id, :lesson_id, :question,
                     :alternatives, :correct_answer, :explanation, :expires_at, 'pending')
            """),
            {
                "quiz_id": quiz_id,
                "student_id": student["id"],
                "course_id": student["course_id"],
                "lesson_id": student["lesson_pointer"],
                "question": quiz.question,
                "alternatives": json.dumps([{"id": a.id, "text": a.text} for a in quiz.alternatives]),
                "correct_answer": quiz.correct_answer,
                "explanation": quiz.explanation,
                "expires_at": datetime.now(timezone.utc) + timedelta(hours=24),
            }
        )
        await session.commit()

    sender_id = student["sender_id"]

    # Prepare the body text with full alternatives included
    options_text = "\n".join([f"{alt.id}) {alt.text}" for alt in quiz.alternatives])
    full_body_text = f"{QUIZ_BODY_PREFIX}{quiz.question}\n\n{options_text}"

    # Regra: <=3 alternativas -> Reply Buttons, >3 -> List Message
    if len(quiz.alternatives) <= 3:
        buttons = [
            {"id": f"quiz_{quiz_id}_{alt.id}", "title": f"Alternativa {alt.id}"}
            for alt in quiz.alternatives
        ]
        await channel.send_buttons(
            user_id=sender_id,
            body=full_body_text,
            buttons=buttons,
            footer=QUIZ_FOOTER_BUTTONS,
        )
    else:
        rows = [
            {
                "id": f"quiz_{quiz_id}_{alt.id}",
                "title": f"Alternativa {alt.id}",
                "description": "Toque para selecionar",
            }
            for alt in quiz.alternatives
        ]
        await channel.send_list(
            user_id=sender_id,
            body=full_body_text,
            button_text=QUIZ_LIST_BUTTON_TEXT,
            sections=[{"title": QUIZ_LIST_SECTION_TITLE, "rows": rows}],
            footer=QUIZ_FOOTER_LIST,
        )

    return quiz_id
