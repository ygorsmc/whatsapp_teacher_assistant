"""
Vocabulário com SRS (F2) — flashcards de palavra/expressão na língua-alvo.

Uso canônico do motor que já temos: `generate_review(material_type='vocab')` gera
(ou recupera do QuizBank, custo $0) um flashcard — palavra-alvo + significado em L1
+ roteiro de áudio da palavra. A entrega usa o sender universal
(`send_quiz_to_student`), e RESPONDER o quiz inscreve o item na
`SpacedRepetitionQueue` (D+3/10/30, SM-2) automaticamente — a Repetição Espaçada
vem de graça.

O formato do flashcard é FIXO (independe do nível), o que o torna cacheável e
compartilhável pela turma. Tudo atrás de `VOCABULARY_SRS_ENABLED` +
`LANGUAGE_MODE_ENABLED` (off por padrão).
"""

import structlog

from src.shared.config import settings
from src.shared.language import detect_language_course

logger = structlog.get_logger(__name__)


def vocab_enabled() -> bool:
    """Recurso de vocabulário ligado (mestre da vertical + submodo)."""
    return bool(settings.LANGUAGE_MODE_ENABLED and settings.VOCABULARY_SRS_ENABLED)


async def deliver_vocab_flashcard(
    channel,
    sender_id: str,
    *,
    teacher_id: str,
    student_id: str,
    student_name: str,
    course_id: str,
    course_name: str,
    subject_name: str | None,
    subject_id: str | None,
    section: int,
    section_label: str,
    material_type: str = "lesson",
) -> bool:
    """Gera e envia 1 flashcard de vocabulário (quiz + áudio da palavra-alvo).

    Retorna True se entregou. Cache Hit no QuizBank = $0; responder = entra no SRS.
    O áudio é best-effort (TTS off/erro ⇒ só o flashcard de texto).
    """
    from src.rag.review_generator import generate_review, synthesize_listening
    from src.rag.quiz_generator import QuizOutput, QuizAlternative
    from src.workers.micro_learning import send_quiz_to_student

    review = await generate_review(
        teacher_id=teacher_id,
        teacher_name="Professor",
        teacher_subject="Idiomas",
        student_id=student_id,
        student_name=student_name,
        course_id=course_id,
        course_name=course_name,
        subject_name=subject_name,
        subject_id=subject_id,
        current_lesson_number=section,
        lesson_label=section_label,
        from_bank=True,
        material_type="vocab",
        # vocab não é indexado: a RECUPERAÇÃO lê o material real da seção.
        retrieval_material_type=material_type or "lesson",
    )
    if not review:
        return False

    quiz_output = QuizOutput(
        question=review.quiz.question,
        alternatives=[QuizAlternative(id=a.id, text=a.text) for a in review.quiz.alternatives],
        correct_answer=review.quiz.correct_answer,
        explanation=review.quiz.explanation,
    )
    await send_quiz_to_student(
        channel=channel,
        student={
            "id": student_id,
            "sender_id": sender_id,
            "course_id": course_id,
            "lesson_pointer": str(section),
        },
        quiz=quiz_output,
        quiz_bank_id=review.quiz_bank_id,  # vínculo que inscreve no SRS na resposta
    )

    # Áudio da palavra-alvo + exemplo (best-effort).
    if review.audio_script:
        spec = detect_language_course(course_name, default_target=settings.DEFAULT_TARGET_LANGUAGE)
        target_lang = spec.target_language if spec else settings.DEFAULT_TARGET_LANGUAGE
        try:
            audio = await synthesize_listening(review.audio_script, target_lang)
            if audio:
                # Transcrição logo abaixo da nota de voz (flashcard de pronúncia —
                # ver a palavra/exemplo é o objetivo, não atrapalha a prática).
                await channel.send_audio(sender_id, audio, caption=review.audio_script)
        except Exception as e:
            logger.warning("vocab_audio_failed", student_id=student_id, error=str(e)[:200])

    logger.info("vocab_flashcard_sent", student_id=student_id, course_id=course_id, section=section)
    return True
