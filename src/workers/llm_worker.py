import asyncio
import structlog
from src.queue.interfaces import MessageContext
from src.shared.config import settings
from src.shared.telemetry import track_event, logger
from src.cache.debounce import drain_buffer
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
from src.channel.factory import get_channel
from src.rag.review_generator import generate_review
from src.workers.micro_learning import send_quiz_to_student
from src.rag.quiz_generator import QuizOutput, QuizAlternative

# RAG pipeline with conversational memory
async def run_rag_pipeline(student_context: dict, text: str) -> str:
    """Executes the LLM pipeline with Redis history and Qdrant RAG."""
    from src.llm.factory import get_provider
    from src.cache.session_history import OptimizedRedisChatMessageHistory
    from src.rag.retriever import retrieve_or_full_context
    from src.rag.prompt_builder import build_system_prompt
    from langchain_core.messages import HumanMessage, AIMessage

    llm = get_provider()

    # 1. Extract context variables
    teacher = student_context["teacher"]
    student = student_context["student"]
    course = student_context["course"]
    subject = student_context.get("subject")
    subject_name = subject.name if subject else None
    subject_id = subject.id if subject else None

    # 1.5. ADDON DE LIVROS — pergunta sobre o material denso (livro/apostila)?
    # Detecção via regex (barata). Quando sim: rate limit de proteção de
    # conteúdo (3/min) + contexto do livro + Modo Socrático + Gabarito
    # Verificado + Errata Viva. Best-effort: falha nunca derruba o fluxo.
    from src.workers.reading_journey import BOOK_QUERY_REGEX, build_reading_rag_addons
    reading_addons = {"context": "", "directives": "", "rate_limited": False}
    if BOOK_QUERY_REGEX.search(text):
        reading_addons = await build_reading_rag_addons(
            teacher_id=teacher.id,
            student_id=student.id,
            course_id=course.id,
            subject_id=subject_id,
            subject_name=subject_name,
            query=text,
        )
        if reading_addons.get("rate_limited"):
            from src.shared.constants.messages import READING_RATE_LIMITED
            return READING_RATE_LIMITED

    # 2. Retrieve Document Context from Qdrant
    # Funil de alta precisão (F3-F7 atrás de flags): híbrido+RRF → rerank →
    # CRAG-lite → (ou bypass long-context). Flags off = busca clássica.
    # O subject_name isola a busca na disciplina da turma do aluno.

    retrieval = await retrieve_or_full_context(
        teacher_id=teacher.id,
        course_id=course.id,
        subject_name=subject_name,
        current_lesson_number=student_context["class_obj"].current_lesson_number,
        query=text
    )
    rag_context = retrieval.context

    # F5 — RECUSA HONESTA: evidência insuficiente (🔴) e nenhum contexto de
    # livro para compensar → responde a mensagem fora-de-escopo do professor
    # SEM chamar o LLM gerador (zero alucinação, zero custo de geração) e
    # avisa o professor (HITL, dedupe diário).
    if retrieval.abstained and not reading_addons.get("context"):
        from src.rag.crag import build_abstention_message, register_abstention
        class_obj = student_context["class_obj"]
        class_label = course.name + (f" — {subject_name}" if subject_name else "")
        await register_abstention(
            student_id=student.id,
            course_id=course.id,
            class_id=class_obj.id,
            class_name=class_label,
            teacher_phone=teacher.phone,
            teacher_telegram_chat_id=teacher.telegram_chat_id,
            query=text,
        )
        track_event("crag_abstention", {
            "student_id": student.id,
            "course_id": course.id,
            "best_score": retrieval.best_score,
            "used_rewrite": retrieval.used_rewrite,
        })
        return build_abstention_message(teacher.out_of_scope_message)

    # 3. Build the System Prompt
    system_message = await build_system_prompt(
        teacher=teacher,
        student=student,
        course_id=course.id,
        course_name=course.name,
        current_lesson_id=str(student_context["class_obj"].current_lesson_number),
        subject_name=subject_name
    )

    # F4 — Roleplay (Vertical de Idiomas): se há sessão ativa (ou o aluno pede),
    # anexa a diretiva de dramatização ao System Prompt. O RAG continua ancorando
    # (Recusa Honesta). Gated: ROLEPLAY_ENABLED + curso de idioma; senão intocado.
    _class_obj = student_context["class_obj"]
    if settings.ROLEPLAY_ENABLED and settings.LANGUAGE_MODE_ENABLED and getattr(_class_obj, "is_language", False):
        from src.workers.roleplay import maybe_apply_roleplay
        from src.shared.language import detect_language_course, level_to_cefr
        _spec = detect_language_course(course.name, default_target=settings.DEFAULT_TARGET_LANGUAGE)
        _target = _spec.target_language if _spec else settings.DEFAULT_TARGET_LANGUAGE
        system_message, _ = await maybe_apply_roleplay(
            system_message, text, student.id, _target,
            level_to_cefr(getattr(_class_obj, "proficiency_level", None)),
        )

    # Diretrizes do Addon (Modo Socrático / Gabarito / Errata) entram ANTES do
    # contexto — são regras de comportamento, não material de consulta.
    if reading_addons.get("directives"):
        system_message += f"\n{reading_addons['directives']}"
    else:
        # F6.3 — Fatos verificados PRIMEIRO também no fluxo de aulas: gabarito
        # oficial/errata da referência exata detectada ancoram a resposta
        # acima de qualquer contexto recuperado (best-effort).
        from src.rag.verified_facts import fetch_verified_fact_directives
        verified_directives = await fetch_verified_fact_directives(course.id, subject_id, text)
        if verified_directives:
            system_message += f"\n{verified_directives}"

    # F5 — groundedness: citar fonte/seção e jamais completar com conhecimento
    # paramétrico. Só com o CRAG ligado (flags off = prompt atual intocado).
    if settings.CRAG_LITE_ENABLED:
        from src.shared.constants.prompts import GROUNDEDNESS_DIRECTIVE, PARTIAL_EVIDENCE_DIRECTIVE
        system_message += GROUNDEDNESS_DIRECTIVE
        if retrieval.evidence == "yellow":
            system_message += PARTIAL_EVIDENCE_DIRECTIVE

    # Append the RAG Context to the System Message
    if rag_context:
        system_message += f"\n\nCONTEXTO RECUPERADO DOS MATERIAIS DA AULA:\n{rag_context}"
    elif not reading_addons.get("context"):
        system_message += "\n\n(Nenhum contexto específico encontrado nos materiais para esta pergunta.)"

    if reading_addons.get("context"):
        system_message += f"\n\nCONTEXTO RECUPERADO DO LIVRO/APOSTILA:\n{reading_addons['context']}"
    
    # 4. Load conversation history from Redis (last 40 messages)
    # Sempre usamos o phone cadastrado como session_id unificador, 
    # mesmo que a mensagem venha do Telegram.
    student_phone = student.phone
    history = OptimizedRedisChatMessageHistory(session_id=student_phone)
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

async def get_student_context(sender_id: str, query_column: str, course_id: str | None = None, class_id: str | None = None):
    """Fetches real DB data: Student, Enrollment, CourseClass, Subject, Course, and Teacher.

    If class_id (preferido) or course_id is provided, filters for the specific
    enrollment and returns a single dict. Otherwise returns a list of all active
    enrollments. O "subject" pode ser None em turmas legadas sem disciplina.
    """
    from src.db.database import AsyncSessionLocal
    from src.db.models.user import Student, Teacher
    from src.db.models.course import StudentEnrollment, CourseClass, Course, Subject
    from sqlalchemy import select

    def _to_dict(r):
        return {
            "student": r[0],
            "enrollment": r[1],
            "class_obj": r[2],
            "course": r[3],
            "teacher": r[4],
            "subject": r[5],  # Subject | None
        }

    async with AsyncSessionLocal() as session:
        # Join tables to get the full context in one query
        # O professor do aluno é o da TURMA dele (posse por turma: CourseClass.teacher_id).
        # A migration faz backfill (turma herda o professor do curso), então nunca fica NULL.
        # Subject é outerjoin: turma sem disciplina (legado) não pode sumir do contexto.
        stmt = (
            select(Student, StudentEnrollment, CourseClass, Course, Teacher, Subject)
            .join(StudentEnrollment, Student.id == StudentEnrollment.student_id)
            .join(CourseClass, StudentEnrollment.class_id == CourseClass.id)
            .join(Course, CourseClass.course_id == Course.id)
            .join(Teacher, CourseClass.teacher_id == Teacher.id)
            .outerjoin(Subject, CourseClass.subject_id == Subject.id)
            .where(getattr(Student, query_column) == sender_id)
        )

        # Filtro específico: por TURMA (botão de review) ou por curso (fallback legado).
        if class_id or course_id:
            stmt = stmt.where(CourseClass.id == class_id) if class_id else stmt.where(Course.id == course_id)
            result = await session.execute(stmt)
            row = result.first()
            if not row:
                return None
            return _to_dict(row)
        else:
            result = await session.execute(stmt)
            rows = result.all()
            if not rows:
                return []
            return [_to_dict(r) for r in rows]


async def handle_quiz_response(
    channel,
    sender_id: str,
    student_id: str,
    reply_id: str,
    interactive_payload: dict,
    message_id: str | None = None
) -> None:
    """Valida resposta do aluno a um quiz por comparação direta (if/else)."""
    # Parse do reply_id: "quiz_{quiz_id}_{answer_id}" → quiz_id, answer_id
    parts = reply_id.split("_", 2)
    if len(parts) < 3:
        await channel.send_text(sender_id, ERROR_INVALID_RESPONSE, reply_to=message_id)
        return
    
    quiz_id = parts[1]
    answer_id = parts[2]

    # Busca o quiz pendente
    from src.db.database import AsyncSessionLocal
    from sqlalchemy import text
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
                SELECT quiz_id, correct_answer, explanation, question_text, status,
                       quiz_bank_id, reading_plan_id, lesson_id, course_id
                FROM "PendingQuizzes"
                WHERE quiz_id = :quiz_id AND student_id = :student_id
            """),
            {"quiz_id": quiz_id, "student_id": student_id}
        )
        quiz = result.fetchone()

        if not quiz:
            await channel.send_text(sender_id, ERROR_QUIZ_NOT_FOUND, reply_to=message_id)
            return

        if quiz.status != "pending":
            await channel.send_text(sender_id, ERROR_QUIZ_ANSWERED, reply_to=message_id)
            return

        # ── VALIDAÇÃO DETERMINÍSTICA — if/else puro, sem LLM ──
        is_correct = (answer_id == quiz.correct_answer)

        # Compare-and-swap ATÔMICO da transição pending→answered (WHERE status=
        # 'pending'). O reprocessamento da PEL (replay de pod morto ou pipeline
        # > min_idle: `_reap_pending` reentrega SEM passar pelo session_lock)
        # poderia rodar os hooks abaixo (streak, SRS, desafio, avanço de seção)
        # DUAS vezes. Só a entrega que VENCE o CAS (rowcount==1) segue adiante.
        upd = await session.execute(
            text("""
                UPDATE "PendingQuizzes"
                SET student_answer = :answer, is_correct = :correct,
                    status = 'answered', answered_at = CURRENT_TIMESTAMP
                WHERE quiz_id = :quiz_id AND status = 'pending'
            """),
            {"answer": answer_id, "correct": is_correct, "quiz_id": quiz_id}
        )
        await session.commit()
        if getattr(upd, "rowcount", 0) == 0:
            # Outra entrega já transicionou este quiz — não duplica os hooks.
            await channel.send_text(sender_id, ERROR_QUIZ_ANSWERED, reply_to=message_id)
            return

    # Monta feedback com emoji visual
    if is_correct:
        feedback = FEEDBACK_CORRECT.format(explanation=quiz.explanation)
    else:
        feedback = FEEDBACK_INCORRECT.format(correct_answer=quiz.correct_answer, explanation=quiz.explanation)

    # ── ADDON DE LIVROS ─────────────────────────────────────────────────────
    # 6.5 Mapeamento Aula ↔ Capítulo: quiz de AULA respondido → sugere o
    # capítulo vinculado no rodapé do feedback (best-effort, nunca quebra).
    if not quiz.reading_plan_id:
        try:
            chapter = await _find_linked_chapter(quiz.course_id, quiz.quiz_bank_id, quiz.lesson_id)
            if chapter:
                from src.shared.constants.messages import LESSON_CHAPTER_SUGGESTION
                feedback += LESSON_CHAPTER_SUGGESTION.format(chapter=chapter)
        except Exception as e:
            logger.error("lesson_chapter_suggestion_failed", error=str(e)[:200])

    await channel.send_text(sender_id, feedback, reply_to=message_id)

    # Fase 3: PORTÃO da Jornada de Leitura — o check-in desbloqueia (ou não) a
    # próxima seção e devolve a mensagem de progresso/releitura.
    if quiz.reading_plan_id:
        try:
            from src.workers.reading_journey import handle_reading_checkin_answer
            section = int(quiz.lesson_id) if str(quiz.lesson_id).isdigit() else 1
            gate_msg = await handle_reading_checkin_answer(quiz.reading_plan_id, section, is_correct)
            if gate_msg:
                await channel.send_text(sender_id, gate_msg)
        except Exception as e:
            logger.error("reading_gate_hook_failed", error=str(e)[:300])

    # Fase 4: Revisão Espaçada — TODA resposta de questão do QuizBank entra na
    # escada D+3/D+10/D+30 (primeira vez insere; revisão sobe degrau ou reseta).
    if quiz.quiz_bank_id:
        from src.workers.spaced_repetition import register_quiz_answer
        await register_quiz_answer(student_id, quiz.quiz_bank_id, is_correct)

    # Telemetria
    track_event("quiz_answered", {
        "student_id": student_id,
        "quiz_id": quiz_id,
        "is_correct": is_correct,
        "answer": answer_id,
        "is_reading_checkin": bool(quiz.reading_plan_id),
    })


async def _find_linked_chapter(course_id: str, quiz_bank_id: str | None, lesson_id: str) -> int | None:
    """Resolve o capítulo vinculado à aula (LessonChapterLinks, Fase 6.5).

    O subject vem do QuizBank quando o quiz é do banco (escopo exato); quizzes
    legados sem vínculo caem no match por curso + aula. Só sugere capítulo para
    quizzes de AULA (material 'lesson') — check-ins de leitura não sugerem.
    """
    if not str(lesson_id).isdigit():
        return None
    lesson_number = int(lesson_id)

    from src.db.database import AsyncSessionLocal
    from sqlalchemy import text as sql_text

    async with AsyncSessionLocal() as session:
        subject_id = None
        if quiz_bank_id:
            res = await session.execute(
                sql_text('SELECT subject_id, material_type FROM "QuizBank" WHERE id = :qbid'),
                {"qbid": quiz_bank_id},
            )
            row = res.fetchone()
            if row:
                if row.material_type != "lesson":
                    return None  # quiz de livro/apostila: sugestão não se aplica
                subject_id = row.subject_id

        subject_clause = "subject_id = :sub" if subject_id else "subject_id IS NULL"
        res = await session.execute(
            sql_text(f'''SELECT chapter_number FROM "LessonChapterLinks"
                         WHERE course_id = :cid AND {subject_clause}
                           AND lesson_number = :lesson
                         LIMIT 1'''),
            {"cid": course_id, "lesson": lesson_number,
             **({"sub": subject_id} if subject_id else {})},
        )
        return res.scalar_one_or_none()

async def handle_review_request(channel, sender_id: str, student_context: dict) -> None:
    """Generates and sends a review summary + quiz when student accepts the daily review.

    Flow (com o Banco de Questões / Cache Semântico):
        1. Extract context (teacher, student, course, subject, enrollment)
        2. generate_review tenta o CACHE HIT no QuizBank (custo $0); se for
           Cache Miss, gera via LLM com uma Lente inédita e salva no banco
        3. Send formatted summary to the student
        4. Send quiz via interactive buttons, registrando o quiz_bank_id na
           entrega (dedupe por aluno + analytics do /acertos)
    """
    teacher = student_context["teacher"]
    student = student_context["student"]
    course = student_context["course"]
    subject = student_context.get("subject")
    subject_name = subject.name if subject else None
    subject_id = subject.id if subject else None
    current_lesson = student_context["class_obj"].current_lesson_number

    from src.rag.prompt_builder import resolve_lesson_label
    lesson_label = await resolve_lesson_label(course.id, str(current_lesson))

    # Vertical de Idiomas: turma de idioma (Idioma=Sim na planilha) com a vertical
    # ligada → revisão com a língua do TEXTO por nível (Iniciante=pt-BR; demais=alvo)
    # + áudio na língua-alvo. Sem cache (escopo por nível); fallback à revisão normal.
    class_obj = student_context["class_obj"]
    from src.rag.review_generator import class_language_context
    delivery, target_language = class_language_context(
        course.name,
        getattr(class_obj, "is_language", False),
        getattr(class_obj, "proficiency_level", None),
    )

    review = None
    if delivery is not None and target_language is not None:
        from src.rag.review_generator import generate_language_review
        review = await generate_language_review(
            teacher_id=teacher.id,
            student_name=student.name,
            course_id=course.id,
            course_name=course.name,
            subject_name=subject_name,
            current_lesson_number=current_lesson,
            lesson_label=lesson_label,
            delivery=delivery,
            target_language=target_language,
        )
        if review is None:
            delivery = None  # sem conteúdo de idioma → cai na revisão normal

    if review is None:
        # Generate review (summary + quiz) — Cache Hit no QuizBank quando possível
        review = await generate_review(
            teacher_id=teacher.id,
            teacher_name=teacher.name,
            teacher_subject=teacher.subject,
            student_id=student.id,
            student_name=student.name,
            course_id=course.id,
            course_name=course.name,
            subject_name=subject_name,
            current_lesson_number=current_lesson,
            lesson_label=lesson_label,
            subject_id=subject_id,
            from_bank=True,
        )

    if not review:
        await channel.send_text(sender_id, ERROR_NO_MATERIAL)
        return

    # Format and send the summary
    key_points_text = "\n".join(f"  • {kp}" for kp in review.key_points)
    
    display_subject = subject_name if subject_name else course.name
    display_topic = review.topic if review.topic else lesson_label.replace("Aula atual", "Aula")
        
    summary_message = (
        f"📖 *Revisão — {display_subject}*\n"
        f"📍 {display_topic}\n\n"
        f"{review.summary}\n\n"
        f"🔑 *Pontos-chave:*\n{key_points_text}\n\n"
        f"_Leia com atenção — o quiz a seguir é baseado neste resumo!_ 👇"
    )
    await channel.send_text(sender_id, summary_message)

    # Vertical de Idiomas: áudio da revisão na LÍNGUA-ALVO (best-effort). Iniciante
    # ouve a versão completa traduzida (audio_script); níveis avançados ouvem o
    # próprio resumo (já na língua-alvo). TTS off/erro ⇒ segue só com o texto.
    if delivery is not None and target_language:
        from src.rag.review_generator import synthesize_listening
        from src.shared.language import review_audio_text
        # Iniciante: o resumo está em pt-BR. Só narramos quando há audio_script
        # (tradução completa na língua-alvo); sem ele NÃO narramos o pt-BR com a
        # voz/locale da língua-alvo (áudio na língua errada) — segue só o texto.
        audio_text = review_audio_text(review.audio_script, review.summary, delivery.is_beginner)
        if audio_text:
            try:
                audio = await synthesize_listening(audio_text, target_language)
                if audio:
                    await channel.send_audio(sender_id, audio)
                    track_event("review_audio_sent", {
                        "student_id": student.id, "course_id": course.id,
                        "lang": target_language, "level": getattr(class_obj, "proficiency_level", None),
                    })
            except Exception as e:
                logger.warning("review_audio_failed", student_id=student.id, error=str(e)[:200])

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

    # Send quiz via interactive buttons (uses existing micro_learning flow).
    # O quiz_bank_id grava o consumo desta questão por este aluno em
    # PendingQuizzes — é o que impede a repetição da dupla (Tópico + Lente).
    quiz_id = await send_quiz_to_student(
        channel=channel,
        student={
            "id": student.id,
            "sender_id": sender_id,
            "course_id": course.id,
            "lesson_pointer": str(current_lesson),
        },
        quiz=quiz_output,
        quiz_bank_id=review.quiz_bank_id,
        # Áudio do quiz na língua-alvo só quando o TEXTO já é alvo (níveis
        # avançados); no iniciante o quiz é pt-BR e o áudio da revisão já cobre.
        target_language=(target_language if (delivery is not None and not delivery.is_beginner) else None),
    )

    track_event("review_sent", {
        "student_id": student.id,
        "course_id": course.id,
        "lesson_id": current_lesson,
        "quiz_id": quiz_id,
        "quiz_bank_id": review.quiz_bank_id,
        "from_quiz_bank_cache": bool(review.quiz_bank_id),
    })

    from src.db.database import AsyncSessionLocal
    from sqlalchemy import text
    from uuid import uuid4
    
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""
                INSERT INTO "StudentInteractions" (id, student_id, course_id, interaction_type, message_content) 
                VALUES (:id, :sid, :cid, :itype, :msg)
            """),
            {
                "id": str(uuid4()),
                "sid": student.id,
                "cid": course.id,
                "itype": "review_sent",
                "msg": None
            }
        )
        await session.commit()


async def process_inbound(event: dict, ctx: MessageContext):
    
    # Suporte legado para payload na transição
    sender_id = str(event.get("sender_id", event.get("phone", "")))
    text = event.get("text")
    message_type = event.get("message_type", "text")
    interactive_payload = event.get("interactive_payload")
    correlation_id = event.get("correlation_id", "unknown")
    message_id = event.get("message_id")
    channel_type = event.get("channel")
    
    channel = get_channel(channel_type)
    query_column = channel.get_user_query_column()
    
    with structlog.contextvars.bound_contextvars(correlation_id=correlation_id, sender_id=sender_id):
        student_contexts = await get_student_context(sender_id, query_column)

        # Guard clause: student may have been deactivated or not enrolled
        if not student_contexts:
            from src.db.database import AsyncSessionLocal
            from src.db.models.user import Teacher
            from sqlalchemy import select
            
            # Check if this is a teacher
            async with AsyncSessionLocal() as session:
                result = await session.execute(select(Teacher).where(getattr(Teacher, query_column) == sender_id))
                if result.scalar_one_or_none():
                    await channel.send_text(sender_id, TEACHER_CHAT_REPLY, reply_to=message_id)
                else:
                    await channel.send_text(sender_id, ERROR_NOT_ENROLLED, reply_to=message_id)
                    
            await ctx.complete()
            return

        student_context = student_contexts[0]  # Default fallback context

        # LGPD Art. 14 (P1.1): criança sem opt-in do responsável → o bot NÃO
        # interage (nem responde, nem registra). Flag off ou aluno não-criança
        # passa direto. O scheduler já disparou o pedido ao responsável.
        student_obj = student_context["student"]
        if getattr(student_obj, "is_child", False):
            from src.workers.guardian_consent import consent_blocked
            if await consent_blocked(student_obj.id, True):
                from src.shared.constants.messages import GUARDIAN_PENDING_STUDENT_MSG
                await channel.send_text(sender_id, GUARDIAN_PENDING_STUDENT_MSG, reply_to=message_id)
                await ctx.complete()
                track_event("guardian_consent_gate_blocked", {"student_id": student_obj.id})
                return

        # 3.1. Log interaction in DB (Movido do Webhook para garantir SLA)
        from src.db.database import AsyncSessionLocal
        from sqlalchemy import text as sql_text
        from uuid import uuid4
        
        async with AsyncSessionLocal() as session:
            student_id = student_context["student"].id
            await session.execute(
                sql_text('UPDATE "Students" SET preferred_channel = :ch WHERE id = :sid'),
                {"ch": channel.channel_name, "sid": student_id}
            )

            interaction_type = "message_received"
            if message_type == "interactive" and interactive_payload and interactive_payload.get("id", "").startswith("quiz_"):
                interaction_type = "quiz_answered"
            
            await session.execute(
                sql_text("""
                    INSERT INTO "StudentInteractions" (id, student_id, interaction_type, message_content) 
                    VALUES (:id, :sid, :itype, :msg)
                """),
                {
                    "id": str(uuid4()),
                    "sid": student_id,
                    "itype": interaction_type,
                    "msg": text or (interactive_payload.get("title", "") if interactive_payload else "")
                }
            )
            await session.commit()

        # Fast-path H2.2: foto recebida (redação) — roteia para a correção
        # dissertativa ANTES do debounce (imagem não entra em buffer de texto).
        if message_type == "image" and event.get("media_id"):
            try:
                from src.workers.essay import handle_image_message
                await handle_image_message(
                    channel, sender_id, student_context,
                    media_id=event["media_id"], caption=text or "",
                )
            except Exception as e:
                logger.error("essay_image_flow_failed", error=str(e)[:300])
                await channel.send_text(sender_id, ERROR_TECHNICAL)
            finally:
                await ctx.complete()
            return

        # Fast-path F3 (Idiomas): nota de voz — roteia para a análise de fala/
        # pronúncia ANTES do debounce (áudio não entra em buffer de texto). O
        # download e o STT acontecem no worker; o áudio é descartado após (LGPD).
        if message_type == "audio" and event.get("media_id"):
            try:
                from src.workers.speaking import handle_audio_message
                await handle_audio_message(
                    channel, sender_id, student_context,
                    media_id=event["media_id"], message_id=event.get("message_id", ""),
                )
            except Exception as e:
                logger.error("speaking_audio_flow_failed", error=str(e)[:300])
                await channel.send_text(sender_id, ERROR_TECHNICAL)
            finally:
                await ctx.complete()
            return

        # Fast-path: confirmação de exclusão de dados (LGPD Art. 18, passo 2)
        if message_type == "interactive" and interactive_payload:
            reply_id = interactive_payload.get("id", "")
            if reply_id in ("lgpd_delete_confirm", "lgpd_delete_cancel"):
                from src.workers.privacy_commands import handle_delete_decision
                await handle_delete_decision(
                    channel, sender_id, student_context,
                    confirmed=(reply_id == "lgpd_delete_confirm"),
                )
                await ctx.complete()
                return

        # Fast-path: resposta interativa de quiz (validação determinística)
        if message_type == "interactive" and interactive_payload:
            reply_id = interactive_payload.get("id", "")
            if reply_id.startswith("quiz_"):
                await handle_quiz_response(channel, sender_id, student_context["student"].id, reply_id, interactive_payload, message_id=message_id)
                await ctx.complete()
                return

            # Fast-path: aluno aceita a revisão diária
            if reply_id.startswith("review_accept_"):
                try:
                    # Extrai o class_id do botão (review_accept_{class_id}). Agendamentos
                    # apontam para a TURMA/Disciplina exata. Botões legados carregavam
                    # course_id → o get_student_context(class_id=...) não acha e cai no
                    # fallback (primeira matrícula), preservando compat na transição.
                    review_class_id = reply_id.replace("review_accept_", "", 1)

                    # Re-fetch context for the SPECIFIC class from the button
                    review_context = await get_student_context(sender_id, query_column, class_id=review_class_id)

                    if not review_context:
                        review_context = student_context  # fallback to default

                    await handle_review_request(channel, sender_id, review_context)
                except Exception as e:
                    logger.error("Fast-path review_accept error", error=str(e), exc_info=True)
                    await channel.send_text(sender_id, ERROR_TECHNICAL)
                finally:
                    await ctx.complete()
                return

            # Fast-path: aluno adia a revisão
            if reply_id.startswith("review_later_"):
                await channel.send_text(sender_id, REVIEW_LATER_ACK)
                await ctx.complete()
                return

            # Fast-path: replanejamento da Jornada de Leitura (Addon de Livros).
            # "✅ Ajustar plano" recalcula a partir de hoje, sem culpa.
            if reply_id.startswith("reading_recalc_"):
                from src.workers.reading_journey import recalc_reading_plan
                from src.shared.constants.messages import READING_RECALC_DONE
                plan_id = reply_id.replace("reading_recalc_", "", 1)
                ok = await recalc_reading_plan(plan_id)
                await channel.send_text(sender_id, READING_RECALC_DONE if ok else ERROR_TECHNICAL)
                await ctx.complete()
                return

            # "▶️ Manter como está": só confirma — o plano segue o fluxo normal.
            if reply_id.startswith("reading_keep_"):
                from src.shared.constants.messages import READING_RECALC_KEPT
                await channel.send_text(sender_id, READING_RECALC_KEPT)
                await ctx.complete()
                return

            # Fast-path: aviso de janela de 23.5h
            if reply_id == "window_warning_later":
                await channel.send_text(sender_id, "Combinado! Vou aguardar até o horário do seu próximo agendamento. Bons estudos!")
                await ctx.complete()
                return

        # Debouncing no worker (movido do webhook)
        from src.cache.redis_client import get_redis
        import json
        redis = await get_redis()
        
        buffer_key = f"buffer:{sender_id}"
        user_key = f"debounce:{sender_id}"

        # Armazena text e message_id para não perdermos o ACK dos follow-ups
        ctx_msg_id = getattr(ctx, 'message_id', None)
        msg_payload = {
            "text": text, 
            "message_id": ctx_msg_id.decode('utf-8') if isinstance(ctx_msg_id, bytes) else str(ctx_msg_id) if ctx_msg_id else None
        }
        await redis.rpush(buffer_key, json.dumps(msg_payload))
        await redis.expire(buffer_key, 10)

        is_first = await redis.set(user_key, "1", nx=True, ex=settings.DEBOUNCE_TTL_SECONDS)
        if not is_first:
            # Bug 5 FIX: Não fazemos ACK imediato do follow-up!
            # Ele fica na PEL. O pod âncora vai dar o ACK por ele no final.
            # Se der crash, a PEL do follow-up e do âncora são reprocessadas.
            return

        # Wait the debounce window
        await asyncio.sleep(settings.DEBOUNCE_TTL_SECONDS)

        # Drain the buffer atomically
        messages_raw = await drain_buffer(sender_id, cooldown=1)
        
        texts = []
        ack_message_ids = []
        for msg in messages_raw:
            try:
                data = json.loads(msg)
                if isinstance(data, dict):
                    texts.append(str(data.get("text", "")))
                    if data.get("message_id"):
                        ack_message_ids.append(data["message_id"].encode('utf-8'))
                else:
                    texts.append(str(msg))
            except Exception:
                texts.append(str(msg))

        combined = " ".join(texts)

        if not combined or len(combined) > settings.MAX_BUFFER_CHARS:
            if combined:
                await channel.send_text(sender_id, ERROR_MESSAGE_TOO_LONG, reply_to=message_id)
            await ctx.complete()
            return

        # LGPD Art. 20 (P1.6): "REVISAR" logo após uma correção de redação =
        # pedido de revisão HUMANA (janela no Redis). Fora da janela, "revisar"
        # é pergunta de estudo normal e segue o pipeline.
        if message_type == "text" and combined.strip().lower() == "revisar":
            from src.workers.privacy_commands import request_essay_review
            if await request_essay_review(channel, sender_id, student_context):
                await ctx.complete()
                return

        # F9 — opt-out de mensagens proativas ("SAIR"/"VOLTAR"): intercepta ANTES
        # do pipeline (palavra-chave exata; não cai como pergunta de estudo).
        if message_type == "text":
            from src.workers.optout import optout_intent, handle_optout
            if optout_intent(combined) is not None:
                await handle_optout(channel, sender_id, student_context, combined)
                await ctx.complete()
                return

        # F5 — resposta do teste de nivelamento: se há sessão ativa no Redis, o
        # texto são as respostas. Intercepta ANTES do pipeline caro (classificação
        # de intenção/LLM). Reusa o padrão de janela do Redis do essay/REVISAR.
        if message_type == "text":
            from src.workers.placement import placement_active, handle_placement_answer
            if await placement_active(sender_id):
                await handle_placement_answer(channel, sender_id, student_context, combined)
                await ctx.complete()
                return

        # --- INTENT CLASSIFICATION FOR MULTIPLE ENROLLMENTS ---
        if message_type == "text" and len(student_contexts) > 1:
            from src.llm.classifier import classify_course_intent
            
            enrolled_courses = []
            for s_ctx in student_contexts:
                c_name = s_ctx["course"].name
                if s_ctx.get("subject"):
                    c_name += f" - {s_ctx['subject'].name}"
                enrolled_courses.append({
                    "id": s_ctx["class_obj"].id,
                    "name": c_name
                })
            
            classified_class_id = await classify_course_intent(combined, enrolled_courses)
            
            if classified_class_id:
                # Override the default context with the classified one
                for s_ctx in student_contexts:
                    if s_ctx["class_obj"].id == classified_class_id:
                        student_context = s_ctx
                        break
            else:
                # Ambiguous intent - ask the student to specify
                course_list_str = "\n".join(f"- {c['name']}" for c in enrolled_courses)
                ambiguous_msg = (
                    "🤔 Pareceu que você fez uma pergunta genérica, mas eu vi que você está "
                    f"matriculado(a) em mais de uma disciplina comigo:\n\n{course_list_str}\n\n"
                    "Por favor, reformule sua pergunta informando sobre qual **disciplina** você deseja consultar! 👇"
                )
                await channel.send_text(sender_id, ambiguous_msg, reply_to=message_id)
                await ctx.complete()
                track_event("intent_ambiguous", {"sender_id": sender_id})
                return
        # --------------------------------------------------------

        # Background lock renewal
        async def renew_lock_periodically(context: MessageContext, interval=60):
            while True:
                await asyncio.sleep(interval)
                try:
                    await context.renew_lock()
                except Exception:
                    break

        lock_task = asyncio.create_task(renew_lock_periodically(ctx))

        try:
            # Typing indicator sent from Worker for precise timing
            await channel.send_typing(sender_id)
            
            # Execute heavy LLM/RAG pipeline
            response = await run_rag_pipeline(student_context, combined)
            
            await channel.send_text(sender_id, response, reply_to=message_id)
            await ctx.complete()
            track_event("llm_pipeline_success")
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            logger.error("worker_pipeline_failed", error=str(e), exc_info=True)
            track_event("worker_error", {"error": str(e)[:500]})
            await channel.send_text(sender_id, ERROR_TECHNICAL, reply_to=message_id)
            await ctx.dead_letter(reason="Pipeline Error", description=str(e)[:500])
        finally:
            lock_task.cancel()
            # Bug 5 FIX: Ack seguro dos follow-ups
            if hasattr(ctx, 'client'):
                for m_id in ack_message_ids:
                    if m_id != getattr(ctx, 'message_id', None):
                        try:
                            await ctx.client.xack(getattr(ctx, "queue"), getattr(ctx, "group"), m_id)
                            await ctx.client.xdel(getattr(ctx, "queue"), m_id)
                        except Exception as e:
                            logger.error("failed_to_ack_followup", error=str(e))

async def llm_loop():
    """Main loop for the LLM worker consuming inbound_messages queue."""
    from src.queue.factory import get_queue
    import os
    
    is_dev = os.getenv("ENVIRONMENT", "development") in ("dev", "development")
    logger.info("main_worker_starting", environment="dev" if is_dev else "production")
    
    queue = get_queue()
    await queue.consume_loop("inbound_messages", process_inbound, use_sessions=True)

async def main_worker():
    from src.workers.command_worker import command_loop, outbound_loop
    from src.workers.ingestion_worker import handle_ingestion_message, drive_polling_loop
    from src.queue.factory import get_queue
    import asyncio
    
    logger.info("starting_unified_workers (LLM, Command, Outbound, Ingestion)")
    queue = get_queue()
    
    await asyncio.gather(
        llm_loop(),
        command_loop(),
        outbound_loop(),
        queue.consume_loop("ingestion_queue", handle_ingestion_message),
        drive_polling_loop()
    )

if __name__ == "__main__":
    import structlog
    asyncio.run(main_worker())
