import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy import text
from src.db.database import init_db, AsyncSessionLocal, ensure_bootstrap_admin, BOOTSTRAP_ADMIN_ID
from src.shared.config import settings
import src.db.models  # Ensure models are registered with Base

# BRT offset (UTC-3)
BRT_OFFSET = timedelta(hours=-3)


def _assert_local_db() -> None:
    """Impede que este seed DESTRUTIVO rode contra um banco remoto (produção).

    O script faz DELETE em todas as tabelas e insere dados fictícios
    (ex.: 'Professor Teste', id 00000000-...-000000000001). Rodá-lo contra
    o PostgreSQL de produção já apagou dados reais e injetou um professor de
    teste cujo telegram_chat_id passou a "roubar" a identidade do professor
    real, quebrando /quiz e /aula. Para popular produção use a sincronização
    da planilha (/sinc_alunos), nunca este seed.

    Permite apenas SQLite ou Postgres em localhost/127.0.0.1.
    """
    from urllib.parse import urlparse

    url = settings.DATABASE_URL
    if url.lower().startswith("sqlite"):
        return
    host = (urlparse(url.split("+", 1)[0] + "://" + url.split("://", 1)[1]).hostname
            if "://" in url else None)
    if host in ("localhost", "127.0.0.1", "::1", None, ""):
        return
    raise SystemExit(
        f"❌ Seed recusado: DATABASE_URL aponta para host remoto '{host}'.\n"
        "   Este script é destrutivo (apaga TODAS as tabelas) e só deve rodar localmente\n"
        "   (SQLite ou Postgres em localhost).\n"
        "   Para popular produção, use a sincronização da planilha (comando /sinc_alunos)."
    )

# Time slots for review messages
REVIEW_TIME_SLOTS = ["07:00", "12:00", "18:00"]


def compute_next_trigger_utc(preferred_hour: str) -> datetime:
    """Computes the next trigger datetime in UTC for a BRT time slot."""
    now_brt = datetime.now(timezone.utc) + BRT_OFFSET
    hour, minute = map(int, preferred_hour.split(":"))
    target_brt = now_brt.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target_brt <= now_brt:
        target_brt += timedelta(days=1)
    target_utc = target_brt - BRT_OFFSET
    return target_utc.replace(tzinfo=None)  # SQLite doesn't handle tz-aware datetimes


async def seed():
    _assert_local_db()
    # Ensure tables are empty before seeding to prevent crash loops
    async with AsyncSessionLocal() as session:
        await session.execute(text('DELETE FROM "StudentInteractions"'))
        await session.execute(text('DELETE FROM "StudentSchedule"'))
        await session.execute(text('DELETE FROM "MessageTemplates"'))
        await session.execute(text('DELETE FROM "StudentEnrollment"'))
        await session.execute(text('DELETE FROM "Classes"'))
        await session.execute(text('DELETE FROM "Students"'))
        await session.execute(text('DELETE FROM "Courses"'))
        await session.execute(text('DELETE FROM "Teachers"'))
        await session.commit()
    
    # Professor admin de bootstrap — fonte única em src/db/database.py.
    # Idempotente; é o mesmo admin (id 000…001, fone 5511000000000) garantido em prod.
    await ensure_bootstrap_admin()
    teacher_id = BOOTSTRAP_ADMIN_ID

    async with AsyncSessionLocal() as session:
        # Create a fake course (Deterministic UUID for local dev)
        course_id = "00000000-0000-0000-0000-000000000002"
        await session.execute(
            text("""
                INSERT INTO "Courses" (id, teacher_id, name, is_active)
                VALUES (:id, :teacher_id, 'Matemática Básica', true)
            """),
            {"id": course_id, "teacher_id": teacher_id}
        )
        
        # Create a real student for testing (Deterministic UUID)
        student_id = "00000000-0000-0000-0000-000000000003"
        phone = "5513991719595"
        await session.execute(
            text("""
                INSERT INTO "Students" (id, name, phone, is_active)
                VALUES (:id, 'Ygor (Aluno)', :phone, true)
            """),
            {"id": student_id, "phone": phone}
        )
        
        # Create a fake class
        class_id = "00000000-0000-0000-0000-000000000004"
        await session.execute(
            text("""
                INSERT INTO "Classes" (id, course_id, name, current_lesson_number)
                VALUES (:id, :course_id, 'Turma Teste', 1)
            """),
            {"id": class_id, "course_id": course_id}
        )
        
        # Enroll the student
        enrollment_id = str(uuid.uuid4())
        await session.execute(
            text("""
                INSERT INTO "StudentEnrollment" (enrollment_id, student_id, class_id)
                VALUES (:id, :student_id, :class_id)
            """),
            {"id": enrollment_id, "student_id": student_id, "class_id": class_id}
        )

        # ── Message Template for daily review ──
        await session.execute(
            text("""
                INSERT INTO "MessageTemplates" (template_name, language, category, status)
                VALUES ('daily_review', 'pt_BR', 'UTILITY', 'approved')
            """)
        )

        # ── Student Schedules — 3 time slots ──
        for slot in REVIEW_TIME_SLOTS:
            schedule_id = str(uuid.uuid4())
            next_trigger = compute_next_trigger_utc(slot)
            await session.execute(
                text("""
                    INSERT INTO "StudentSchedule"
                        (schedule_id, student_id, course_id, template_name,
                         preferred_hour, content_type, next_trigger_date,
                         is_active, status)
                    VALUES
                        (:sid, :student_id, :course_id, 'daily_review',
                         :preferred_hour, 'review', :next_trigger,
                         true, 'scheduled')
                """),
                {
                    "sid": schedule_id,
                    "student_id": student_id,
                    "course_id": course_id,
                    "preferred_hour": slot,
                    "next_trigger": next_trigger,
                }
            )
        
        await session.commit()
        print(f"Database seeded successfully!")
        print(f"Student Phone for Testing: {phone}")
        print(f"Teacher ID: {teacher_id}")
        print(f"Course ID: {course_id}")
        print(f"Review Schedule Time Slots: {', '.join(REVIEW_TIME_SLOTS)} BRT")
        for slot in REVIEW_TIME_SLOTS:
            trigger = compute_next_trigger_utc(slot)
            print(f"  {slot} BRT → Next trigger: {trigger.isoformat()} UTC")

if __name__ == "__main__":
    asyncio.run(seed())
