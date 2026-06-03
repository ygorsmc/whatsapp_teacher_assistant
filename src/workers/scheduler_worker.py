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
from src.shared.whatsapp_interactive import send_template_message, send_reply_buttons
from src.shared.constants.messages import REVIEW_INVITE_BODY, REVIEW_INVITE_FOOTER, REVIEW_ACCEPT_TITLE, REVIEW_LATER_TITLE
from src.cache.redis_client import get_redis
from src.db.database import AsyncSessionLocal, init_db

logger = structlog.get_logger(__name__)

# BRT offset (UTC-3, fixed — no DST since 2019)
BRT_OFFSET = timedelta(hours=-3)

# Scheduler poll interval (seconds)
SCHEDULER_INTERVAL = 300  # 5 minutes


def now_brt() -> datetime:
    """Returns current datetime in BRT (UTC-3)."""
    return datetime.now(timezone.utc) + BRT_OFFSET

LAST_USER_SYNC_DATE = None

async def perform_daily_user_sync():
    """Sincroniza alunos e professores da planilha do Google todos os dias de madrugada (03:00 BRT)."""
    global LAST_USER_SYNC_DATE
    
    agora_brt = now_brt()
    hoje = agora_brt.date()
    
    # Executa entre 03:00 e 03:59 BRT
    if agora_brt.hour == 3:
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
    base = from_date or now_brt()
    hour, minute = map(int, preferred_hour.split(":"))

    # Build target time in BRT
    target_brt = base.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # If target already passed today, schedule for tomorrow
    if target_brt <= base:
        target_brt += timedelta(days=1)

    # Convert BRT to UTC for storage
    target_utc = target_brt - BRT_OFFSET
    return target_utc.replace(tzinfo=timezone.utc)


async def process_pending_schedules():
    """Finds and processes all student schedules that are due."""
    now_utc = datetime.now(timezone.utc)
    redis = await get_redis()

    async with AsyncSessionLocal() as session:
        # Fetch all due schedules with student and course info
        result = await session.execute(
            text("""
                SELECT ss.schedule_id, ss.student_id, ss.course_id, ss.template_name,
                       ss.preferred_hour, ss.content_type,
                       s.name AS student_name, s.phone,
                       c.name AS course_name
                FROM StudentSchedule ss
                JOIN Students s ON s.id = ss.student_id
                JOIN Courses c ON c.id = ss.course_id
                WHERE ss.status = 'scheduled'
                  AND ss.next_trigger_date <= :now
                  AND ss.is_active = 1
                  AND s.is_active = 1
                  AND c.is_active = 1
            """),
            {"now": now_utc.strftime("%Y-%m-%d %H:%M:%S")},
        )
        pending = result.fetchall()

        if not pending:
            logger.debug("scheduler_no_pending", checked_at=now_utc.isoformat())
            return

        logger.info("scheduler_found_pending", count=len(pending))

        for row in pending:
            phone = row.phone
            schedule_id = row.schedule_id

            try:
                # Check if service window is already open (avoid redundant template)
                window_key = f"service_window:{phone}"
                window_active = await redis.exists(window_key)

                # Torna o título único (encurta o nome do curso se necessário)
                course_short = row.course_name[:10]
                accept_title = f"{REVIEW_ACCEPT_TITLE} {course_short}"
                later_title = f"{REVIEW_LATER_TITLE} {course_short}"

                if window_active:
                    logger.info("scheduler_window_active_direct_send", phone=phone, schedule_id=schedule_id)
                    # Window is already open! Skip the Meta Template and simulate the user clicking "Sim, vamos lá!"
                    # This triggers the fast-path directly and sends the review + quiz for free.
                    from src.shared.service_bus import service_bus
                    import uuid
                    
                    interactive_payload = {
                        "type": "button_reply",
                        "id": f"review_accept_{row.course_id}",
                        "title": accept_title[:20]
                    }
                    
                    await service_bus.enqueue(
                        queue="inbound_messages",
                        payload={
                            "phone": phone,
                            "text": interactive_payload["title"],
                            "message_type": "interactive",
                            "interactive_payload": interactive_payload,
                            "correlation_id": str(uuid.uuid4()),
                            "message_id": f"auto_{schedule_id}_{int(now_utc.timestamp())}"
                        },
                        session_id=phone
                    )
                else:
                    logger.info("scheduler_window_closed_sending_invite", phone=phone, schedule_id=schedule_id)
                    # Window is closed. Send the review invitation via interactive buttons (Template)
                    # This opens the 24h service window when the student responds
                    await send_reply_buttons(
                        phone=phone,
                        body_text=REVIEW_INVITE_BODY.format(course_name=row.course_name, student_name=row.student_name),
                        buttons=[
                            {"id": f"review_accept_{row.course_id}", "title": accept_title[:20]},
                            {"id": f"review_later_{row.course_id}", "title": later_title[:20]},
                        ],
                        footer=REVIEW_INVITE_FOOTER,
                    )

                # Mark as sent and compute next trigger
                next_trigger = compute_next_trigger(row.preferred_hour)
                await session.execute(
                    text("""
                        UPDATE StudentSchedule
                        SET status = 'scheduled',
                            last_sent_at = :now,
                            next_trigger_date = :next_trigger
                        WHERE schedule_id = :sid
                    """),
                    {
                        "now": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
                        "next_trigger": next_trigger.strftime("%Y-%m-%d %H:%M:%S"),
                        "sid": schedule_id,
                    },
                )
                await session.commit()

                track_event("scheduler_template_sent" if not window_active else "scheduler_direct_sent", {
                    "phone": phone,
                    "course_id": row.course_id,
                    "preferred_hour": row.preferred_hour,
                    "next_trigger": next_trigger.isoformat(),
                })

                logger.info(
                    "scheduler_sent",
                    phone=phone,
                    course_id=row.course_id,
                    next_trigger=next_trigger.isoformat(),
                    mode="direct" if window_active else "invite"
                )

            except Exception as e:
                logger.error(
                    "scheduler_send_failed",
                    phone=phone,
                    schedule_id=schedule_id,
                    error=str(e)[:500],
                )
                # Mark as failed but don't stop processing others
                await session.execute(
                    text("""
                        UPDATE StudentSchedule
                        SET status = 'failed'
                        WHERE schedule_id = :sid
                    """),
                    {"sid": schedule_id},
                )
                await session.commit()


async def advance_classes_pointer():
    """Verifica e avança as turmas com base na data de hoje e os dias de aula marcados na planilha."""
    import json
    brt = timezone(timedelta(hours=-3))
    hoje = datetime.now(brt).date()
    hoje_str = hoje.isoformat()
    ontem = hoje - timedelta(days=1)
    ontem_weekday = str(ontem.weekday()) # 0=Monday, 6=Sunday
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT id, start_date, schedule_map, current_lesson_number, last_advanced_date FROM Classes"))
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
            except:
                smap = {}
                
            if last_adv_str and str(last_adv_str)[:10] == hoje_str:
                continue
                
            new_lesson = current_lesson
            
            # Condicional de Inicio: Se hoje é o dia de começar e estava no 0, vai pra Aula 1.
            if current_lesson == 0 and start_date_iso == hoje_str:
                new_lesson = 1
            # Se a turma já iniciou, avança no dia SEGUINTE à aula
            elif current_lesson >= 1:
                if ontem_weekday in smap:
                    new_lesson = current_lesson + 1
                    
            if new_lesson == current_lesson:
                continue
            
            await session.execute(
                text("""
                    UPDATE Classes
                    SET current_lesson_number = :num, last_advanced_date = :hoje, updated_at = CURRENT_TIMESTAMP
                    WHERE id = :cid
                """),
                {"num": new_lesson, "hoje": hoje_str, "cid": class_id}
            )
            advanced_count += 1
            logger.info("class_pointer_advanced_auto", class_id=class_id, old_lesson=current_lesson, new_lesson=new_lesson)
            
        if advanced_count > 0:
            await session.commit()


async def scheduler_loop():
    """Main scheduler loop — polls every 5 minutes."""
    logger.info("scheduler_started", interval_seconds=SCHEDULER_INTERVAL)

    # Ensure DB tables exist (for local dev)
    await init_db()

    while True:
        try:
            await perform_daily_user_sync()
            await advance_classes_pointer()
            await process_pending_schedules()
        except Exception as e:
            logger.error("scheduler_loop_error", error=str(e)[:500])

        await asyncio.sleep(SCHEDULER_INTERVAL)


if __name__ == "__main__":
    from src.shared.telemetry import setup_telemetry
    setup_telemetry(settings.LOG_LEVEL)
    asyncio.run(scheduler_loop())
