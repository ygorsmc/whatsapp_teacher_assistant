import json
import uuid
import structlog
from datetime import datetime, timezone, timedelta
from sqlalchemy import text

from src.db.database import AsyncSessionLocal
from src.rag.review_generator import (
    get_all_lesson_topics,
    generate_review,
    generate_language_review,
    class_language_context,
)
from src.workers.micro_learning import deliver_next_pending_quiz
from src.shared.constants.messages import CLASS_REVIEW_SEPARATOR

logger = structlog.get_logger(__name__)

async def handle_class_session_start(payload: dict):
    """
    Consome o evento de Sessão Mista de uma aula.
    Gera as variações de quizzes de TODOS os tópicos daquela aula,
    insere na PendingQuizzes (com delay de segundos no created_at) e 
    dispara o envio da PRIMEIRA questão para todos os alunos.
    """
    class_id = payload["class_id"]
    course_id = payload["course_id"]
    lesson_number = payload["lesson_number"]
    
    async with AsyncSessionLocal() as session:
        # Pega a turma e informações do professor/disciplina
        result = await session.execute(
            text("""
                SELECT c.name as class_name, c.teacher_id, c.subject_id,
                       t.name as teacher_name, sub.name as subject_name,
                       co.name as course_name, c.is_language, c.proficiency_level
                FROM "Classes" c
                LEFT JOIN "Teachers" t ON c.teacher_id = t.id
                LEFT JOIN "Subjects" sub ON c.subject_id = sub.id
                LEFT JOIN "Courses" co ON c.course_id = co.id
                WHERE c.id = :class_id
            """),
            {"class_id": class_id}
        )
        cls_row = result.fetchone()
        if not cls_row:
            return

    # Turma de IDIOMA: no avanço NÃO fazemos a Sessão Mista packed (fluxo do
    # PIBEP). Apenas AQUECEMOS o QuizBank com o pacote de idioma da aula — o
    # MESMO banco que a revisão diária relê e a Revisão Espaçada reentrega. A
    # entrega fica a cargo da revisão diária; from_bank=False gera+salva uma vez.
    delivery, target_language = class_language_context(
        cls_row.course_name, cls_row.is_language, cls_row.proficiency_level
    )
    if delivery is not None and target_language is not None:
        try:
            review = await generate_language_review(
                teacher_id=cls_row.teacher_id,
                student_id="broadcast",
                student_name="Turma",
                course_id=course_id,
                course_name=cls_row.course_name,
                subject_name=cls_row.subject_name,
                current_lesson_number=lesson_number,
                lesson_label=f"Aula {lesson_number}",
                delivery=delivery,
                target_language=target_language,
                subject_id=cls_row.subject_id,
                from_bank=False,
            )
            logger.info(
                "class_session_language_bank_warmed",
                class_id=class_id, lesson_number=lesson_number,
                quiz_bank_id=getattr(review, "quiz_bank_id", None),
            )
        except Exception as e:
            logger.error("class_session_language_warm_failed", class_id=class_id, error=str(e)[:300])
        return

    async with AsyncSessionLocal() as session:

        # Pega os alunos matriculados ativos
        result = await session.execute(
            text("""
                SELECT s.id, s.name, s.phone, s.telegram_chat_id, s.preferred_channel
                FROM "Students" s
                JOIN "StudentEnrollment" e ON s.id = e.student_id
                WHERE e.class_id = :class_id AND s.is_active = true
            """),
            {"class_id": class_id}
        )
        students = result.fetchall()
        
    if not students:
        return

    logger.info("generating_class_session", class_id=class_id, lesson_number=lesson_number, students_count=len(students))
    
    try:
        topics = await get_all_lesson_topics(
            teacher_id=cls_row.teacher_id,
            course_id=course_id,
            subject_name=cls_row.subject_name,
            current_lesson_number=lesson_number,
            material_type="lesson"
        )
    except Exception as e:
        logger.error("class_session_topics_error", class_id=class_id, lesson=lesson_number, error=str(e)[:300])
        return

    if not topics:
        logger.warning("class_session_no_topics", class_id=class_id, lesson=lesson_number)
        return
        
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    
    # Envia para cada aluno
    for stu in students:
        generated_count = 0
        
        for idx, topic in enumerate(topics):
            # Passamos from_bank=True (mas se não tiver, ele gera o batch de 3 variações e salva no QuizBank)
            review = await generate_review(
                teacher_id=cls_row.teacher_id,
                teacher_name=cls_row.teacher_name,
                teacher_subject=cls_row.subject_name or "",
                student_id=stu.id,
                student_name=stu.name,
                course_id=course_id,
                course_name=cls_row.class_name,
                subject_name=cls_row.subject_name,
                current_lesson_number=lesson_number,
                lesson_label=f"Aula {lesson_number}",
                subject_id=cls_row.subject_id,
                from_bank=True,
                material_type="lesson",
                force_topic=topic
            )
            
            if review and review.quiz:
                quiz_id = str(uuid.uuid4())[:8]
                quiz_bank_id = review.quiz_bank_id
                
                # Persiste resumo (revisão) + pergunta juntos no question_text, mas
                # separados pelo CLASS_REVIEW_SEPARATOR: na ENTREGA, send_existing_quiz
                # divide por ele e manda a revisão como TEXTO e a pergunta no body
                # interativo (limite de 1024 chars do WhatsApp). Manter o marcador
                # "Revisão do Tópico" no texto preserva as heurísticas do llm_worker.
                question_with_review = f"📖 *Revisão do Tópico ({idx+1}/{len(topics)}):*\n{review.summary}{CLASS_REVIEW_SEPARATOR}{review.quiz.question}"
                
                async with AsyncSessionLocal() as session:
                    await session.execute(
                        text("""
                            INSERT INTO "PendingQuizzes"
                                (quiz_id, student_id, course_id, lesson_id, question_text,
                                 alternatives, correct_answer, explanation, expires_at, status,
                                 quiz_bank_id, created_at, updated_at)
                            VALUES
                                (:quiz_id, :student_id, :course_id, :lesson_id, :question,
                                 :alternatives, :correct_answer, :explanation, :expires_at, 'pending',
                                 :quiz_bank_id, :created_at, :created_at)
                        """),
                        {
                            "quiz_id": quiz_id,
                            "student_id": stu.id,
                            "course_id": course_id,
                            "lesson_id": str(lesson_number),
                            "question": question_with_review,
                            "alternatives": json.dumps([{"id": a.id, "text": a.text} for a in review.quiz.alternatives]),
                            "correct_answer": review.quiz.correct_answer,
                            "explanation": review.quiz.explanation,
                            "expires_at": now_utc + timedelta(hours=24),
                            "quiz_bank_id": quiz_bank_id,
                            "created_at": now_utc + timedelta(seconds=idx) # garante ordem dos tópicos!
                        }
                    )
                    await session.commit()
                generated_count += 1
                
        # Após enfileirar todos, avisa e manda o primeiro
        if generated_count > 0:
            try:
                ct = (stu.preferred_channel or "whatsapp").lower()
                sender_id = stu.telegram_chat_id if ct == "telegram" else stu.phone
                
                if not sender_id:
                    logger.warning("class_session_skip_no_sender_id", student_id=stu.id)
                    continue

                from src.channel.factory import get_channel
                channel = get_channel(ct)
                await channel.send_text(
                    sender_id, 
                    f"📚 *Iniciando a Aula {lesson_number}!* 📚\n\nResponda as questões abaixo para avançar no seu ritmo."
                )
                await deliver_next_pending_quiz(channel, sender_id, stu.id, course_id, str(lesson_number))
            except Exception as e:
                logger.error("error_sending_class_session_start", error=str(e)[:200])
