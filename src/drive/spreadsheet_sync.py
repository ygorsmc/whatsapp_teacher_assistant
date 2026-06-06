"""
Spreadsheet Sync Module — Sincroniza o Google Sheet com o banco.

Layout (abas separadas):
  - "Professores": Nome | Telefone | Curso (Pasta) | Turma
        → tabela Teachers + mapa de posse por turma
  - "Alunos":      Nome | Telefone | Papel | Curso | Turma | H1 | H2 | H3 | Status
        → Students / Courses / Classes / StudentEnrollment / StudentSchedule
  - "Turmas":      Turma | Curso | Data Início | Seg..Dom
        → calendário das Classes (start_date + schedule_map)
  - "Lista_Pastas": nomes das pastas/cursos (não consumida aqui)

Posse por turma: cada linha de "Professores" define o dono de uma (Curso, Turma)
→ Classes.teacher_id. Courses.teacher_id recebe o primeiro professor visto para o
curso (FK + fallback para o contexto do aluno). Turmas sem professor explícito
caem no Admin de bootstrap e são reportadas em stats["classes_sem_professor"].

Compatível com PostgreSQL (produção) e SQLite (dev local) — ambos via ON CONFLICT.
"""

import structlog
import re
import json
from typing import cast
from collections import defaultdict
from datetime import datetime, timezone, timedelta, date
from sqlalchemy import text
from src.db.database import AsyncSessionLocal, BOOTSTRAP_ADMIN_ID
from src.drive.client import drive_client
from src.shared.config import settings

logger = structlog.get_logger(__name__)

ACTIVE_VALUES = ("ativo", "active", "1", "true")
_DATE_FORMATS = ("%d/%m/%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S")


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


def _cell(row, idx: int, default: str = "") -> str:
    """Lê a célula `idx` como string limpa, tolerando linhas curtas/None."""
    if idx < len(row) and row[idx] is not None:
        return str(row[idx]).strip()
    return default


def _parse_date(value: str) -> date | None:
    """Tenta parsear a data em formatos comuns (BR e ISO)."""
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


async def sync_users_from_sheet(sheet_id: str | None = None) -> dict:
    """
    Lê o Google Sheet (abas Professores/Alunos/Turmas) e sincroniza o banco.
    Retorna um resumo das operações.
    """
    sheet_id = sheet_id or settings.GOOGLE_SHEET_ID
    if not sheet_id:
        logger.error("No GOOGLE_SHEET_ID configured.")
        return {"error": "Missing GOOGLE_SHEET_ID"}

    # --- Leitura das abas ---
    try:
        prof_rows = await drive_client.get_spreadsheet_data(sheet_id, "Professores!A2:D1000")
    except Exception as e:
        prof_rows = []
        logger.warning("Aba 'Professores' não encontrada ou ilegível", error=str(e)[:200])

    try:
        alunos_rows = await drive_client.get_spreadsheet_data(sheet_id, "Alunos!A2:I1000")
    except Exception as e:
        logger.error("Falha ao ler a aba 'Alunos'", error=str(e))
        return {"error": f"Drive API Error (Alunos): {str(e)}"}

    if not alunos_rows and not prof_rows:
        return {"error": "Nenhum dado encontrado nas abas 'Alunos'/'Professores'."}

    # Importa lazy para evitar import circular (scheduler_worker importa esta sync).
    from src.workers.scheduler_worker import compute_next_trigger

    stats = {"teachers_upserted": 0, "students_upserted": 0, "courses_upserted": 0, "schedules_upserted": 0}
    course_owner: dict[str, str] = {}                 # course_name -> teacher_id (dono do curso)
    class_owner: dict[tuple[str, str], str] = {}      # (course_name, class_name) -> teacher_id
    classes_sem_professor: set[str] = set()
    upserted_teacher_ids: set[str] = set()

    def resolve_class_teacher(course_name: str, class_name: str) -> str:
        """Dono da turma: específico (Professores) > dono do curso > Admin."""
        tid = class_owner.get((course_name, class_name))
        if tid:
            return tid
        tid = course_owner.get(course_name)
        if tid:
            return tid
        return BOOTSTRAP_ADMIN_ID

    async with AsyncSessionLocal() as session:
        # Snapshot antes
        teachers_before = (await session.execute(text('SELECT count(*) FROM "Teachers"'))).scalar()
        students_before = (await session.execute(text('SELECT count(*) FROM "Students"'))).scalar()
        courses_before = (await session.execute(text('SELECT count(*) FROM "Courses"'))).scalar()
        schedules_before = (await session.execute(text('SELECT count(*) FROM "StudentSchedule"'))).scalar()

        # ===== Pass 0: Professores → Teachers + mapas de posse =====
        for row in prof_rows:
            name = _cell(row, 0)
            phone = clean_phone(_cell(row, 1))
            course_name = _cell(row, 2)
            class_name = _cell(row, 3)
            if not name or not phone:
                continue

            teacher_id = generate_id("tea", phone)
            res = await session.execute(
                text("""
                    INSERT INTO "Teachers" (id, name, subject, phone, assistant_persona, out_of_scope_message, is_active)
                    VALUES (:id, :name, 'Diversos', :phone, 'Seja amigável e direto.', 'Fora do escopo.', true)
                    ON CONFLICT(phone) DO UPDATE SET
                        name = excluded.name,
                        is_active = true,
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING id
                """),
                {"id": teacher_id, "name": name, "phone": phone},
            )
            teacher_id = cast(str, res.scalar())
            upserted_teacher_ids.add(teacher_id)

            if course_name:
                course_owner.setdefault(course_name, teacher_id)
                if class_name:
                    class_owner[(course_name, class_name)] = teacher_id

        stats["teachers_upserted"] = len(upserted_teacher_ids)

        # ===== Pass 1: Alunos → Students/Courses/Classes/Enrollments/Schedules =====
        student_active_classes = defaultdict(set)
        student_active_schedules = defaultdict(set)

        for row in alunos_rows:
            name = _cell(row, 0)
            phone = clean_phone(_cell(row, 1))
            role = _cell(row, 2).lower()
            course_name = _cell(row, 3)
            class_name = _cell(row, 4) or "Turma Única"
            hours = [_cell(row, i) for i in (5, 6, 7) if _cell(row, i)]
            status_val = (_cell(row, 8) or "ativo").lower()
            is_active = status_val in ACTIVE_VALUES

            # A aba "Alunos" deve conter só alunos; ignora qualquer linha marcada como professor.
            if role and role != "aluno":
                continue
            if not phone:
                continue

            # 1. Upsert Student
            student_id = generate_id("stu", phone)
            res = await session.execute(
                text("""
                    INSERT INTO "Students" (id, name, phone, is_active)
                    VALUES (:id, :name, :phone, :is_active)
                    ON CONFLICT(phone) DO UPDATE SET
                        name = excluded.name,
                        is_active = excluded.is_active,
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING id
                """),
                {"id": student_id, "name": name, "phone": phone, "is_active": is_active},
            )
            student_id = cast(str, res.scalar())
            stats["students_upserted"] += 1

            if not course_name:
                continue  # aluno sem curso atribuído

            # 2. Upsert Course (dono = primeiro professor do curso, ou Admin)
            course_id = generate_id("crs", course_name)
            owner_tid = course_owner.get(course_name, BOOTSTRAP_ADMIN_ID)
            await session.execute(
                text("""
                    INSERT INTO "Courses" (id, teacher_id, name, is_active)
                    VALUES (:id, :tid, :name, true)
                    ON CONFLICT(id) DO UPDATE SET
                        teacher_id = excluded.teacher_id,
                        name = excluded.name,
                        is_active = true,
                        updated_at = CURRENT_TIMESTAMP
                """),
                {"id": course_id, "tid": owner_tid, "name": course_name},
            )
            stats["courses_upserted"] += 1

            # 2.5 Upsert Class (dono por turma)
            class_tid = resolve_class_teacher(course_name, class_name)
            if (course_name, class_name) not in class_owner:
                classes_sem_professor.add(f"{course_name} / {class_name}")
            class_id = generate_id("cls", f"{course_id}_{class_name}")
            await session.execute(
                text("""
                    INSERT INTO "Classes" (id, course_id, teacher_id, name, current_lesson_number)
                    VALUES (:id, :cid, :tid, :name, 1)
                    ON CONFLICT(id) DO UPDATE SET
                        teacher_id = excluded.teacher_id,
                        name = excluded.name,
                        updated_at = CURRENT_TIMESTAMP
                """),
                {"id": class_id, "cid": course_id, "tid": class_tid, "name": class_name},
            )

            # 3. Upsert Enrollment
            enroll_id = generate_id("enr", f"{student_id}_{class_id}")
            await session.execute(
                text("""
                    INSERT INTO "StudentEnrollment" (enrollment_id, student_id, class_id)
                    VALUES (:id, :sid, :cid)
                    ON CONFLICT(student_id, class_id) DO NOTHING
                """),
                {"id": enroll_id, "sid": student_id, "cid": class_id},
            )
            student_active_classes[student_id].add(class_id)

            # 4. Upsert Schedules (horários de revisão)
            for h in hours:
                if ":" not in h:
                    continue
                try:
                    h_clean = h[:5]
                    next_trigger = compute_next_trigger(h_clean)
                    sched_id = generate_id("sch", f"{student_id}_{course_id}_{h_clean}")
                    await session.execute(
                        text("""
                            INSERT INTO "StudentSchedule"
                                (schedule_id, student_id, course_id, template_name, preferred_hour, content_type, next_trigger_date, is_active, status)
                            VALUES (:id, :sid, :cid, 'daily_review', :hr, 'review', :nt, :is_active, 'scheduled')
                            ON CONFLICT(student_id, course_id, content_type, preferred_hour) DO UPDATE SET
                                is_active = excluded.is_active,
                                status = 'scheduled',
                                updated_at = CURRENT_TIMESTAMP
                        """),
                        {"id": sched_id, "sid": student_id, "cid": course_id, "hr": h_clean, "nt": next_trigger, "is_active": is_active},
                    )
                    stats["schedules_upserted"] += 1
                    student_active_schedules[student_id].add(sched_id)
                except Exception as e:
                    logger.warning(f"Horário inválido '{h}' para o aluno {name}: {e}")

        # ===== Pass 2: Turmas → calendário das Classes =====
        try:
            turmas_rows = await drive_client.get_spreadsheet_data(sheet_id, "Turmas!A2:J1000")
        except Exception as e:
            turmas_rows = []
            logger.warning("Aba 'Turmas' não encontrada", error=str(e)[:200])

        for row in turmas_rows:
            class_name = _cell(row, 0)
            course_name = _cell(row, 1)
            if not class_name or not course_name:
                continue

            start_date_str = _cell(row, 2)
            start_date_obj = _parse_date(start_date_str) if start_date_str else None
            if start_date_str and start_date_obj is None:
                logger.warning(f"Data inválida na turma {class_name}: {start_date_str}")

            schedule_map = {}
            for i in range(7):
                val = _cell(row, 3 + i)
                if val and val.lower() not in ("sem aula", "-"):
                    schedule_map[str(i)] = val
            schedule_map_json = json.dumps(schedule_map) if schedule_map else None

            course_id = generate_id("crs", course_name)
            owner_tid = course_owner.get(course_name, BOOTSTRAP_ADMIN_ID)
            await session.execute(
                text("""
                    INSERT INTO "Courses" (id, teacher_id, name, is_active)
                    VALUES (:id, :tid, :name, true)
                    ON CONFLICT(id) DO UPDATE SET
                        teacher_id = excluded.teacher_id,
                        name = excluded.name,
                        is_active = true,
                        updated_at = CURRENT_TIMESTAMP
                """),
                {"id": course_id, "tid": owner_tid, "name": course_name},
            )

            class_tid = resolve_class_teacher(course_name, class_name)
            if (course_name, class_name) not in class_owner:
                classes_sem_professor.add(f"{course_name} / {class_name}")

            class_id = generate_id("cls", f"{course_id}_{class_name}")
            current_lesson = 1
            if start_date_obj:
                local_tz = timezone(timedelta(hours=settings.TIMEZONE_OFFSET_HOURS))
                if start_date_obj > datetime.now(local_tz).date():
                    current_lesson = 0

            await session.execute(
                text("""
                    INSERT INTO "Classes" (id, course_id, teacher_id, name, current_lesson_number, start_date, schedule_map)
                    VALUES (:id, :cid, :tid, :name, :lesson, :start_date, :sched_map)
                    ON CONFLICT(id) DO UPDATE SET
                        teacher_id = excluded.teacher_id,
                        start_date = excluded.start_date,
                        schedule_map = excluded.schedule_map,
                        current_lesson_number = CASE WHEN excluded.start_date > CURRENT_DATE THEN 0 ELSE "Classes".current_lesson_number END,
                        updated_at = CURRENT_TIMESTAMP
                """),
                {
                    "id": class_id,
                    "cid": course_id,
                    "tid": class_tid,
                    "name": class_name,
                    "lesson": current_lesson,
                    "start_date": start_date_obj,
                    "sched_map": schedule_map_json,
                },
            )

        # ===== Pass 3: Limpeza de matrículas/agendamentos órfãos (alunos sincronizados) =====
        for sid, class_ids in student_active_classes.items():
            if class_ids:
                placeholders = ",".join(f"'{cid}'" for cid in class_ids)
                await session.execute(text(f"DELETE FROM \"StudentEnrollment\" WHERE student_id = '{sid}' AND class_id NOT IN ({placeholders})"))
            else:
                await session.execute(text(f"DELETE FROM \"StudentEnrollment\" WHERE student_id = '{sid}'"))

        for sid, sched_ids in student_active_schedules.items():
            if sched_ids:
                placeholders = ",".join(f"'{sch}'" for sch in sched_ids)
                await session.execute(text(f"DELETE FROM \"StudentSchedule\" WHERE student_id = '{sid}' AND schedule_id NOT IN ({placeholders})"))
            else:
                await session.execute(text(f"DELETE FROM \"StudentSchedule\" WHERE student_id = '{sid}'"))

        await session.commit()

        # Snapshot depois
        stats["teachers_before"] = teachers_before or 0
        stats["teachers_after"] = (await session.execute(text('SELECT count(*) FROM "Teachers"'))).scalar() or 0
        stats["students_before"] = students_before or 0
        stats["students_after"] = (await session.execute(text('SELECT count(*) FROM "Students"'))).scalar() or 0
        stats["courses_before"] = courses_before or 0
        stats["courses_after"] = (await session.execute(text('SELECT count(*) FROM "Courses"'))).scalar() or 0
        stats["schedules_before"] = schedules_before or 0
        stats["schedules_after"] = (await session.execute(text('SELECT count(*) FROM "StudentSchedule"'))).scalar() or 0

        # Turmas que não têm professor explícito na aba "Professores" (caíram no Admin).
        stats["classes_sem_professor"] = sorted(classes_sem_professor)

        # Alunos com agendamento ativo mas SEM endereço de entrega no canal atual.
        # No Telegram (query_column = telegram_chat_id) isso quer dizer que o aluno ainda
        # não vinculou a conta no bot — então o scheduler nunca consegue disparar o quiz.
        # No WhatsApp (query_column = phone) a coluna é sempre preenchida pela sync, então fica 0.
        from src.channel.factory import get_channel
        query_col = get_channel().get_user_query_column()
        unlinked_rows = (await session.execute(
            text(f"""
                SELECT DISTINCT s.name
                FROM "Students" s
                JOIN "StudentSchedule" ss ON ss.student_id = s.id AND ss.is_active = true
                WHERE s.is_active = true
                  AND (s.{query_col} IS NULL OR s.{query_col} = '')
                ORDER BY s.name
            """)
        )).fetchall()
        stats["unlinked_names"] = [r[0] for r in unlinked_rows]
        stats["students_unlinked"] = len(stats["unlinked_names"])

        logger.info("Spreadsheet sync completed", stats=stats)
        return stats
