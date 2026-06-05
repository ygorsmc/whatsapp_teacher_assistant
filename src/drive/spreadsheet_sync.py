"""
Spreadsheet Sync Module - Synchronizes Google Sheets rows into the Azure SQL Database.
Uses MERGE statements for MSSQL compatibility.
"""

import structlog
import re
import json
from datetime import datetime, timezone, timedelta
from sqlalchemy import text
from src.db.database import AsyncSessionLocal
from src.drive.client import drive_client
from src.shared.config import settings

logger = structlog.get_logger(__name__)

def clean_phone(phone: str) -> str:
    """Removes non-numeric characters from the phone number."""
    digits = re.sub(r"\D", "", str(phone))
    if digits.startswith("0"):
        digits = digits[1:]
    if len(digits) >= 10 and not digits.startswith("55"):
        digits = "55" + digits
    return digits

def generate_id(prefix: str, seed: str) -> str:
    """Generates a deterministic ID based on a string seed."""
    import hashlib
    h = hashlib.md5(seed.encode()).hexdigest()[:8]
    return f"{prefix}_{h}"


def _is_mssql(url: str) -> bool:
    """Check if the database URL points to MSSQL."""
    return "mssql" in url.lower() or "aioodbc" in url.lower()


async def sync_users_from_sheet(sheet_id: str | None = None) -> dict:
    """
    Reads the Google Sheet and synchronizes Students, Teachers, Courses, and Schedules.
    Returns a summary of operations.
    """
    sheet_id = sheet_id or settings.GOOGLE_SHEET_ID
    if not sheet_id:
        logger.error("No GOOGLE_SHEET_ID configured.")
        return {"error": "Missing GOOGLE_SHEET_ID"}
        
    try:
        # Read from the first sheet instead of forcing a specific sheet name
        rows = await drive_client.get_spreadsheet_data(sheet_id, "A2:H1000")
    except Exception as e:
        logger.error("Failed to read Google Sheet", error=str(e))
        return {"error": f"Drive API Error: {str(e)}"}

    if not rows:
        return {"error": "No data found or sheet 'Alunos' doesn't exist."}

    use_mssql = _is_mssql(settings.DATABASE_URL)
    stats = {"teachers_upserted": 0, "students_upserted": 0, "courses_upserted": 0, "schedules_upserted": 0}
    default_teacher_id = None

    async with AsyncSessionLocal() as session:
        # Snapshot before
        teachers_before = (await session.execute(text('SELECT count(*) FROM \"Teachers\"'))).scalar()
        students_before = (await session.execute(text('SELECT count(*) FROM \"Students\"'))).scalar()
        courses_before = (await session.execute(text('SELECT count(*) FROM \"Courses\"'))).scalar()
        schedules_before = (await session.execute(text('SELECT count(*) FROM \"StudentSchedule\"'))).scalar()

        # Pass 1: Upsert Teachers first (so Courses have an owner)
        for row in rows:
            if len(row) < 3:
                continue
                
            name = row[0].strip()
            phone = clean_phone(row[1])
            role = str(row[2]).strip().lower()
            status_val = row[7].strip().lower() if len(row) > 7 else "ativo"
            is_active = True if status_val in ("ativo", "active", "1", "true") else False

            if role == "professor":
                teacher_id = generate_id("tea", phone)
                if not default_teacher_id:
                    default_teacher_id = teacher_id

                if use_mssql:
                    await session.execute(
                        text("""
                            MERGE INTO "Teachers" AS target
                            USING (SELECT :phone AS phone) AS source
                            ON target.phone = source.phone
                            WHEN MATCHED THEN UPDATE SET
                                name = :name,
                                is_active = :is_active,
                                updated_at = GETUTCDATE()
                            WHEN NOT MATCHED THEN INSERT (id, name, subject, phone, assistant_persona, out_of_scope_message, is_active)
                                VALUES (:id, :name, 'Diversos', :phone, 'Seja amigável e direto.', 'Fora do escopo.', :is_active);
                        """),
                        {"id": teacher_id, "name": name, "phone": phone, "is_active": is_active}
                    )
                    res = await session.execute(text('SELECT id FROM \"Teachers\" WHERE phone = :phone'), {"phone": phone})
                    teacher_id = res.scalar()
                    if default_teacher_id == generate_id('tea', phone):
                        default_teacher_id = teacher_id
                else:
                    res = await session.execute(
                        text("""
                            INSERT INTO "Teachers" (id, name, subject, phone, assistant_persona, out_of_scope_message, is_active)
                            VALUES (:id, :name, 'Diversos', :phone, 'Seja amigável e direto.', 'Fora do escopo.', :is_active)
                            ON CONFLICT(phone) DO UPDATE SET 
                                name=excluded.name, 
                                is_active=excluded.is_active,
                                updated_at=CURRENT_TIMESTAMP
                            RETURNING id
                        """),
                        {"id": teacher_id, "name": name, "phone": phone, "is_active": is_active}
                    )
                    teacher_id = res.scalar()
                    if default_teacher_id == generate_id('tea', phone):
                        default_teacher_id = teacher_id
                stats["teachers_upserted"] += 1
                
        # Fallback if no teacher defined in sheet (needed for course foreign key)
        if not default_teacher_id:
            default_teacher_id = "tea_default"
            if use_mssql:
                await session.execute(
                    text("""
                        MERGE INTO "Teachers" AS target
                        USING (SELECT :id AS id) AS source
                        ON target.id = source.id
                        WHEN NOT MATCHED THEN INSERT (id, name, subject, phone, assistant_persona, out_of_scope_message, is_active)
                            VALUES (:id, 'Professor Default', 'Geral', '00000000', 'Seja prestativo.', 'Não sei.', true);
                    """), {"id": default_teacher_id}
                )
            else:
                await session.execute(
                    text("""
                        INSERT INTO "Teachers" (id, name, subject, phone, assistant_persona, out_of_scope_message, is_active)
                        VALUES (:id, 'Professor Default', 'Geral', '00000000', 'Seja prestativo.', 'Não sei.', true)
                        ON CONFLICT DO NOTHING
                    """), {"id": default_teacher_id}
                )

        # Pass 2: Upsert Students, Courses, Enrollments, and Schedules
        from collections import defaultdict
        student_active_courses = defaultdict(set)
        student_active_schedules = defaultdict(set)
        
        for row in rows:
            if len(row) < 3:
                continue
                
            name = row[0].strip()
            phone = clean_phone(row[1])
            role = str(row[2]).strip().lower()
            course_name = row[3].strip() if len(row) > 3 else ""
            class_name = row[4].strip() if len(row) > 4 else "Turma Única"
            
            # Horários: Colunas F(5), G(6), H(7)
            hours = []
            for idx in [5, 6, 7]:
                if len(row) > idx and row[idx].strip():
                    hours.append(row[idx].strip())
            
            # Status: Coluna I(8)
            status_val = row[8].strip().lower() if len(row) > 8 else "ativo"
            is_active = True if status_val in ("ativo", "active", "1", "true") else False

            if role == "aluno" and phone:
                student_id = generate_id("stu", phone)
                
                # 1. Upsert Student
                if use_mssql:
                    await session.execute(
                        text("""
                            MERGE INTO "Students" AS target
                            USING (SELECT :phone AS phone) AS source
                            ON target.phone = source.phone
                            WHEN MATCHED THEN UPDATE SET
                                name = :name,
                                is_active = :is_active,
                                updated_at = GETUTCDATE()
                            WHEN NOT MATCHED THEN INSERT (id, name, phone, is_active)
                                VALUES (:id, :name, :phone, :is_active);
                        """),
                        {"id": student_id, "name": name, "phone": phone, "is_active": is_active}
                    )
                    res = await session.execute(text('SELECT id FROM \"Students\" WHERE phone = :phone'), {"phone": phone})
                    student_id = res.scalar()
                else:
                    res = await session.execute(
                        text("""
                            INSERT INTO "Students" (id, name, phone, is_active)
                            VALUES (:id, :name, :phone, :is_active)
                            ON CONFLICT(phone) DO UPDATE SET 
                                name=excluded.name, 
                                is_active=excluded.is_active,
                                updated_at=CURRENT_TIMESTAMP
                            RETURNING id
                        """),
                        {"id": student_id, "name": name, "phone": phone, "is_active": is_active}
                    )
                    student_id = res.scalar()
                stats["students_upserted"] += 1
                
                if not course_name:
                    continue # Student has no course assigned
                    
                # 2. Upsert Course
                course_id = generate_id("crs", course_name)
                if use_mssql:
                    await session.execute(
                        text("""
                            MERGE INTO "Courses" AS target
                            USING (SELECT :id AS id) AS source
                            ON target.id = source.id
                            WHEN NOT MATCHED THEN INSERT (id, teacher_id, name, is_active)
                                VALUES (:id, :tid, :name, true);
                        """),
                        {"id": course_id, "tid": default_teacher_id, "name": course_name}
                    )
                else:
                    await session.execute(
                        text("""
                            INSERT INTO "Courses" (id, teacher_id, name, is_active)
                            VALUES (:id, :tid, :name, true)
                            ON CONFLICT DO NOTHING
                        """),
                        {"id": course_id, "tid": default_teacher_id, "name": course_name}
                    )
                stats["courses_upserted"] += 1
                
                # 2.5 Upsert Class
                class_id = generate_id("cls", f"{course_id}_{class_name}")
                if use_mssql:
                    await session.execute(
                        text("""
                            MERGE INTO "Classes" AS target
                            USING (SELECT :id AS id) AS source
                            ON target.id = source.id
                            WHEN NOT MATCHED THEN INSERT (id, course_id, name, current_lesson_number)
                                VALUES (:id, :cid, :name, 1);
                        """),
                        {"id": class_id, "cid": course_id, "name": class_name}
                    )
                else:
                    await session.execute(
                        text("""
                            INSERT INTO "Classes" (id, course_id, name, current_lesson_number)
                            VALUES (:id, :cid, :name, 1)
                            ON CONFLICT DO NOTHING
                        """),
                        {"id": class_id, "cid": course_id, "name": class_name}
                    )
                
                # 3. Upsert Enrollment
                enroll_id = generate_id("enr", f"{student_id}_{class_id}")
                if use_mssql:
                    await session.execute(
                        text("""
                            MERGE INTO "StudentEnrollment" AS target
                            USING (SELECT :sid AS student_id, :cid AS class_id) AS source
                            ON target.student_id = source.student_id AND target.class_id = source.class_id
                            WHEN NOT MATCHED THEN INSERT (enrollment_id, student_id, class_id)
                                VALUES (:id, :sid, :cid);
                        """),
                        {"id": enroll_id, "sid": student_id, "cid": class_id}
                    )
                else:
                    await session.execute(
                        text("""
                            INSERT INTO "StudentEnrollment" (enrollment_id, student_id, class_id)
                            VALUES (:id, :sid, :cid)
                            ON CONFLICT(student_id, class_id) DO NOTHING
                        """),
                        {"id": enroll_id, "sid": student_id, "cid": class_id}
                    )
                student_active_courses[student_id].add(class_id)
                
                # 4. Upsert Schedules
                from src.workers.scheduler_worker import compute_next_trigger
                for h in hours:
                    if not h or ":" not in h:
                        continue # Empty or invalid hour format
                        
                    try:
                        # Extract "HH:MM" 
                        h_clean = h[:5]
                        next_trigger = compute_next_trigger(h_clean)
                        
                        sched_id = generate_id("sch", f"{student_id}_{course_id}_{h_clean}")
                        if use_mssql:
                            await session.execute(
                                text("""
                                    MERGE INTO "StudentSchedule" AS target
                                    USING (SELECT :sid AS student_id, :cid AS course_id, :ct AS content_type, :hr AS preferred_hour) AS source
                                    ON target.student_id = source.student_id 
                                       AND target.course_id = source.course_id 
                                       AND target.content_type = source.content_type 
                                       AND target.preferred_hour = source.preferred_hour
                                    WHEN MATCHED THEN UPDATE SET
                                        is_active = :is_active,
                                        status = 'scheduled',
                                        updated_at = GETUTCDATE()
                                    WHEN NOT MATCHED THEN INSERT 
                                        (schedule_id, student_id, course_id, template_name, preferred_hour, content_type, next_trigger_date, is_active, status)
                                        VALUES (:id, :sid, :cid, 'daily_review', :hr, :ct, :nt, :is_active, 'scheduled');
                                """),
                                {"id": sched_id, "sid": student_id, "cid": course_id, "hr": h_clean, "ct": "review", "nt": next_trigger, "is_active": is_active}
                            )
                        else:
                            await session.execute(
                                text("""
                                    INSERT INTO "StudentSchedule" 
                                    (schedule_id, student_id, course_id, template_name, preferred_hour, content_type, next_trigger_date, is_active, status)
                                    VALUES (:id, :sid, :cid, 'daily_review', :hr, 'review', :nt, :is_active, 'scheduled')
                                    ON CONFLICT(student_id, course_id, content_type, preferred_hour) DO UPDATE SET
                                        is_active=excluded.is_active,
                                        status='scheduled',
                                        updated_at=CURRENT_TIMESTAMP
                                """),
                                {"id": sched_id, "sid": student_id, "cid": course_id, "hr": h_clean, "nt": next_trigger, "is_active": is_active}
                            )
                        stats["schedules_upserted"] += 1
                        student_active_schedules[student_id].add(sched_id)
                    except Exception as e:
                        logger.warning(f"Invalid hour format '{h}' for student {name}: {e}")
                        
        # Pass 1.5: Sync Calendar (Turmas Tab)
        try:
            turmas_rows = await drive_client.get_spreadsheet_data(sheet_id, "Turmas!A2:J1000")
            for row in turmas_rows:
                if len(row) < 2:
                    continue
                
                class_name = row[0].strip()
                course_name = row[1].strip()
                start_date_str = row[2].strip() if len(row) > 2 else ""
                
                start_date_obj = None
                if start_date_str:
                    try:
                        start_date_obj = datetime.strptime(start_date_str, "%d/%m/%Y").date()
                    except ValueError:
                        logger.warning(f"Invalid date format for class {class_name}: {start_date_str}")
                        
                schedule_map = {}
                for i in range(7):
                    idx = 3 + i
                    val = row[idx].strip() if len(row) > idx else ""
                    if val and val.lower() != "sem aula":
                        schedule_map[str(i)] = val
                        
                schedule_map_json = json.dumps(schedule_map) if schedule_map else None
                
                course_id = generate_id("crs", course_name)

                if use_mssql:
                    await session.execute(
                        text("""
                            MERGE INTO "Courses" AS target
                            USING (SELECT :id AS id) AS source
                            ON target.id = source.id
                            WHEN NOT MATCHED THEN INSERT (id, teacher_id, name, is_active)
                                VALUES (:id, :tid, :name, true);
                        """),
                        {"id": course_id, "tid": default_teacher_id, "name": course_name}
                    )
                else:
                    await session.execute(
                        text("""
                            INSERT INTO "Courses" (id, teacher_id, name, is_active)
                            VALUES (:id, :tid, :name, true)
                            ON CONFLICT DO NOTHING
                        """),
                        {"id": course_id, "tid": default_teacher_id, "name": course_name}
                    )

                class_id = generate_id("cls", f"{course_id}_{class_name}")
                
                current_lesson = 1
                if start_date_obj:
                    local_tz = timezone(timedelta(hours=settings.TIMEZONE_OFFSET_HOURS))
                    hoje = datetime.now(local_tz).date()
                    if start_date_obj > hoje:
                        current_lesson = 0

                if use_mssql:
                    await session.execute(
                        text("""
                            MERGE INTO "Classes" AS target
                            USING (SELECT :id AS id) AS source
                            ON target.id = source.id
                            WHEN MATCHED THEN UPDATE SET
                                start_date = :start_date,
                                schedule_map = :sched_map,
                                current_lesson_number = CASE WHEN :start_date > CAST(GETUTCDATE() AS DATE) THEN 0 ELSE target.current_lesson_number END,
                                updated_at = GETUTCDATE()
                            WHEN NOT MATCHED THEN INSERT (id, course_id, name, current_lesson_number, start_date, schedule_map)
                                VALUES (:id, :cid, :name, :lesson, :start_date, :sched_map);
                        """),
                        {
                            "id": class_id, 
                            "cid": course_id, 
                            "name": class_name, 
                            "lesson": current_lesson,
                            "start_date": start_date_obj if start_date_obj else None,
                            "sched_map": schedule_map_json
                        }
                    )
                else:
                    await session.execute(
                        text("""
                            INSERT INTO "Classes" (id, course_id, name, current_lesson_number, start_date, schedule_map)
                            VALUES (:id, :cid, :name, :lesson, :start_date, :sched_map)
                            ON CONFLICT(id) DO UPDATE SET 
                                start_date=excluded.start_date,
                                schedule_map=excluded.schedule_map,
                                current_lesson_number=CASE WHEN excluded.start_date > CURRENT_DATE THEN 0 ELSE "Classes".current_lesson_number END,
                                updated_at=CURRENT_TIMESTAMP
                        """),
                        {
                            "id": class_id, 
                            "cid": course_id, 
                            "name": class_name, 
                            "lesson": current_lesson,
                            "start_date": start_date_obj if start_date_obj else None,
                            "sched_map": schedule_map_json
                        }
                    )
        except Exception as e:
            logger.warning("Failed to read Turmas sheet, maybe it doesn't exist yet", error=str(e))

        # Pass 3: Cleanup orphaned enrollments and schedules for the synced students
        for sid, class_ids in student_active_courses.items():
            if class_ids:
                placeholders = ','.join(f"'{cid}'" for cid in class_ids)
                await session.execute(text(f"DELETE FROM \"StudentEnrollment\" WHERE student_id = '{sid}' AND class_id NOT IN ({placeholders})"))
            else:
                await session.execute(text(f"DELETE FROM \"StudentEnrollment\" WHERE student_id = '{sid}'"))
                
        for sid, sched_ids in student_active_schedules.items():
            if sched_ids:
                placeholders = ','.join(f"'{sch}'" for sch in sched_ids)
                await session.execute(text(f"DELETE FROM \"StudentSchedule\" WHERE student_id = '{sid}' AND schedule_id NOT IN ({placeholders})"))
            else:
                await session.execute(text(f"DELETE FROM \"StudentSchedule\" WHERE student_id = '{sid}'"))

        await session.commit()
        
        # Snapshot after
        stats["teachers_before"] = teachers_before
        stats["teachers_after"] = (await session.execute(text('SELECT count(*) FROM \"Teachers\"'))).scalar()
        stats["students_before"] = students_before
        stats["students_after"] = (await session.execute(text('SELECT count(*) FROM \"Students\"'))).scalar()
        stats["courses_before"] = courses_before
        stats["courses_after"] = (await session.execute(text('SELECT count(*) FROM \"Courses\"'))).scalar()
        stats["schedules_before"] = schedules_before
        stats["schedules_after"] = (await session.execute(text('SELECT count(*) FROM \"StudentSchedule\"'))).scalar()

        logger.info("Spreadsheet sync completed", stats=stats)
        return stats
