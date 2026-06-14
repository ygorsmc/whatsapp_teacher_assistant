"""
Reading Journey — Jornada de Leitura Guiada (Addon de Livros e Apostilas).

Núcleo da Fase 2/3 do Addon. Responsabilidades:

  - `process_reading_plans()`    → (scheduler) envia a microdose de leitura do
    dia (10-15 min, NUNCA "leia o capítulo inteiro") ou, se o aluno não
    interagiu desde a última sugestão, oferece recalcular o plano SEM CULPA.
  - `process_reading_checkins()` → (scheduler) dispara o quiz de check-in
    algumas horas após a sugestão (ex.: sugestão 07:00 → quiz 19:00). O quiz é
    o "portão": acertar avança a seção; errar oferece reler o trecho.
  - `handle_reading_checkin_answer()` → (llm_worker) aplica o resultado do
    portão no plano (avanço + streak + progresso visível).
  - Helpers puros (testáveis sem I/O): ritmo → próximo gatilho, streak,
    percentual de progresso, seleção da próxima seção ativa (Modo Curador).

Regras de produto (do prompt de execução):
  - Replanejamento sem culpa: NUNCA dizer "você está X capítulos atrasado".
  - Modo férias: is_paused=true para de gerar gatilhos, sem perder progresso.
  - Progresso visível em toda interação: "📖 42% do livro | 🔥 6 dias".
"""

import json
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

import structlog
from sqlalchemy import text

from src.shared.config import settings
from src.shared.telemetry import track_event
from src.db.database import AsyncSessionLocal
from src.channel.factory import get_channel
from src.shared.constants.messages import (
    READING_SUGGESTION_BODY,
    READING_PROGRESS_LINE,
    READING_RECALC_BODY,
    READING_RECALC_YES_TITLE,
    READING_RECALC_KEEP_TITLE,
    READING_CHECKIN_INTRO,
    READING_GATE_PASSED,
    READING_GATE_RETRY,
    READING_COMPLETED,
    ERRATA_WARNING,
)

logger = structlog.get_logger(__name__)

LOCAL_TZ_OFFSET = timedelta(hours=settings.TIMEZONE_OFFSET_HOURS)

# Janela entre a sugestão de leitura e o quiz de check-in (sugestão às 07:00 →
# check-in às 19:00).
CHECKIN_DELAY_HOURS = 12

MATERIAL_LABELS = {"lesson": "aula", "book": "livro", "workbook": "apostila"}


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_naive(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt


# ==============================================================================
# HELPERS PUROS (sem I/O — cobertos por testes unitários)
# ==============================================================================

def compute_reading_next_trigger(pace: str, preferred_time: str, from_date: Optional[datetime] = None) -> datetime:
    """Próximo gatilho (naive UTC) do plano de leitura.

    daily  → próxima ocorrência do preferred_time (amanhã, se já passou hoje);
    weekly → mesma hora, 7 dias após a próxima ocorrência diária ser hoje
             (na prática: +7 dias a partir da ocorrência de hoje/amanhã).
    `from_date` é "agora" em UTC naive (injetável para teste).
    """
    now_utc = from_date or _naive_utc_now()
    base_local = now_utc + LOCAL_TZ_OFFSET
    hour, minute = map(int, preferred_time.split(":"))

    target_local = base_local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target_local <= base_local:
        target_local += timedelta(days=1)

    if pace == "weekly":
        target_local += timedelta(days=6)

    return target_local - LOCAL_TZ_OFFSET


def update_streak(last_read_at: Optional[datetime], now_utc: datetime, current_streak: int) -> int:
    """Streak em DIAS LOCAIS consecutivos de leitura validada.

    - nunca leu → 1 (primeiro dia)
    - mesma data local → mantém (o dia já contou)
    - data local de ontem → +1
    - buraco de 2+ dias → recomeça em 1 (sem culpa: a mensagem nunca expõe a
      quebra, apenas mostra o número atual)
    """
    if not last_read_at:
        return max(1, 1 if current_streak <= 0 else 1)
    last_local = (last_read_at + LOCAL_TZ_OFFSET).date()
    now_local = (now_utc + LOCAL_TZ_OFFSET).date()
    delta_days = (now_local - last_local).days
    if delta_days <= 0:
        return max(current_streak, 1)
    if delta_days == 1:
        return current_streak + 1
    return 1


def progress_percent(current_section: int, total_sections: int) -> int:
    if total_sections <= 0:
        return 0
    pct = round(100 * current_section / total_sections)
    return max(0, min(100, pct))


def parse_active_chapters(raw: Optional[str]) -> Optional[list[int]]:
    """Desserializa Classes.active_chapters ("[1, 3, 5]"). None/inválido = todos ativos."""
    if not raw:
        return None
    try:
        chapters = json.loads(raw)
        chapters = sorted({int(c) for c in chapters})
        return chapters or None
    except Exception:
        return None


def next_active_section(current_section: int, total_sections: int, active_chapters: Optional[list[int]]) -> Optional[int]:
    """Próxima seção a sugerir, pulando capítulos desativados (Modo Curador).

    Retorna None quando não há mais seções ativas pela frente (jornada
    concluída do ponto de vista do plano de curso).
    """
    candidate = current_section + 1
    while candidate <= total_sections:
        if active_chapters is None or candidate in active_chapters:
            return candidate
        candidate += 1
    return None


def build_progress_line(current_section: int, total_sections: int, streak_days: int, material_type: str) -> str:
    label = MATERIAL_LABELS.get(material_type, "livro")
    return READING_PROGRESS_LINE.format(
        percent=progress_percent(current_section, total_sections),
        material_label=label,
        streak_days=max(streak_days, 0),
    )


def _checkin_hour(preferred_time: str) -> str:
    """Hora local (HH:MM) do check-in = preferred_time + CHECKIN_DELAY_HOURS."""
    hour, minute = map(int, preferred_time.split(":"))
    return f"{(hour + CHECKIN_DELAY_HOURS) % 24:02d}:{minute:02d}"


# H1.4 — Medalhas de streak: marcos exatos que destravam celebração.
STREAK_MILESTONES = {3: "🥉", 7: "🥈", 30: "🥇", 100: "🏆"}


def streak_medal(streak_days: int) -> str | None:
    """Medalha do marco EXATO (só celebra no dia em que o marco é atingido)."""
    return STREAK_MILESTONES.get(streak_days)


def best_streak_badge(streak_days: int) -> str:
    """Maior medalha já conquistada pelo streak atual (para o /meu_progresso)."""
    badge = ""
    for days in sorted(STREAK_MILESTONES):
        if streak_days >= days:
            badge = STREAK_MILESTONES[days]
    return badge


def section_label_for(material_type: str, section: int) -> str:
    return ("Capítulo" if material_type == "book" else "Módulo") + f" {section}"


# ==============================================================================
# ACESSO A DADOS
# ==============================================================================

async def _window_active(session, student_id: str, now_utc: datetime) -> bool:
    """Janela de 24h da Meta (mesmo critério do scheduler clássico)."""
    res = await session.execute(
        text("""
            SELECT MAX(timestamp)
            FROM "StudentInteractions"
            WHERE student_id = :sid
              AND interaction_type IN ('message_received', 'quiz_answered')
        """),
        {"sid": student_id},
    )
    last_interaction = _as_naive(res.scalar())
    return bool(last_interaction and last_interaction > now_utc - timedelta(hours=24))


async def _get_class_context(session, student_id: str, course_id: str, subject_id: Optional[str]):
    """Turma do aluno para o escopo (curso, disciplina): traz capítulos ativos
    e o professor dono (necessário para gerar o quiz de check-in).

    Filtro obrigatório por course_id; subject_id entra quando o plano tem
    disciplina (turmas legadas sem Subject casam por subject_id IS NULL).
    """
    subject_clause = "cl.subject_id = :sub" if subject_id else "cl.subject_id IS NULL"
    res = await session.execute(
        text(f"""
            SELECT cl.id AS class_id, cl.active_chapters, cl.teacher_id,
                   t.name AS teacher_name, t.subject AS teacher_subject,
                   cl.is_language, cl.proficiency_level
            FROM "Classes" cl
            JOIN "StudentEnrollment" se ON se.class_id = cl.id
            LEFT JOIN "Teachers" t ON t.id = cl.teacher_id
            WHERE se.student_id = :stid
              AND cl.course_id = :cid
              AND {subject_clause}
            LIMIT 1
        """),
        {"stid": student_id, "cid": course_id, **({"sub": subject_id} if subject_id else {})},
    )
    return res.fetchone()


async def get_errata_warning(course_id: str, subject_id: Optional[str], section_number: int) -> str:
    """Errata Viva (Fase 6.6): devolve o bloco de aviso da seção ("" se não houver)."""
    try:
        subject_clause = "e.subject_id = :sub" if subject_id else "e.subject_id IS NULL"
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                text(f"""
                    SELECT e.correction FROM "Erratas" e
                    WHERE e.course_id = :cid
                      AND {subject_clause}
                      AND e.section_number = :section
                      AND e.is_active = true
                    ORDER BY e.created_at DESC
                """),
                {"cid": course_id, "section": section_number, **({"sub": subject_id} if subject_id else {})},
            )
            corrections = [r[0] for r in res.fetchall()]
        if not corrections:
            return ""
        return ERRATA_WARNING.format(correction="\n".join(f"• {c}" for c in corrections))
    except Exception as e:
        logger.warning("errata_lookup_failed", error=str(e)[:200])
        return ""


# ==============================================================================
# SCHEDULER — SUGESTÃO DE LEITURA (microdose)
# ==============================================================================

async def process_reading_plans():
    """Varre os ReadingPlans vencidos (next_trigger_at <= NOW, não pausados) e
    envia a sugestão de leitura — ou a oferta de recalcular, se o aluno não
    interagiu desde a última sugestão (o plano NUNCA acumula)."""
    now_utc = _naive_utc_now()
    default_ct = settings.CHANNEL_BACKEND.lower()

    from src.shared.privacy import guardian_consent_sql_clause
    consent_clause = guardian_consent_sql_clause("s")  # LGPD Art. 14 (P1.1)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text(f"""
                SELECT rp.id, rp.student_id, rp.course_id, rp.subject_id,
                       rp.material_type, rp.total_sections, rp.current_section,
                       rp.pace, rp.preferred_time, rp.streak_days,
                       rp.last_read_at, rp.last_suggestion_at,
                       s.name AS student_name, s.phone, s.telegram_chat_id, s.preferred_channel,
                       c.name AS course_name
                FROM "ReadingPlans" rp
                JOIN "Students" s ON s.id = rp.student_id
                JOIN "Courses" c ON c.id = rp.course_id
                WHERE rp.is_paused = false
                  AND rp.next_trigger_at IS NOT NULL
                  AND rp.next_trigger_at <= :now
                  AND s.is_active = true
                  AND c.is_active = true
                  {consent_clause}
                FOR UPDATE OF rp SKIP LOCKED
            """),
            {"now": now_utc},
        )
        plans = result.fetchall()

        if not plans:
            return

        logger.info("reading_plans_due", count=len(plans))

        for plan in plans:
            next_trigger = compute_reading_next_trigger(plan.pace, plan.preferred_time, now_utc)
            try:
                ct = (plan.preferred_channel or default_ct).lower()
                sender_id = plan.telegram_chat_id if ct == "telegram" else plan.phone

                # Reagenda SEMPRE (mesmo sem entrega): plano nunca trava.
                if not sender_id:
                    logger.warning("reading_skip_no_sender", plan_id=plan.id, channel=ct)
                    await _reschedule(session, plan.id, next_trigger)
                    continue

                # Política Meta: fora da janela de 24h não há entrega proativa
                # no WhatsApp (templates desativados). Telegram entrega sempre.
                if ct == "whatsapp" and not await _window_active(session, plan.student_id, now_utc):
                    logger.info("reading_skip_window_closed", plan_id=plan.id)
                    await _reschedule(session, plan.id, next_trigger)
                    continue

                channel = get_channel(ct)
                material_label = MATERIAL_LABELS.get(plan.material_type, "livro")

                # ── Replanejamento sem culpa ────────────────────────────────
                # Aluno não interagiu desde a última sugestão → NÃO acumula:
                # oferece recalcular o plano (sem mencionar "atraso").
                last_read = _as_naive(plan.last_read_at)
                last_sugg = _as_naive(plan.last_suggestion_at)
                if last_sugg is not None and (last_read is None or last_read < last_sugg):
                    await channel.send_buttons(
                        user_id=sender_id,
                        body=READING_RECALC_BODY.format(
                            material_label=material_label.capitalize(),
                            student_name=plan.student_name.split(" ")[0],
                        ),
                        buttons=[
                            {"id": f"reading_recalc_{plan.id}", "title": READING_RECALC_YES_TITLE[:20]},
                            {"id": f"reading_keep_{plan.id}", "title": READING_RECALC_KEEP_TITLE[:20]},
                        ],
                        footer="Sem pressa — o plano é seu!",
                    )
                    # last_suggestion_at=NULL: a pergunta não conta como
                    # sugestão; o próximo ciclo volta a sugerir a MESMA seção.
                    await session.execute(
                        text("""
                            UPDATE "ReadingPlans"
                            SET next_trigger_at = :next, last_suggestion_at = NULL,
                                next_checkin_at = NULL, updated_at = CURRENT_TIMESTAMP
                            WHERE id = :pid
                        """),
                        {"next": next_trigger, "pid": plan.id},
                    )
                    await session.commit()
                    track_event("reading_recalc_offered", {"plan_id": plan.id})
                    continue

                # ── Sugestão da microdose ───────────────────────────────────
                klass = await _get_class_context(session, plan.student_id, plan.course_id, plan.subject_id)
                active = parse_active_chapters(klass.active_chapters) if klass else None
                target = next_active_section(plan.current_section, plan.total_sections, active)

                if target is None:
                    # Nada mais a ler (concluído ou capítulos restantes
                    # desativados pelo professor): encerra os gatilhos.
                    await session.execute(
                        text("""
                            UPDATE "ReadingPlans"
                            SET is_paused = true, next_trigger_at = NULL,
                                next_checkin_at = NULL, updated_at = CURRENT_TIMESTAMP
                            WHERE id = :pid
                        """),
                        {"pid": plan.id},
                    )
                    await session.commit()
                    logger.info("reading_plan_exhausted", plan_id=plan.id)
                    continue

                section_label = section_label_for(plan.material_type, target)
                body = READING_SUGGESTION_BODY.format(
                    material_label=material_label.capitalize(),
                    student_name=plan.student_name.split(" ")[0],
                    section_label=f"{section_label} de {plan.total_sections}",
                    checkin_hour=_checkin_hour(plan.preferred_time),
                    progress_line=build_progress_line(
                        plan.current_section, plan.total_sections, plan.streak_days, plan.material_type
                    ),
                )
                # Errata Viva: avisa o leitor ANTES de ele ler a seção errada.
                body += await get_errata_warning(plan.course_id, plan.subject_id, target)

                await channel.send_text(sender_id, body)

                # H1.3 — Pílula de áudio (best-effort, atrás de flag): versão
                # falada da sugestão para os "tempos mortos" do leitor.
                try:
                    from src.shared.tts import synthesize, is_tts_available
                    if is_tts_available():
                        audio = await synthesize(body)
                        if audio:
                            await channel.send_audio(sender_id, audio)
                except Exception as e:
                    logger.warning("reading_tts_failed", plan_id=plan.id, error=str(e)[:200])

                await session.execute(
                    text("""
                        UPDATE "ReadingPlans"
                        SET next_trigger_at = :next,
                            last_suggestion_at = :now,
                            next_checkin_at = :checkin,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = :pid
                    """),
                    {
                        "next": next_trigger,
                        "now": now_utc,
                        "checkin": now_utc + timedelta(hours=CHECKIN_DELAY_HOURS),
                        "pid": plan.id,
                    },
                )
                await session.commit()

                track_event("reading_suggestion_sent", {
                    "plan_id": plan.id,
                    "student_id": plan.student_id,
                    "course_id": plan.course_id,
                    "section": target,
                })
                logger.info("reading_suggestion_sent", plan_id=plan.id, section=target, channel=ct)

            except Exception as e:
                logger.error("reading_plan_send_failed", plan_id=plan.id, error=str(e)[:500])
                await _reschedule(session, plan.id, next_trigger)


async def _reschedule(session, plan_id: str, next_trigger: datetime):
    """Reagenda o gatilho sem alterar o restante do plano (best-effort)."""
    try:
        await session.execute(
            text("""
                UPDATE "ReadingPlans"
                SET next_trigger_at = :next, updated_at = CURRENT_TIMESTAMP
                WHERE id = :pid
            """),
            {"next": next_trigger, "pid": plan_id},
        )
        await session.commit()
    except Exception as e:
        logger.error("reading_plan_reschedule_failed", plan_id=plan_id, error=str(e)[:300])


# ==============================================================================
# SCHEDULER — QUIZ DE CHECK-IN (o "portão" da próxima seção)
# ==============================================================================

async def process_reading_checkins():
    """Dispara o quiz de check-in das leituras sugeridas hoje (next_checkin_at
    vencido). Curto por design: 1 questão objetiva sobre o trecho — taxa de
    conclusão importa mais que profundidade no WhatsApp (limite do spec: 2-3).
    """
    now_utc = _naive_utc_now()
    default_ct = settings.CHANNEL_BACKEND.lower()

    from src.shared.privacy import guardian_consent_sql_clause
    consent_clause = guardian_consent_sql_clause("s")  # LGPD Art. 14 (P1.1)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text(f"""
                SELECT rp.id, rp.student_id, rp.course_id, rp.subject_id,
                       rp.material_type, rp.total_sections, rp.current_section,
                       rp.preferred_time, rp.streak_days,
                       s.name AS student_name, s.phone, s.telegram_chat_id, s.preferred_channel,
                       c.name AS course_name,
                       sub.name AS subject_name
                FROM "ReadingPlans" rp
                JOIN "Students" s ON s.id = rp.student_id
                JOIN "Courses" c ON c.id = rp.course_id
                LEFT JOIN "Subjects" sub ON sub.id = rp.subject_id
                WHERE rp.is_paused = false
                  AND rp.next_checkin_at IS NOT NULL
                  AND rp.next_checkin_at <= :now
                  AND s.is_active = true
                  AND c.is_active = true
                  {consent_clause}
                FOR UPDATE OF rp SKIP LOCKED
            """),
            {"now": now_utc},
        )
        plans = result.fetchall()

    if not plans:
        return

    logger.info("reading_checkins_due", count=len(plans))

    for plan in plans:
        try:
            # Desarma o gatilho ANTES de gerar (generate_review pode demorar;
            # outro ciclo do scheduler não pode reenviar o mesmo check-in).
            async with AsyncSessionLocal() as session:
                await session.execute(
                    text("""
                        UPDATE "ReadingPlans"
                        SET next_checkin_at = NULL, updated_at = CURRENT_TIMESTAMP
                        WHERE id = :pid
                    """),
                    {"pid": plan.id},
                )
                await session.commit()

                ct = (plan.preferred_channel or default_ct).lower()
                sender_id = plan.telegram_chat_id if ct == "telegram" else plan.phone
                if not sender_id:
                    continue
                if ct == "whatsapp" and not await _window_active(session, plan.student_id, now_utc):
                    logger.info("reading_checkin_window_closed", plan_id=plan.id)
                    continue

                klass = await _get_class_context(session, plan.student_id, plan.course_id, plan.subject_id)

            if not klass or not klass.teacher_id:
                logger.warning("reading_checkin_no_teacher", plan_id=plan.id)
                continue

            active = parse_active_chapters(klass.active_chapters)
            target = next_active_section(plan.current_section, plan.total_sections, active)
            if target is None:
                continue

            section_label = section_label_for(plan.material_type, target)

            # F1 — Listening: para CURSO DE IDIOMA (detectado pelo nome) com a
            # vertical + TTS ligados, o check-in vira "áudio do dia": diálogo na
            # língua-alvo (sintetizado) + a MESMA pergunta de compreensão (o portão
            # que já existe). Flags off ou curso normal ⇒ check-in idêntico ao atual.
            from src.shared.language import detect_language_course
            is_listening = bool(
                settings.LANGUAGE_MODE_ENABLED and settings.TTS_ENABLED
                and detect_language_course(plan.course_name)
            )

            # Gera (ou recupera do QuizBank) o pacote da seção — Cache Hit = $0.
            from src.rag.review_generator import generate_review
            base_kwargs = dict(
                teacher_id=klass.teacher_id,
                teacher_name=klass.teacher_name or "Professor",
                teacher_subject=klass.teacher_subject or "Geral",
                student_id=plan.student_id,
                student_name=plan.student_name,
                course_id=plan.course_id,
                course_name=plan.course_name,
                subject_name=plan.subject_name,
                current_lesson_number=target,
                lesson_label=section_label,
                subject_id=plan.subject_id,
                from_bank=True,
            )
            review = None
            if is_listening:
                # Cache/prompt 'listening'; RAG lê o material REAL da seção.
                review = await generate_review(
                    **base_kwargs, material_type="listening",
                    retrieval_material_type=plan.material_type or "lesson",
                )
                if review is None:
                    is_listening = False  # sem conteúdo → cai no check-in normal
            if review is None:
                review = await generate_review(**base_kwargs, material_type=plan.material_type)

            if not review:
                logger.warning("reading_checkin_no_content", plan_id=plan.id, section=target)
                continue

            channel = get_channel(ct)
            intro = READING_CHECKIN_INTRO.format(section_label=section_label)
            intro += await get_errata_warning(plan.course_id, plan.subject_id, target)
            await channel.send_text(sender_id, intro)

            # F1 — "áudio do dia": envia o diálogo (review.summary) como TEXTO
            # (fallback/acessibilidade) + ÁUDIO na língua-alvo (best-effort).
            if is_listening:
                from src.rag.review_generator import listening_directives, synthesize_listening
                target_lang = listening_directives(plan.course_name)["target_language"]
                await channel.send_text(sender_id, review.summary)
                try:
                    audio = await synthesize_listening(review.summary, target_lang)
                    if audio:
                        await channel.send_audio(sender_id, audio)
                except Exception as e:
                    logger.warning("listening_audio_failed", plan_id=plan.id, error=str(e)[:200])
                track_event("listening_sent", {
                    "plan_id": plan.id, "student_id": plan.student_id,
                    "section": target, "lang": target_lang,
                })

            from src.rag.quiz_generator import QuizOutput, QuizAlternative
            from src.workers.micro_learning import send_quiz_to_student

            quiz_output = QuizOutput(
                question=review.quiz.question,
                alternatives=[QuizAlternative(id=a.id, text=a.text) for a in review.quiz.alternatives],
                correct_answer=review.quiz.correct_answer,
                explanation=review.quiz.explanation,
            )
            await send_quiz_to_student(
                channel=channel,
                student={
                    "id": plan.student_id,
                    "sender_id": sender_id,
                    "course_id": plan.course_id,
                    "lesson_pointer": str(target),
                },
                quiz=quiz_output,
                quiz_bank_id=review.quiz_bank_id,
                reading_plan_id=plan.id,
            )

            track_event("reading_checkin_sent", {
                "plan_id": plan.id,
                "student_id": plan.student_id,
                "section": target,
            })
            logger.info("reading_checkin_sent", plan_id=plan.id, section=target)

        except Exception as e:
            logger.error("reading_checkin_failed", plan_id=plan.id, error=str(e)[:500])


# ==============================================================================
# PORTÃO — resposta do quiz de check-in (chamado pelo llm_worker)
# ==============================================================================

async def handle_reading_checkin_answer(reading_plan_id: str, section: int, is_correct: bool) -> Optional[str]:
    """Aplica o resultado do check-in no plano e devolve a mensagem extra a
    enviar após o feedback do quiz (progresso/streak ou convite à releitura).

    Acertou → desbloqueia: current_section avança para `section`, streak
    atualiza, last_read_at = agora. Errou → seção não avança (a próxima
    sugestão repete o trecho); last_read_at marca a interação (o detector de
    "sumiu" não dispara para quem está tentando).
    """
    now_utc = _naive_utc_now()
    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                text("""
                    SELECT id, student_id, material_type, total_sections,
                           current_section, streak_days, last_read_at
                    FROM "ReadingPlans"
                    WHERE id = :pid
                    FOR UPDATE
                """),
                {"pid": reading_plan_id},
            )
            plan = res.fetchone()
            if not plan:
                return None

            if not is_correct:
                await session.execute(
                    text("""
                        UPDATE "ReadingPlans"
                        SET last_read_at = :now, updated_at = CURRENT_TIMESTAMP
                        WHERE id = :pid
                    """),
                    {"now": now_utc, "pid": reading_plan_id},
                )
                await session.commit()
                section_label = section_label_for(plan.material_type, section)
                return READING_GATE_RETRY.format(section_label=section_label)

            new_section = max(plan.current_section, section)
            new_streak = update_streak(_as_naive(plan.last_read_at), now_utc, plan.streak_days)
            completed = new_section >= plan.total_sections

            await session.execute(
                text("""
                    UPDATE "ReadingPlans"
                    SET current_section = :section,
                        streak_days = :streak,
                        last_read_at = :now,
                        is_paused = CASE WHEN :completed THEN true ELSE is_paused END,
                        next_trigger_at = CASE WHEN :completed THEN NULL ELSE next_trigger_at END,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :pid
                """),
                {
                    "section": new_section,
                    "streak": new_streak,
                    "now": now_utc,
                    "completed": completed,
                    "pid": reading_plan_id,
                },
            )
            await session.commit()

            # Nome do aluno para a celebração de conclusão
            student_name = ""
            if completed:
                res_name = await session.execute(
                    text('SELECT name FROM "Students" WHERE id = :sid'),
                    {"sid": plan.student_id},
                )
                student_name = (res_name.scalar() or "").split(" ")[0]

        track_event("reading_gate_result", {
            "plan_id": reading_plan_id,
            "section": section,
            "passed": True,
            "completed": completed,
        })

        material_label = MATERIAL_LABELS.get(plan.material_type, "livro")
        if completed:
            return READING_COMPLETED.format(student_name=student_name or "aluno(a)", material_label=material_label)

        message = READING_GATE_PASSED.format(
            progress_line=build_progress_line(new_section, plan.total_sections, new_streak, plan.material_type)
        )
        # H1.4 — Medalha no marco exato (3/7/30/100 dias seguidos)
        medal = streak_medal(new_streak)
        if medal:
            from src.shared.constants.messages import STREAK_MEDAL_UNLOCKED
            message += STREAK_MEDAL_UNLOCKED.format(medal=medal, days=new_streak)
        return message
    except Exception as e:
        logger.error("reading_gate_failed", plan_id=reading_plan_id, error=str(e)[:400])
        return None


# ==============================================================================
# RAG SOCRÁTICO — contexto de livro/apostila para a conversa livre (Fase 5)
# ==============================================================================

# Detecção barata (regex) de pergunta "sobre o material denso".
BOOK_QUERY_REGEX = re.compile(
    r"\b(livro|cap[íi]tulos?|cap\.|apostilas?|exerc[íi]cios?|se[çc][õo]es|se[çc][ãa]o|p[áa]ginas?|quest[õo]es|quest[ãa]o)\b",
    re.IGNORECASE,
)

# "exercício 3.2" → (cap 3, ex 2) | "exercício 2 do capítulo 3" → (cap 3, ex 2)
_EX_DOTTED_REGEX = re.compile(r"\bexerc[íi]cio\s+(\d+)\.(\d+)\b", re.IGNORECASE)
_EX_PLAIN_REGEX = re.compile(r"\bexerc[íi]cio\s+(\d+)\b", re.IGNORECASE)
_CHAPTER_REGEX = re.compile(r"\b(?:cap[íi]tulo|cap\.?|se[çc][ãa]o)\s+(\d+)\b", re.IGNORECASE)

# Proteção de conteúdo: máx. 3 consultas RAG/minuto por aluno sobre o material.
READING_RAG_RATE_LIMIT = 3
READING_RAG_RATE_WINDOW = 60  # segundos


def extract_section_refs(query: str) -> list[str]:
    """Extrai referências canônicas de gabarito ("cap3_ex2") da pergunta.

    Cobre "exercício 3.2" (capítulo.exercício) e "exercício 2 do capítulo 3".
    """
    refs: list[str] = []
    dotted = _EX_DOTTED_REGEX.search(query)
    if dotted:
        refs.append(f"cap{dotted.group(1)}_ex{dotted.group(2)}")
    chapter = _CHAPTER_REGEX.search(query)
    plain = _EX_PLAIN_REGEX.search(query)
    if chapter and plain and not dotted:
        refs.append(f"cap{chapter.group(1)}_ex{plain.group(1)}")
    return refs


async def check_reading_rate_limit(student_id: str, course_id: str) -> bool:
    """True se a consulta está DENTRO do limite (3/min sobre o mesmo material).
    Falha do Redis nunca bloqueia o aluno (fail-open)."""
    try:
        from src.cache.redis_client import get_redis
        redis = await get_redis()
        key = f"ragrl:{student_id}:{course_id}:material"
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, READING_RAG_RATE_WINDOW)
        return count <= READING_RAG_RATE_LIMIT
    except Exception as e:
        logger.warning("reading_rate_limit_failed", error=str(e)[:200])
        return True


async def build_reading_rag_addons(
    teacher_id: str,
    student_id: str,
    course_id: str,
    subject_id: Optional[str],
    subject_name: Optional[str],
    query: str,
) -> dict:
    """Monta os complementos de RAG para perguntas sobre livro/apostila:

      - `context`    → chunks do material denso (filtro material_type +
                       capítulos ativos + teto anti-spoiler do plano)
      - `directives` → Modo Socrático + Gabarito Verificado + Errata Viva,
                       para anexar ao System Prompt
      - `rate_limited` → True se estourou as 3 consultas/min (proteção de
                       conteúdo) — o chamador responde sem LLM.

    Best-effort: qualquer falha devolve addons vazios (o fluxo clássico de
    aulas segue intocado).
    """
    from src.shared.constants.prompts import SOCRATIC_MODE_DIRECTIVE, VERIFIED_ANSWER_KEY_DIRECTIVE

    empty = {"context": "", "directives": "", "rate_limited": False}
    try:
        # Plano de leitura do aluno define material + teto de seções liberadas
        subject_clause = "rp.subject_id = :sub" if subject_id else "rp.subject_id IS NULL"
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                text(f"""
                    SELECT rp.material_type, rp.current_section, rp.total_sections
                    FROM "ReadingPlans" rp
                    WHERE rp.student_id = :stid AND rp.course_id = :cid AND {subject_clause}
                    ORDER BY rp.is_paused ASC, rp.updated_at DESC
                    LIMIT 1
                """),
                {"stid": student_id, "cid": course_id,
                 **({"sub": subject_id} if subject_id else {})},
            )
            plan = res.fetchone()
            klass = await _get_class_context(session, student_id, course_id, subject_id)

        # Rate limit de proteção de conteúdo (3/min sobre o mesmo material) —
        # checado DEPOIS do plano (SQL barato), ANTES do Qdrant (caro).
        if not await check_reading_rate_limit(student_id, course_id):
            return {**empty, "rate_limited": True}

        material_type = plan.material_type if plan else "book"
        # Anti-spoiler: o aluno enxerga até a seção em leitura (atual + 1).
        section_cap = (plan.current_section + 1) if plan else 9999
        active = parse_active_chapters(klass.active_chapters) if klass else None

        from src.rag.retriever import retrieve_context
        book_context = await retrieve_context(
            teacher_id=teacher_id,
            course_id=course_id,
            subject_name=subject_name,
            current_lesson_number=section_cap,
            query=query,
            material_type=material_type,
            allowed_sections=active,
        )

        # Turma sem material denso (nem plano, nem vetores de livro/apostila):
        # o Addon fica INERTE — o fluxo clássico de aulas segue intocado, sem
        # Modo Socrático e sem mensagens sobre o "livro".
        if not plan and not book_context:
            return empty

        directives = SOCRATIC_MODE_DIRECTIVE

        # Gabarito Verificado: resposta oficial como âncora (anti-alucinação)
        refs = extract_section_refs(query)
        if refs:
            subject_clause_ak = "ak.subject_id = :sub" if subject_id else "ak.subject_id IS NULL"
            async with AsyncSessionLocal() as session:
                for ref in refs:  # no máximo 2 referências por pergunta
                    res = await session.execute(
                        text(f"""
                            SELECT ak.section_ref, ak.correct_answer, ak.explanation
                            FROM "AnswerKeys" ak
                            WHERE ak.course_id = :cid AND {subject_clause_ak}
                              AND ak.section_ref = :ref
                            LIMIT 1
                        """),
                        {"cid": course_id, "ref": ref,
                         **({"sub": subject_id} if subject_id else {})},
                    )
                    key = res.fetchone()
                    if not key:
                        continue
                    explanation_block = f"Explicação oficial: {key.explanation}\n" if key.explanation else ""
                    directives += VERIFIED_ANSWER_KEY_DIRECTIVE.format(
                        section_ref=key.section_ref,
                        correct_answer=key.correct_answer,
                        explanation_block=explanation_block,
                    )

        # Errata Viva: seção citada na pergunta ou a seção em leitura
        chapter_match = _CHAPTER_REGEX.search(query)
        errata_section = int(chapter_match.group(1)) if chapter_match else (
            plan.current_section + 1 if plan else None
        )
        errata_note = ""
        if errata_section is not None:
            errata_note = await get_errata_warning(course_id, subject_id, errata_section)
        if errata_note:
            directives += (
                "\nERRATA OFICIAL desta seção (cadastrada pelo professor — mencione ao aluno):"
                f"{errata_note}\n"
            )

        return {"context": book_context, "directives": directives, "rate_limited": False}
    except Exception as e:
        logger.error("reading_rag_addons_failed", error=str(e)[:300])
        return empty


# ==============================================================================
# BOTÕES DE REPLANEJAMENTO (fast-path do llm_worker)
# ==============================================================================

async def recalc_reading_plan(plan_id: str) -> bool:
    """"✅ Ajustar plano": recalcula a partir de AGORA, sem penalidades — o
    ponteiro continua de onde parou e o próximo gatilho volta ao horário
    preferido. Retorna False se o plano não existir mais."""
    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                text('SELECT pace, preferred_time FROM "ReadingPlans" WHERE id = :pid'),
                {"pid": plan_id},
            )
            plan = res.fetchone()
            if not plan:
                return False
            next_trigger = compute_reading_next_trigger(plan.pace, plan.preferred_time)
            await session.execute(
                text("""
                    UPDATE "ReadingPlans"
                    SET next_trigger_at = :next, last_suggestion_at = NULL,
                        next_checkin_at = NULL, is_paused = false,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = :pid
                """),
                {"next": next_trigger, "pid": plan_id},
            )
            await session.commit()
        track_event("reading_plan_recalculated", {"plan_id": plan_id})
        return True
    except Exception as e:
        logger.error("reading_recalc_failed", plan_id=plan_id, error=str(e)[:300])
        return False
