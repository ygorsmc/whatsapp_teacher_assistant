"""
Preparação para exame (F8 — TOEFL/IELTS).

Reusa a máquina do /simulado + ClassChallenges (cronometrado), mas sobre um banco
ESTILO-PROVA: questões `material_type='exam'` no QuizBank, geradas na língua-alvo e
ancoradas no material (Recusa Honesta). Vertical de alto ticket.

`ensure_exam_questions` semeia o banco estilo-prova sob demanda (1 LLM por questão,
dentro da janela 24h); reentregas no /simulado são Cache Hit ($0). Atrás de
`EXAM_PREP_ENABLED` + `LANGUAGE_MODE_ENABLED` + turma de idioma.
"""

import structlog

from src.shared.config import settings

logger = structlog.get_logger(__name__)

EXAM_MATERIAL_TYPE = "exam"


def exam_prep_enabled() -> bool:
    return bool(settings.LANGUAGE_MODE_ENABLED and settings.EXAM_PREP_ENABLED)


async def ensure_exam_questions(
    teacher_id: str,
    course_id: str,
    course_name: str,
    subject_name: str | None,
    subject_id: str | None,
    max_chapter: int,
    target_count: int = 5,
) -> int:
    """Garante até `target_count` questões estilo-prova no QuizBank (gera as que
    faltam, 1 LLM cada, cruzando capítulos). Retorna quantas existem ao final.
    Best-effort: capítulos sem material indexado são pulados."""
    from src.rag import quiz_bank
    from src.rag.review_generator import generate_review

    existing = await quiz_bank.list_simulado_questions(
        course_id=course_id, subject_id=subject_id, max_section=max_chapter,
        limit=target_count, material_type=EXAM_MATERIAL_TYPE,
    )
    generated = len(existing)
    chapter = 1
    while generated < target_count and chapter <= max_chapter:
        try:
            review = await generate_review(
                teacher_id=teacher_id,
                teacher_name="Examinador",
                teacher_subject="Idiomas",
                student_id="broadcast",
                student_name="Turma",
                course_id=course_id,
                course_name=course_name,
                subject_name=subject_name,
                current_lesson_number=chapter,
                lesson_label=f"Capítulo {chapter}",
                subject_id=subject_id,
                from_bank=False,            # broadcast: gera questão nova p/ a turma
                material_type=EXAM_MATERIAL_TYPE,
                retrieval_material_type="lesson",  # exam não é indexado: lê o material real
            )
            if review and review.quiz_bank_id:
                generated += 1
        except Exception as e:
            logger.warning("exam_seed_failed", chapter=chapter, error=str(e)[:200])
        chapter += 1

    logger.info("exam_bank_ensured", course_id=course_id, count=generated)
    return generated
