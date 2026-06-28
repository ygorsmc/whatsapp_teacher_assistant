"""
Scheduler Worker — Sends daily review templates to students at their preferred time slots.

Runs as a periodic loop (equivalent to Azure Function Timer Trigger locally).
Executes every 5 minutes and checks for students whose next_trigger_date <= now.

Time slots: 07:00, 12:00, 18:00 BRT (UTC-3 fixed — Brazil hasn't observed DST since 2019).

Flow:
    1. Query StudentSchedule for pending triggers
    2. Check if service window is already open (skip if active)
    3. Send Utility Template → opens 24h window
    4. Mark as sent, schedule next trigger for tomorrow same hour
    5. When student responds → LLM Worker generates review + quiz
"""

import asyncio
import structlog
from datetime import datetime, timezone, timedelta
from sqlalchemy import text

from src.shared.config import settings
from src.shared.telemetry import track_event
from src.channel.factory import get_channel
from src.shared.constants.messages import REVIEW_ACCEPT_TITLE
from src.db.database import AsyncSessionLocal, init_db

logger = structlog.get_logger(__name__)

# Local TZ offset (configurable, default UTC-3)
LOCAL_TZ_OFFSET = timedelta(hours=settings.TIMEZONE_OFFSET_HOURS)

# Scheduler poll interval (seconds)
SCHEDULER_INTERVAL = 300  # 5 minutes


def now_local() -> datetime:
    """Returns current datetime in Local TZ."""
    return datetime.now(timezone.utc) + LOCAL_TZ_OFFSET

LAST_USER_SYNC_DATE = None

async def perform_daily_user_sync():
    """Sincroniza alunos e professores da planilha do Google todos os dias de madrugada (03:00 BRT)."""
    global LAST_USER_SYNC_DATE
    
    agora_local = now_local()
    hoje = agora_local.date()
    
    # Executa entre 03:00 e 03:59 Local Time
    if agora_local.hour == 3:
        if LAST_USER_SYNC_DATE != hoje:
            from src.drive.spreadsheet_sync import sync_users_from_sheet
            try:
                logger.info("scheduler_daily_sync_started", date=hoje.isoformat())
                stats = await sync_users_from_sheet()
                LAST_USER_SYNC_DATE = hoje
                logger.info("scheduler_daily_sync_success", stats=stats)
            except Exception as e:
                logger.error("scheduler_daily_sync_failed", error=str(e)[:500])



def compute_next_trigger(preferred_hour: str, from_date: datetime | None = None) -> datetime:
    """Computes the next trigger datetime (UTC) for a given BRT time slot.

    Args:
        preferred_hour: Time slot in BRT format "HH:MM" (e.g., "07:00", "12:00", "18:00")
        from_date: Base date to calculate from (defaults to now_brt())

    Returns:
        Next trigger datetime in UTC
    """
    base = from_date or now_local()
    hour, minute = map(int, preferred_hour.split(":"))

    # Build target time in Local TZ
    target_local = base.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # If target already passed today, schedule for tomorrow
    if target_local <= base:
        target_local += timedelta(days=1)

    # Convert Local TZ to UTC for storage
    target_utc = target_local - LOCAL_TZ_OFFSET
    return target_utc.replace(tzinfo=None)


async def process_pending_schedules():
    """Finds and processes all student schedules that are due."""
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    default_ct = settings.CHANNEL_BACKEND.lower()
    # LGPD Art. 14 (P1.1): exclui criança sem opt-in do responsável das
    # entregas proativas (string vazia com a flag off — comportamento atual).
    from src.shared.privacy import guardian_consent_sql_clause, messaging_optout_clause
    consent_clause = guardian_consent_sql_clause("s") + messaging_optout_clause("s")  # P1.1 + F9 opt-out

    async with AsyncSessionLocal() as session:
        # Busca os agendamentos vencidos com os DOIS endereços do aluno + o canal preferido.
        # O canal de entrega é resolvido por aluno (roteamento "follow-the-user").
        result = await session.execute(
            text(f"""
                SELECT ss.schedule_id, ss.student_id, ss.class_id, cl.course_id AS course_id,
                       ss.template_name, ss.preferred_hour, ss.content_type,
                       s.name AS student_name, s.phone, s.telegram_chat_id, s.preferred_channel,
                       c.name AS course_name
                FROM "StudentSchedule" ss
                JOIN "Students" s ON s.id = ss.student_id
                JOIN "Classes" cl ON cl.id = ss.class_id
                JOIN "Courses" c ON c.id = cl.course_id
                WHERE ss.status = 'scheduled'
                  AND ss.next_trigger_date <= :now
                  AND ss.is_active = true
                  AND s.is_active = true
                  AND c.is_active = true
                  AND cl.current_lesson_number > 0
                  {consent_clause}
                FOR UPDATE OF ss SKIP LOCKED
            """),
            {"now": now_utc},
        )
        pending = result.fetchall()

        if not pending:
            logger.debug("scheduler_no_pending", checked_at=now_utc.isoformat())
            return

        logger.info("scheduler_found_pending", count=len(pending))

        for row in pending:
            schedule_id = row.schedule_id

            # Roteamento "follow-the-user": entrega no canal onde o aluno falou por
            # último (preferred_channel); na ausência, usa o canal global.
            ct = (row.preferred_channel or default_ct).lower()
            sender_id = row.telegram_chat_id if ct == "telegram" else row.phone

            if not sender_id:
                # Agendamento existe, mas o aluno não tem endereço no canal preferido.
                # No Telegram isso significa que ainda não vinculou a conta (telegram_chat_id NULL),
                # então o quiz NUNCA é disparado. Logamos em WARNING para não ficar invisível.
                logger.warning(
                    "scheduler_skip_no_sender_id",
                    schedule_id=schedule_id,
                    student_id=row.student_id,
                    student_name=row.student_name,
                    channel=ct,
                    preferred_hour=row.preferred_hour,
                    hint="Aluno sem endereço no canal preferido (ex.: telegram_chat_id NULL). Precisa vincular a conta no bot.",
                )
                track_event("scheduler_skip_no_sender_id", {
                    "schedule_id": schedule_id,
                    "student_id": row.student_id,
                    "channel": ct,
                })
                continue

            try:
                # Check if service window is already open (SQL Telemetry 24h fallback)
                twenty_four_hours_ago = now_utc - timedelta(hours=24)
                
                res = await session.execute(
                    text("""
                        SELECT MAX(timestamp) 
                        FROM "StudentInteractions" 
                        WHERE student_id = :sid 
                          AND interaction_type IN ('message_received', 'quiz_answered')
                    """),
                    {"sid": row.student_id}
                )
                last_interaction = res.scalar()
                
                window_active = False
                if last_interaction:
                    # Parse safely in case DB driver returns string
                    if isinstance(last_interaction, str):
                        last_interaction = datetime.fromisoformat(last_interaction)
                    if last_interaction.tzinfo is not None:
                        last_interaction = last_interaction.replace(tzinfo=None)
                        
                    if last_interaction > twenty_four_hours_ago:
                        window_active = True


                # Torna o título único (encurta o nome do curso se necessário)
                course_short = row.course_name[:10]
                accept_title = f"{REVIEW_ACCEPT_TITLE} {course_short}"

                if ct == "telegram" or window_active:
                    logger.info("scheduler_window_active_direct_send", sender_id=sender_id, schedule_id=schedule_id, channel=ct)
                    # A pedido do professor: nas ocasiões normais (janela ativa ou telegram), 
                    # a revisão deve ser enviada de forma DIRETA, pulando os botões de "você tem 5 minutinhos".
                    # Simulamos o clique no botão para o llm_worker gerar e entregar a aula imediatamente.
                    from src.queue.factory import get_queue
                    import uuid
                    
                    interactive_payload = {
                        "type": "button_reply",
                        "id": f"review_accept_{row.class_id}",
                        "title": accept_title[:20]
                    }
                    
                    await get_queue().enqueue(
                        queue="inbound_messages",
                        payload={
                            "sender_id": sender_id,
                            "text": interactive_payload["title"],
                            "message_type": "interactive",
                            "interactive_payload": interactive_payload,
                            "correlation_id": str(uuid.uuid4()),
                            "message_id": f"auto_{schedule_id}_{int(now_utc.timestamp())}",
                            "channel": ct
                        },
                        session_id=sender_id
                    )
                else:
                    logger.info("scheduler_window_closed_skipping", sender_id=sender_id, schedule_id=schedule_id)
                    # A pedido do professor: O envio de mensagens via template (fora da janela de 24h) 
                    # foi DESATIVADO temporariamente para evitar risco de banimento por SPAM pela Meta.
                    pass

                # Mark as sent and compute next trigger
                next_trigger = compute_next_trigger(row.preferred_hour)
                await session.execute(
                    text("""
                        UPDATE "StudentSchedule"
                        SET status = 'scheduled',
                            last_sent_at = :now,
                            next_trigger_date = :next_trigger
                        WHERE schedule_id = :sid
                    """),
                    {
                        "now": now_utc,
                        "next_trigger": next_trigger,
                        "sid": schedule_id,
                    },
                )
                await session.commit()

                track_event("scheduler_template_sent" if not window_active else "scheduler_direct_sent", {
                    "sender_id": sender_id,
                    "course_id": row.course_id,
                    "preferred_hour": row.preferred_hour,
                    "next_trigger": next_trigger.isoformat(),
                })

                logger.info(
                    "scheduler_sent",
                    sender_id=sender_id,
                    course_id=row.course_id,
                    next_trigger=next_trigger.isoformat(),
                    mode="direct" if window_active else "invite"
                )

            except Exception as e:
                logger.error(
                    "scheduler_send_failed",
                    sender_id=sender_id,
                    schedule_id=schedule_id,
                    error=str(e)[:500],
                )
                # Mark as failed but don't stop processing others
                await session.execute(
                    text("""
                        UPDATE "StudentSchedule"
                        SET status = 'failed'
                        WHERE schedule_id = :sid
                    """),
                    {"sid": schedule_id},
                )
                await session.commit()


async def advance_classes_pointer():
    """Verifica e avança as turmas com base na data de hoje e o horário marcado na planilha, disparando a Sessão Mista."""
    import json
    brt = timezone(timedelta(hours=-3))
    agora = datetime.now(brt)
    hoje_str = agora.date().isoformat()
    weekday = str(agora.weekday()) # 0=Monday, 6=Sunday
    
    from src.queue.factory import get_queue
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(text('SELECT id, course_id, start_date, schedule_map, current_lesson_number, last_advanced_date FROM "Classes"'))
        classes = result.fetchall()
        
        advanced_count = 0
        for c in classes:
            class_id = c.id
            start_date_str = c.start_date
            schedule_map_str = c.schedule_map
            current_lesson = c.current_lesson_number
            last_adv_str = c.last_advanced_date
            
            if not schedule_map_str:
                continue
                
            start_date_iso = None
            if start_date_str:
                start_date_iso = str(start_date_str)[:10]
                if start_date_iso > hoje_str:
                    continue
                    
            try:
                smap = json.loads(schedule_map_str)
            except Exception:
                continue
                
            # Compara last_advanced_date convertida para BRT com a data de hoje em BRT
            if last_adv_str:
                last_adv_dt = last_adv_str if isinstance(last_adv_str, datetime) else datetime.fromisoformat(str(last_adv_str))
                if last_adv_dt.tzinfo is None:
                    last_adv_dt = last_adv_dt.replace(tzinfo=timezone.utc)
                last_adv_brt = last_adv_dt.astimezone(brt).date().isoformat()
                if last_adv_brt == hoje_str:
                    continue
            
            if weekday not in smap:
                continue
                
            class_time_str = smap[weekday]
            try:
                class_time = datetime.strptime(class_time_str[:5], "%H:%M").time()
            except ValueError:
                continue
                
            if agora.time() >= class_time:
                new_lesson = current_lesson + 1 if current_lesson >= 1 else 1
                
                # Grava last_advanced_date em UTC explicitamente (offset-naive para asyncpg)
                now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
                await session.execute(
                    text("""
                        UPDATE "Classes"
                        SET current_lesson_number = :num, last_advanced_date = :adv_date, updated_at = :adv_date
                        WHERE id = :cid
                    """),
                    {"num": new_lesson, "cid": class_id, "adv_date": now_utc}
                )
                advanced_count += 1
                logger.info("class_pointer_advanced_auto", class_id=class_id, old_lesson=current_lesson, new_lesson=new_lesson)
                
                # Dispara o evento de Sessão Mista para gerar e enfileirar os quizzes da aula
                await get_queue().enqueue(
                    queue="command_messages",
                    payload={
                        "action": "class_session_start",
                        "class_id": class_id,
                        "course_id": c.course_id,
                        "lesson_number": new_lesson,
                        "correlation_id": f"sess_{class_id}_{new_lesson}_{int(agora.timestamp())}"
                    },
                    session_id=class_id
                )
            
        if advanced_count > 0:
            await session.commit()

async def process_window_extensions():
    """Busca alunos ociosos (23h30m a 23h50m) e envia aviso interativo para maximizar a janela da Meta."""
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    min_idle_time = timedelta(hours=23, minutes=30)
    max_idle_time = timedelta(hours=23, minutes=50)

    default_ct = settings.CHANNEL_BACKEND.lower()
    async with AsyncSessionLocal() as session:
        # Estudantes ativos com os dois endereços + canal preferido + um curso (p/ o botão).
        # course_id vem da matrícula (a tabela Students não tem essa coluna).
        result = await session.execute(
            text("""
                SELECT s.id as student_id, s.phone, s.telegram_chat_id, s.preferred_channel,
                       (SELECT se.class_id
                          FROM "StudentEnrollment" se
                         WHERE se.student_id = s.id
                         LIMIT 1) AS class_id
                FROM "Students" s
                WHERE s.is_active = true
            """)
        )
        students = result.fetchall()

        for student in students:
            # Roteamento "follow-the-user": resolve canal + endereço do aluno.
            ct = (student.preferred_channel or default_ct).lower()
            sender_id = student.telegram_chat_id if ct == "telegram" else student.phone
            if not sender_id or not student.class_id:
                continue
            channel = get_channel(ct)

            # Pega a última interação real do aluno
            res_last = await session.execute(
                text("""
                    SELECT MAX(timestamp) 
                    FROM "StudentInteractions" 
                    WHERE student_id = :sid 
                      AND interaction_type IN ('message_received', 'quiz_answered')
                """),
                {"sid": student.student_id}
            )
            last_interaction = res_last.scalar()

            if not last_interaction:
                continue

            idle_duration = now_utc - last_interaction

            if min_idle_time <= idle_duration <= max_idle_time:
                # Verifica se já mandamos o aviso NESTE ciclo (ou seja, depois da última interação)
                res_warn = await session.execute(
                    text("""
                        SELECT 1 FROM "StudentInteractions"
                        WHERE student_id = :sid
                          AND interaction_type = 'window_closing_warning_sent'
                          AND timestamp > :last_int
                    """),
                    {"sid": student.student_id, "last_int": last_interaction}
                )
                already_warned = res_warn.scalar()

                if not already_warned:
                    logger.info("sending_window_extension_warning", student_id=student.student_id, idle=str(idle_duration))

                    class_id = student.class_id
                    footer = "Mantenha seus estudos em dia!"

                    # Gamificação (Fase 0) — REÚSO do slot 23:30 (zero slot novo):
                    # vira "salvar streak" para quem tem sequência ≥ 3 e ainda não
                    # agiu hoje. Flag OFF ⇒ None ⇒ cópia padrão byte-idêntica.
                    streak_copy = None
                    try:
                        from src.workers.gamification import maybe_streak_save_copy
                        streak_copy = await maybe_streak_save_copy(session, student.student_id, class_id)
                    except Exception as e:
                        logger.error("streak_save_copy_failed", error=str(e)[:200])

                    if streak_copy:
                        body_text, buttons = streak_copy
                        footer = "Sua sequência conta! 🔥"
                    else:
                        body_text = (
                            "⏰ *Aviso Importante*\n\n"
                            "Para que eu possa continuar enviando seus quizzes e dicas sem interrupções "
                            "nos próximos horários, preciso que você interaja comigo!\n\n"
                            "Quer aproveitar e responder um quiz rápido agora ou prefere deixar para o próximo agendamento?"
                        )
                        buttons = [
                            {"id": f"review_accept_{class_id}", "title": "Responder Quiz Agora"},
                            {"id": "window_warning_later", "title": "Responder Depois"}
                        ]

                    # No Telegram, usamos list / buttons emulados
                    # No WhatsApp, max 3 botões usa send_buttons
                    await channel.send_buttons(
                        user_id=sender_id,
                        body=body_text,
                        buttons=buttons,
                        footer=footer
                    )
                    
                    from uuid import uuid4
                    await session.execute(
                        text("""
                            INSERT INTO "StudentInteractions" (id, student_id, interaction_type)
                            VALUES (:id, :sid, 'window_closing_warning_sent')
                        """),
                        {"id": str(uuid4()), "sid": student.student_id}
                    )
                    await session.commit()
                    
                    track_event("window_extension_sent", {
                        "student_id": student.student_id,
                        "idle_seconds": idle_duration.total_seconds()
                    })


async def scheduler_loop():
    """Main scheduler loop — polls every 5 minutes."""
    logger.info("scheduler_started", interval_seconds=SCHEDULER_INTERVAL)

    # Ensure DB tables exist (for local dev)
    await init_db()

    # Addon de Livros + Horizontes H1-H3: cada etapa é isolada em try/except
    # próprio — uma falha em qualquer feature nova NUNCA derruba o fluxo
    # clássico de revisões/quizzes.
    from src.workers.reading_journey import process_reading_plans, process_reading_checkins
    from src.workers.spaced_repetition import process_spaced_repetition
    from src.workers.challenges import process_challenge_closures
    from src.workers.sentinel import process_sentinel_alerts
    from src.workers.editorial_report import process_monthly_editorial_report
    from src.workers.proficiency_report import process_monthly_proficiency_report  # F6 idiomas
    # LGPD (Plano P1.1/P1.3): consentimento parental e retenção — ambos atrás
    # de flag própria (no-op com a flag off; padrão da casa).
    from src.workers.guardian_consent import process_guardian_consent_requests
    from src.workers.retention import run_data_retention
    # Gamificação (Fase 0): snapshot da liga + resumo semanal (1 template/semana).
    # Atrás de flag própria (no-op com a flag off; padrão da casa).
    from src.workers.gamification import process_weekly_leaderboard

    while True:
        try:
            await perform_daily_user_sync()
            await advance_classes_pointer()
            await process_pending_schedules()
            await process_window_extensions()
        except Exception as e:
            logger.error("scheduler_loop_error", error=str(e)[:500])

        for step_name, step in (
            ("reading_plans", process_reading_plans),
            ("reading_checkins", process_reading_checkins),
            ("spaced_repetition", process_spaced_repetition),
            ("challenge_closures", process_challenge_closures),     # H1.1/H2.3
            ("sentinel_alerts", process_sentinel_alerts),           # H1.2 (1x/dia)
            ("editorial_report", process_monthly_editorial_report), # H2.4 (1x/mês)
            ("proficiency_report", process_monthly_proficiency_report), # F6 idiomas (1x/mês, flag)
            ("guardian_consent", process_guardian_consent_requests),# LGPD P1.1 (flag)
            ("data_retention", run_data_retention),                 # LGPD P1.3 (1x/mês, flag)
            ("weekly_leaderboard", process_weekly_leaderboard),     # Gamificação (1x/semana, flag)
        ):
            try:
                await step()
            except Exception as e:
                logger.error("scheduler_addon_step_error", step=step_name, error=str(e)[:500])

        await asyncio.sleep(SCHEDULER_INTERVAL)


if __name__ == "__main__":
    from src.shared.telemetry import setup_telemetry
    setup_telemetry(settings.LOG_LEVEL)
    asyncio.run(scheduler_loop())
