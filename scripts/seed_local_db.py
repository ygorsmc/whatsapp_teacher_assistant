import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from sqlalchemy import text
from src.db.database import init_db, AsyncSessionLocal
import src.db.models  # Ensure models are registered with Base

# BRT offset (UTC-3)
BRT_OFFSET = timedelta(hours=-3)

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
    # Ensure tables are created
    await init_db()
    
    async with AsyncSessionLocal() as session:
        # Create a fake teacher (Deterministic UUID for local dev)
        teacher_id = "00000000-0000-0000-0000-000000000001"
        await session.execute(
            text("""
                INSERT OR IGNORE INTO Teachers (id, name, subject, phone, assistant_persona, out_of_scope_message, is_active)
                VALUES (:id, 'Professor Teste', 'Matemática', '5511000000000', 'Seja educado e prestativo', 'Isso está fora do escopo.', 1)
            """),
            {"id": teacher_id}
        )
        
        # Create a fake course (Deterministic UUID for local dev)
        course_id = "00000000-0000-0000-0000-000000000002"
        await session.execute(
            text("""
                INSERT OR IGNORE INTO Courses (id, teacher_id, name, is_active)
                VALUES (:id, :teacher_id, 'Matemática Básica', 1)
            """),
            {"id": course_id, "teacher_id": teacher_id}
        )
        
        # Create a real student for testing (Deterministic UUID)
        student_id = "00000000-0000-0000-0000-000000000003"
        phone = "5513991719595"
        await session.execute(
            text("""
                INSERT OR IGNORE INTO Students (id, name, phone, is_active)
                VALUES (:id, 'Ygor (Aluno)', :phone, 1)
            """),
            {"id": student_id, "phone": phone}
        )
        
        # Create a fake class
        class_id = "00000000-0000-0000-0000-000000000004"
        await session.execute(
            text("""
                INSERT OR IGNORE INTO Classes (id, course_id, name, current_lesson_number)
                VALUES (:id, :course_id, 'Turma Teste', 1)
            """),
            {"id": class_id, "course_id": course_id}
        )
        
        # Enroll the student
        enrollment_id = str(uuid.uuid4())
        await session.execute(
            text("""
                INSERT OR IGNORE INTO StudentEnrollment (enrollment_id, student_id, class_id)
                VALUES (:id, :student_id, :class_id)
            """),
            {"id": enrollment_id, "student_id": student_id, "class_id": class_id}
        )

        # ── Message Template for daily review ──
        await session.execute(
            text("""
                INSERT OR IGNORE INTO MessageTemplates (template_name, language, category, status)
                VALUES ('daily_review', 'pt_BR', 'UTILITY', 'approved')
            """)
        )

        # ── Student Schedules — 3 time slots ──
        for slot in REVIEW_TIME_SLOTS:
            schedule_id = str(uuid.uuid4())
            next_trigger = compute_next_trigger_utc(slot)
            await session.execute(
                text("""
                    INSERT OR IGNORE INTO StudentSchedule
                        (schedule_id, student_id, course_id, template_name,
                         preferred_hour, content_type, next_trigger_date,
                         is_active, status)
                    VALUES
                        (:sid, :student_id, :course_id, 'daily_review',
                         :preferred_hour, 'review', :next_trigger,
                         1, 'scheduled')
                """),
                {
                    "sid": schedule_id,
                    "student_id": student_id,
                    "course_id": course_id,
                    "preferred_hour": slot,
                    "next_trigger": next_trigger.isoformat(),
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
