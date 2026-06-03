import asyncio
import json
import structlog
from azure.servicebus.aio import ServiceBusClient, AutoLockRenewer
from src.shared.config import settings
from src.shared.telemetry import track_event, logger
from src.cache.debounce import drain_buffer
from src.shared.whatsapp_interactive import send_whatsapp_message, send_whatsapp_typing
from src.shared.constants.messages import (
    ERROR_INVALID_RESPONSE,
    ERROR_QUIZ_NOT_FOUND,
    ERROR_QUIZ_ANSWERED,
    FEEDBACK_CORRECT,
    FEEDBACK_INCORRECT,
    ERROR_NO_MATERIAL,
    REVIEW_LATER_ACK,
    ERROR_NOT_ENROLLED,
    ERROR_MESSAGE_TOO_LONG,
    ERROR_TECHNICAL,
    TEACHER_CHAT_REPLY,
)
from src.rag.review_generator import generate_review
from src.workers.micro_learning import send_quiz_to_student
from src.rag.quiz_generator import QuizOutput, QuizAlternative

# RAG pipeline with conversational memory
async def run_rag_pipeline(student_context: dict, phone: str, text: str) -> str:
    """Executes the LLM pipeline with Redis history and Qdrant RAG."""
    from src.llm.factory import get_provider
    from src.cache.session_history import OptimizedRedisChatMessageHistory
    from src.rag.retriever import retrieve_context
    from src.rag.prompt_builder import build_system_prompt
    from langchain_core.messages import HumanMessage, AIMessage
    
    llm = get_provider()
    
    # 1. Extract context variables
    teacher = student_context["teacher"]
    student = student_context["student"]
    course = student_context["course"]
    enrollment = student_context["enrollment"]
    
    # 2. Retrieve Document Context from Qdrant
    # For now, we assume the student only has access to their current lesson pointer
    
    rag_context = await retrieve_context(
        teacher_id=teacher.id,
        course_id=course.id,
        current_lesson_number=student_context["class_obj"].current_lesson_number,
        query=text
    )
    
    # 3. Build the System Prompt
    system_message = await build_system_prompt(
        teacher=teacher,
        student=student,
        course_id=course.id,
        course_name=course.name,
        current_lesson_id=str(student_context["class_obj"].current_lesson_number)
    )
    
    # Append the RAG Context to the System Message
    if rag_context:
        system_message += f"\n\nCONTEXTO RECUPERADO DOS MATERIAIS DA AULA:\n{rag_context}"
    else:
        system_message += "\n\n(Nenhum contexto específico encontrado nos materiais para esta pergunta.)"
    
    # 4. Load conversation history from Redis (last 40 messages)
    history = OptimizedRedisChatMessageHistory(session_id=phone)
    previous_messages = await history.aget_messages()
    
    # Call LLM with full conversational context
    response = await llm.agenerate(
        prompt=text,
        system_message=system_message,
        history=previous_messages
    )
    
    # Persist both the student's question and the AI's answer to Redis
    await history.aadd_messages([
        HumanMessage(content=text),
        AIMessage(content=response)
    ])
    
    return response

async def get_student_context_by_phone(phone: str, course_id: str | None = None):
    """Fetches real DB data: Student, Enrollment, CourseClass, Course, and Teacher.
    
    If course_id is provided, filters for the specific enrollment and returns a dict.
    Otherwise returns a list of all active enrollments.
    """
    from src.db.database import AsyncSessionLocal
    from src.db.models import Student, StudentEnrollment, CourseClass, Course, Teacher
    from sqlalchemy import select
    
    async with AsyncSessionLocal() as session:
        # Join tables to get the full context in one query
        stmt = (
            select(Student, StudentEnrollment, CourseClass, Course, Teacher)
            .join(StudentEnrollment, Student.id == StudentEnrollment.student_id)
            .join(CourseClass, StudentEnrollment.class_id == CourseClass.id)
            .join(Course, CourseClass.course_id == Course.id)
            .join(Teacher, Course.teacher_id == Teacher.id)
            .where(Student.phone == phone)
        )
        
        # Filter by specific course if provided (e.g., from review button)
        if course_id:
            stmt = stmt.where(Course.id == course_id)
            result = await session.execute(stmt)
            row = result.first()
            if not row:
                return None
            return {
                "student": row[0],
                "enrollment": row[1],
                "class_obj": row[2],
                "course": row[3],
                "teacher": row[4]
            }
        else:
            result = await session.execute(stmt)
            rows = result.all()
            if not rows:
                return []
            return [
                {
                    "student": r[0],
                    "enrollment": r[1],
                    "class_obj": r[2],
                    "course": r[3],
                    "teacher": r[4]
                }
                for r in rows
            ]

async def send_whatsapp_typing(phone: str):
    """Sends typing indicator."""
    pass

async def handle_quiz_response(
    phone: str,
    student_id: str,
    reply_id: str,
    interactive_payload: dict,
    message_id: str | None = None
) -> None:
    """Valida resposta do aluno a um quiz por comparação direta (if/else)."""
    # Parse do reply_id: "quiz_{quiz_id}_{answer_id}" → quiz_id, answer_id
    parts = reply_id.split("_", 2)
    if len(parts) < 3:
        await send_whatsapp_message(phone, ERROR_INVALID_RESPONSE, reply_to_message_id=message_id)
        return
    
    quiz_id = parts[1]
    answer_id = parts[2]

    # Busca o quiz pendente
    from src.db.database import AsyncSessionLocal
    from sqlalchemy import text
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
                SELECT quiz_id, correct_answer, explanation, question_text, status
                FROM PendingQuizzes
                WHERE quiz_id = :quiz_id AND student_id = :student_id
            """),
            {"quiz_id": quiz_id, "student_id": student_id}
        )
        quiz = result.fetchone()

        if not quiz:
            await send_whatsapp_message(phone, ERROR_QUIZ_NOT_FOUND, reply_to_message_id=message_id)
            return

        if quiz.status != "pending":
            await send_whatsapp_message(phone, ERROR_QUIZ_ANSWERED, reply_to_message_id=message_id)
            return

        # ── VALIDAÇÃO DETERMINÍSTICA — if/else puro, sem LLM ──
        is_correct = (answer_id == quiz.correct_answer)

        # Atualiza o registro do quiz
        await session.execute(
            text("""
                UPDATE PendingQuizzes
                SET student_answer = :answer, is_correct = :correct,
                    status = 'answered', answered_at = CURRENT_TIMESTAMP
                WHERE quiz_id = :quiz_id
            """),
            {"answer": answer_id, "correct": is_correct, "quiz_id": quiz_id}
        )
        await session.commit()

    # Monta feedback com emoji visual
    if is_correct:
        feedback = FEEDBACK_CORRECT.format(explanation=quiz.explanation)
    else:
        feedback = FEEDBACK_INCORRECT.format(correct_answer=quiz.correct_answer, explanation=quiz.explanation)

    await send_whatsapp_message(phone, feedback, reply_to_message_id=message_id)

    # Telemetria
    track_event("quiz_answered", {
        "student_id": student_id,
        "quiz_id": quiz_id,
        "is_correct": is_correct,
        "answer": answer_id,
    })

async def handle_review_request(phone: str, student_context: dict) -> None:
    """Generates and sends a review summary + quiz when student accepts the daily review.

    Flow:
        1. Extract context (teacher, student, course, enrollment)
        2. Generate review via review_generator (single LLM call)
        3. Send formatted summary to WhatsApp
        4. Send quiz via interactive buttons
    """
    teacher = student_context["teacher"]
    student = student_context["student"]
    course = student_context["course"]
    enrollment = student_context["enrollment"]
    current_lesson = student_context["class_obj"].current_lesson_number

    from src.rag.prompt_builder import resolve_lesson_label
    lesson_label = await resolve_lesson_label(course.id, str(current_lesson))

    # Generate review (summary + quiz) from indexed content
    review = await generate_review(
        teacher_id=teacher.id,
        teacher_name=teacher.name,
        teacher_subject=teacher.subject,
        student_name=student.name,
        course_id=course.id,
        course_name=course.name,
        current_lesson_number=current_lesson,
        lesson_label=lesson_label,
    )

    if not review:
        await send_whatsapp_message(phone, ERROR_NO_MATERIAL)
        return

    # Format and send the summary
    key_points_text = "\n".join(f"  • {kp}" for kp in review.key_points)
    summary_message = (
        f"📖 *Revisão — {course.name}*\n"
        f"📍 {lesson_label}\n\n"
        f"{review.summary}\n\n"
        f"🔑 *Pontos-chave:*\n{key_points_text}\n\n"
        f"_Leia com atenção — o quiz a seguir é baseado neste resumo!_ 👇"
    )
    await send_whatsapp_message(phone, summary_message)

    # Convert ReviewQuiz to QuizOutput for micro_learning compatibility
    quiz_output = QuizOutput(
        question=review.quiz.question,
        alternatives=[
            QuizAlternative(id=alt.id, text=alt.text)
            for alt in review.quiz.alternatives
        ],
        correct_answer=review.quiz.correct_answer,
        explanation=review.quiz.explanation,
    )

    # Send quiz via interactive buttons (uses existing micro_learning flow)
    quiz_id = await send_quiz_to_student(
        student={
            "id": student.id,
            "phone": phone,
            "course_id": course.id,
            "lesson_pointer": str(current_lesson),
        },
        quiz=quiz_output,
    )

    track_event("review_sent", {
        "student_id": student.id,
        "course_id": course.id,
        "lesson_id": current_lesson,
        "quiz_id": quiz_id,
    })

async def process_inbound(receiver, message):
    try:
        body_bytes = b"".join(message.body)
        event = json.loads(body_bytes.decode('utf-8'))
    except Exception as e:
        await receiver.dead_letter_message(message, reason="Invalid JSON", error_description=str(e)[:500])
        return

    phone = event.get("phone")
    text = event.get("text")
    message_type = event.get("message_type", "text")
    interactive_payload = event.get("interactive_payload")
    correlation_id = event.get("correlation_id", "unknown")
    message_id = event.get("message_id")
    
    with structlog.contextvars.bound_contextvars(correlation_id=correlation_id, phone=phone):
        student_contexts = await get_student_context_by_phone(phone)

        # Guard clause: student may have been deactivated or not enrolled
        if not student_contexts:
            from src.db.database import AsyncSessionLocal
            from src.db.models import Teacher
            from sqlalchemy import select
            
            # Check if this is a teacher
            async with AsyncSessionLocal() as session:
                result = await session.execute(select(Teacher).where(Teacher.phone == phone))
                if result.scalar_one_or_none():
                    await send_whatsapp_message(phone, TEACHER_CHAT_REPLY, reply_to_message_id=message_id)
                else:
                    await send_whatsapp_message(phone, ERROR_NOT_ENROLLED, reply_to_message_id=message_id)
                    
            await receiver.complete_message(message)
            return

        student_context = student_contexts[0]  # Default fallback context

        # Fast-path: resposta interativa de quiz (validação determinística)
        if message_type == "interactive" and interactive_payload:
            reply_id = interactive_payload.get("id", "")
            if reply_id.startswith("quiz_"):
                await handle_quiz_response(phone, student_context["student"].id, reply_id, interactive_payload, message_id=message_id)
                await receiver.complete_message(message)
                return

            # Fast-path: aluno aceita a revisão diária
            if reply_id.startswith("review_accept_"):
                try:
                    # Extract course_id from the button payload (review_accept_{course_id})
                    review_course_id = reply_id.replace("review_accept_", "", 1)
                    
                    logger.info(f"DEBUG_ROUTING: Extracted review_course_id: {review_course_id}")
                    
                    # Re-fetch context for the SPECIFIC course from the button
                    review_context = await get_student_context_by_phone(phone, course_id=review_course_id)
                    
                    if not review_context:
                        logger.info("DEBUG_ROUTING: review_context was None! Falling back to default.")
                        review_context = student_context  # fallback to default
                    else:
                        logger.info(f"DEBUG_ROUTING: review_context found! Course: {review_context['course'].name}")
                        
                    await handle_review_request(phone, review_context)
                except Exception as e:
                    import traceback
                    logger.error("Fast-path review_accept error", error=str(e), exc_info=True)
                    traceback.print_exc()
                    await send_whatsapp_message(phone, ERROR_TECHNICAL)
                finally:
                    await receiver.complete_message(message)
                return

            # Fast-path: aluno adia a revisão
            if reply_id.startswith("review_later_"):
                await send_whatsapp_message(phone, REVIEW_LATER_ACK)
                await receiver.complete_message(message)
                return

        # Debouncing no worker (movido do webhook)
        from src.cache.redis_client import get_redis
        redis = await get_redis()
        
        buffer_key = f"buffer:{phone}"
        user_key = f"debounce:{phone}"

        await redis.rpush(buffer_key, text)
        await redis.expire(buffer_key, 10)

        is_first = await redis.set(user_key, "1", nx=True, ex=settings.DEBOUNCE_TTL_SECONDS)
        if not is_first:
            # Another message is currently counting down the debounce window
            # We don't complete this message, it gets rolled into the drain
            await receiver.complete_message(message)
            return

        # Wait the debounce window
        await asyncio.sleep(settings.DEBOUNCE_TTL_SECONDS)

        # Drain the buffer atomically
        messages = await drain_buffer(phone, cooldown=1)
        combined = " ".join(messages)

        if not combined or len(combined) > settings.MAX_BUFFER_CHARS:
            if combined:
                await send_whatsapp_message(phone, ERROR_MESSAGE_TOO_LONG, reply_to_message_id=message_id)
            await receiver.complete_message(message)
            return

        # --- INTENT CLASSIFICATION FOR MULTIPLE ENROLLMENTS ---
        if message_type == "text" and len(student_contexts) > 1:
            from src.llm.classifier import classify_course_intent
            
            enrolled_courses = [
                {"id": ctx["course"].id, "name": ctx["course"].name}
                for ctx in student_contexts
            ]
            
            classified_course_id = await classify_course_intent(combined, enrolled_courses)
            
            if classified_course_id:
                # Override the default context with the classified one
                for ctx in student_contexts:
                    if ctx["course"].id == classified_course_id:
                        student_context = ctx
                        break
            else:
                # Ambiguous intent - ask the student to specify
                course_list_str = "\n".join(f"- {c['name']}" for c in enrolled_courses)
                ambiguous_msg = (
                    "🤔 Pareceu que você fez uma pergunta genérica, mas eu vi que você está "
                    f"matriculado(a) em mais de um curso comigo:\n\n{course_list_str}\n\n"
                    "Por favor, reformule sua pergunta informando o **nome do curso** que deseja consultar! 👇"
                )
                await send_whatsapp_message(phone, ambiguous_msg, reply_to_message_id=message_id)
                await receiver.complete_message(message)
                track_event("intent_ambiguous", {"phone": phone})
                return
        # --------------------------------------------------------

        # Background lock renewal
        async def renew_lock_periodically(rcv, msg, interval=60):
            while True:
                await asyncio.sleep(interval)
                try:
                    await rcv.renew_message_lock(msg)
                except Exception:
                    break

        lock_task = asyncio.create_task(renew_lock_periodically(receiver, message))

        try:
            # Typing indicator sent from Worker for precise timing
            await send_whatsapp_typing(phone)
            
            # Execute heavy LLM/RAG pipeline
            response = await run_rag_pipeline(student_context, phone, combined)
            
            await send_whatsapp_message(phone, response, reply_to_message_id=message_id)
            await receiver.complete_message(message)
            track_event("llm_pipeline_success")
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            track_event("worker_error", {"error": str(e)[:500]})
            await send_whatsapp_message(phone, ERROR_TECHNICAL, reply_to_message_id=message_id)
            await receiver.dead_letter_message(message, reason="Pipeline Error", error_description=str(e)[:500])
        finally:
            lock_task.cancel()

async def llm_loop():
    """Main loop for the LLM worker consuming inbound_messages queue."""
    from src.shared.service_bus import service_bus
    from azure.servicebus import NEXT_AVAILABLE_SESSION
    from azure.servicebus.exceptions import OperationTimeoutError
    import asyncio
    import os
    
    is_dev = os.getenv("ENVIRONMENT", "development") in ("dev", "development")
    
    logger.info("main_worker_starting", environment="dev" if is_dev else "production")
    
    client = await service_bus.get_client()
    logger.info("service_bus_client_acquired")
        
    async with client:
        logger.info("service_bus_client_context_entered", mode="dev" if is_dev else "session-fifo")
        poll_count = 0
        while True:
            try:
                poll_count += 1
                if poll_count % 30 == 1:
                    logger.info("worker_poll_heartbeat", poll_count=poll_count, mode="dev" if is_dev else "session")
                    
                if is_dev:
                    receiver = client.get_queue_receiver(
                        queue_name="inbound_messages",
                        max_wait_time=10
                    )
                    async with receiver:
                        messages = await receiver.receive_messages(max_message_count=10, max_wait_time=5)
                        if messages:
                            logger.info("messages_received", count=len(messages))
                        for msg in messages:
                            await process_inbound(receiver, msg)
                else:
                    # Production: session-enabled FIFO per student
                    # NEXT_AVAILABLE_SESSION can block indefinitely on `async with receiver:`
                    # We wrap it in wait_for to prevent the worker from hanging.
                    receiver = client.get_queue_receiver(
                        queue_name="inbound_messages", 
                        session_id=NEXT_AVAILABLE_SESSION,
                        max_wait_time=10
                    )
                    try:
                        async with receiver:
                            logger.info("session_acquired")
                            messages = await receiver.receive_messages(max_message_count=10, max_wait_time=5)
                            if messages:
                                logger.info("messages_received", count=len(messages))
                            for msg in messages:
                                await process_inbound(receiver, msg)
                    except OperationTimeoutError:
                        # No sessions available right now, retry
                        pass
                        
            except OperationTimeoutError:
                # No active sessions available, just loop and wait
                if poll_count % 30 == 0:
                    logger.info("worker_idle_no_sessions", poll_count=poll_count)
                await asyncio.sleep(2)
            except Exception as e:
                logger.error("main_worker_error", error=str(e)[:500], error_type=type(e).__name__)
                if "amqp:socket-error" in str(e).lower() or "connection closed" in str(e).lower():
                    await asyncio.sleep(10)
                else:
                    import traceback
                    traceback.print_exc()
                    await asyncio.sleep(5)

async def main_worker():
    from src.workers.command_worker import command_loop
    import asyncio
    
    logger.info("starting_unified_workers")
    await asyncio.gather(
        llm_loop(),
        command_loop()
    )

if __name__ == "__main__":
    import structlog
    asyncio.run(main_worker())
