"""
Spreadsheet Sync Module — Sincroniza o Google Sheet com o banco.

Layout (abas separadas) — camada Disciplina entre Curso e Turma:
  - "Professores": Nome | Telefone | Curso (Pasta) | Disciplina | Turma | Status
        → tabela Teachers + mapa de posse por turma
  - "Alunos":      Nome | Telefone | Curso | Disciplina | Turma | H1 | H2 | H3 | Status
        → Students / Courses / Subjects / Classes / StudentEnrollment / StudentSchedule
  - "Turmas":      Turma | Disciplina | Curso | Data Início | Seg..Dom
        → calendário das Classes (start_date + schedule_map) + disciplina AUTORITATIVA
  - "Lista_Pastas": nomes das pastas/cursos (não consumida aqui)

Posse por turma: cada linha de "Professores" define o dono de uma (Curso, Turma)
→ Classes.teacher_id. Courses.teacher_id recebe o primeiro professor visto para o
curso (FK + fallback para o contexto do aluno). Turmas sem professor explícito
caem no Admin de bootstrap e são reportadas em stats["classes_sem_professor"].

Disciplina (Subject): uma Turma pertence a UMA disciplina. A aba "Turmas" é a
fonte autoritativa (sobrescreve o que vier da aba "Alunos"). Divergências entre
as abas (mesma turma com disciplinas diferentes) são reportadas em
stats["disciplina_divergente"] — vale a disciplina da aba "Turmas".

PostgreSQL em dev e produção (mesmo banco em todo lugar) — upserts via ON CONFLICT.
"""

import structlog
import re
import json
from typing import cast
from collections import defaultdict
from datetime import datetime, timezone, timedelta, date
from sqlalchemy import text
from src.db.database import AsyncSessionLocal, BOOTSTRAP_ADMIN_ID
from src.drive.factory import get_registry_source
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
    # MD5 aqui é só para derivar um ID estável (não-criptográfico); o flag
    # `usedforsecurity=False` documenta a intenção e satisfaz o FIPS/bandit
    # sem alterar o hash gerado (IDs existentes permanecem idênticos).
    h = hashlib.md5(seed.encode(), usedforsecurity=False).hexdigest()[:8]
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
    registry = get_registry_source()
    # Guard do Google Sheets: só exige o ID quando a fonte é o Drive (default).
    # Fontes locais (CSV) resolvem o diretório por LOCAL_REGISTRY_DIR.
    if settings.REGISTRY_BACKEND.lower() == "gdrive" and not sheet_id:
        logger.error("No GOOGLE_SHEET_ID configured.")
        return {"error": "Missing GOOGLE_SHEET_ID"}

    # --- Leitura das abas (tabelas lógicas, agnóstico de fonte) ---
    try:
        prof_rows = await registry.read_table(sheet_id, "Professores")
    except Exception as e:
        prof_rows = []
        logger.warning("Aba 'Professores' não encontrada ou ilegível", error=str(e)[:200])

    try:
        # A:K cobre Nome..Status (A-I) + Responsável (J) + Menor (K) — as duas
        # últimas são LGPD P1.1 (Art. 14), opcionais. Sem elas, as células vêm
        # vazias e o aluno nasce sem responsável/não-criança (compat. total).
        alunos_rows = await registry.read_table(sheet_id, "Alunos")
    except Exception as e:
        logger.error("Falha ao ler a aba 'Alunos'", error=str(e))
        return {"error": f"Drive API Error (Alunos): {str(e)}"}

    if not alunos_rows and not prof_rows:
        return {"error": "Nenhum dado encontrado nas abas 'Alunos'/'Professores'."}

    # Importa lazy para evitar import circular (scheduler_worker importa esta sync).
    from src.workers.scheduler_worker import compute_next_trigger

    # Valores heterogêneos (ints de contagem + listas de avisos), daí dict[str, object].
    stats: dict[str, object] = {"teachers_upserted": 0, "students_upserted": 0, "courses_upserted": 0,
                                "subjects_upserted": 0, "schedules_upserted": 0}
    course_owner: dict[str, str] = {}                 # course_name -> teacher_id (dono do curso)
    class_owner: dict[tuple[str, str], str] = {}      # (course_name, class_name) -> teacher_id
    classes_sem_professor: set[str] = set()
    disciplina_divergente: set[str] = set()
    upserted_teacher_ids: set[str] = set()
    upserted_subject_ids: set[str] = set()
    upserted_course_ids: set[str] = set()
    upserted_class_ids: set[str] = set()
    # Disciplina por turma vinda de cada aba (p/ detectar divergência Alunos×Turmas).
    alunos_subject: dict[tuple[str, str], set[str]] = defaultdict(set)  # (curso,turma) -> {disciplinas vistas na aba Alunos}
    turmas_subject: dict[tuple[str, str], str] = {}                     # (curso,turma) -> disciplina (autoritativa)

    async def upsert_subject(session, course_id: str, subject_name: str) -> str:
        """Cria/atualiza a Disciplina e retorna o subject_id determinístico."""
        subject_id = generate_id("sub", f"{course_id}_{subject_name}")
        await session.execute(
            text("""
                INSERT INTO "Subjects" (id, course_id, name, is_active)
                VALUES (:id, :cid, :name, true)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    is_active = true,
                    updated_at = CURRENT_TIMESTAMP
            """),
            {"id": subject_id, "cid": course_id, "name": subject_name},
        )
        upserted_subject_ids.add(subject_id)
        return subject_id

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
        teachers_before = (await session.execute(text('SELECT count(*) FROM "Teachers" WHERE is_active = true'))).scalar()
        students_before = (await session.execute(text('SELECT count(*) FROM "Students" WHERE is_active = true'))).scalar()
        courses_before = (await session.execute(text('SELECT count(*) FROM "Courses" WHERE is_active = true'))).scalar()
        subjects_before = (await session.execute(text('SELECT count(*) FROM "Subjects"'))).scalar()
        schedules_before = (await session.execute(text('SELECT count(*) FROM "StudentSchedule"'))).scalar()

        # ===== Pass 0: Professores → Teachers + mapas de posse =====
        for row in prof_rows:
            name = _cell(row, 0)
            phone = clean_phone(_cell(row, 1))
            course_name = _cell(row, 2)
            # col 3 = Disciplina (não afeta a posse, que é por Curso+Turma); col 4 = Turma.
            class_name = _cell(row, 4)
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

        # ===== Pre-scan: is_active global por aluno (OR de todas as linhas) =====
        # Um aluno aparece em várias linhas (uma por turma). O is_active da
        # entidade Student deve ser true se QUALQUER matrícula estiver ativa;
        # o status por LINHA controla a matrícula/agendamento daquela turma.
        student_global_active: dict[str, bool] = {}
        for row in alunos_rows:
            phone = clean_phone(_cell(row, 1))
            if not phone:
                continue
            status_val = (_cell(row, 8) or "ativo").lower()
            row_active = status_val in ACTIVE_VALUES
            student_global_active[phone] = student_global_active.get(phone, False) or row_active

        # ===== Pass 1: Alunos → Students/Courses/Classes/Enrollments/Schedules =====
        student_active_classes = defaultdict(set)
        student_active_schedules = defaultdict(set)
        upserted_student_phones: set[str] = set()

        for row in alunos_rows:
            name = _cell(row, 0)
            phone = clean_phone(_cell(row, 1))
            # Layout novo (sem coluna "Papel"): Nome | Tel | Curso | Disciplina | Turma | H1 | H2 | H3 | Status
            course_name = _cell(row, 2)
            subject_name = _cell(row, 3)
            class_name = _cell(row, 4) or "Turma Única"
            hours = [_cell(row, i) for i in (5, 6, 7) if _cell(row, i)]
            status_val = (_cell(row, 8) or "ativo").lower()
            is_active = status_val in ACTIVE_VALUES
            # LGPD Art. 14 (P1.1): colunas OPCIONAIS no fim da aba (planilhas
            # antigas sem elas seguem valendo). "Responsável" = telefone do
            # responsável; "Menor" (sim/true/1) = criança (<12), exige opt-in.
            guardian_phone = clean_phone(_cell(row, 9)) or None
            menor_val = (_cell(row, 10) or "").strip().lower()
            is_child = menor_val in ("sim", "s", "true", "1", "menor")

            if not phone:
                continue

            # 1. Upsert Student — is_active global: true se QUALQUER linha
            # deste telefone for ativa (pré-computado acima). Evita que a
            # última linha processada sobrescreva indevidamente o status.
            student_id = generate_id("stu", phone)
            global_active = student_global_active.get(phone, is_active)
            if phone not in upserted_student_phones:
                res = await session.execute(
                    text("""
                        INSERT INTO "Students" (id, name, phone, is_active, guardian_phone, is_child)
                        VALUES (:id, :name, :phone, :is_active, :gphone, :is_child)
                        ON CONFLICT(phone) DO UPDATE SET
                            name = excluded.name,
                            is_active = excluded.is_active,
                            guardian_phone = excluded.guardian_phone,
                            is_child = excluded.is_child,
                            updated_at = CURRENT_TIMESTAMP
                        RETURNING id
                    """),
                    {"id": student_id, "name": name, "phone": phone, "is_active": global_active,
                     "gphone": guardian_phone, "is_child": is_child},
                )
                student_id = cast(str, res.scalar())
                upserted_student_phones.add(phone)
                stats["students_upserted"] = cast(int, stats["students_upserted"]) + 1
            else:
                # Já fez upsert para este phone — só resolve o student_id.
                res = await session.execute(
                    text('SELECT id FROM "Students" WHERE phone = :phone'),
                    {"phone": phone},
                )
                student_id = cast(str, res.scalar())

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
            upserted_course_ids.add(course_id)
            stats["courses_upserted"] = cast(int, stats["courses_upserted"]) + 1

            # 2.2 Upsert Subject (Disciplina). A aba Turmas é autoritativa e
            # sobrescreve depois; aqui é best-effort para turmas sem linha em Turmas.
            subject_id = None
            if subject_name:
                subject_id = await upsert_subject(session, course_id, subject_name)
                alunos_subject[(course_name, class_name)].add(subject_name)

            # 2.5 Upsert Class (dono por turma + disciplina)
            class_tid = resolve_class_teacher(course_name, class_name)
            if (course_name, class_name) not in class_owner:
                classes_sem_professor.add(f"{course_name} / {class_name}")
            class_id: str | None = generate_id("cls", f"{course_id}_{class_name}")
            await session.execute(
                text("""
                    INSERT INTO "Classes" (id, course_id, subject_id, teacher_id, name, current_lesson_number)
                    VALUES (:id, :cid, :sid, :tid, :name, 1)
                    ON CONFLICT(id) DO UPDATE SET
                        teacher_id = excluded.teacher_id,
                        subject_id = COALESCE(excluded.subject_id, "Classes".subject_id),
                        name = excluded.name,
                        updated_at = CURRENT_TIMESTAMP
                """),
                {"id": class_id, "cid": course_id, "sid": subject_id, "tid": class_tid, "name": class_name},
            )
            upserted_class_ids.add(class_id)

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
                    sched_id = generate_id("sch", f"{student_id}_{class_id}_{h_clean}")
                    await session.execute(
                        text("""
                            INSERT INTO "StudentSchedule"
                                (schedule_id, student_id, class_id, template_name, preferred_hour, content_type, next_trigger_date, is_active, status)
                            VALUES (:id, :sid, :cid, 'daily_review', :hr, 'review', :nt, :is_active, 'scheduled')
                            ON CONFLICT(student_id, class_id, content_type, preferred_hour) DO UPDATE SET
                                is_active = excluded.is_active,
                                status = 'scheduled',
                                updated_at = CURRENT_TIMESTAMP
                        """),
                        {"id": sched_id, "sid": student_id, "cid": class_id, "hr": h_clean, "nt": next_trigger, "is_active": is_active},
                    )
                    stats["schedules_upserted"] = cast(int, stats["schedules_upserted"]) + 1
                    student_active_schedules[student_id].add(sched_id)
                except Exception as e:
                    logger.warning(f"Horário inválido '{h}' para o aluno {name}: {e}")

        # ===== Pass 2: Turmas → calendário das Classes + Disciplina AUTORITATIVA =====
        try:
            turmas_rows = await registry.read_table(sheet_id, "Turmas")
        except Exception as e:
            turmas_rows = []
            logger.warning("Aba 'Turmas' não encontrada", error=str(e)[:200])

        from src.shared.language import normalize_level
        for row in turmas_rows:
            # Layout: Turma | Disciplina | Curso | Data Início | Seg..Dom | Idioma | Nível
            # (Vertical de Idiomas: Idioma/Nível são as 2 ÚLTIMAS colunas, opcionais —
            #  acrescentadas ao FINAL para não deslocar Data/dias do layout existente.)
            class_name = _cell(row, 0)
            subject_name = _cell(row, 1)
            course_name = _cell(row, 2)
            if not class_name or not course_name:
                continue

            start_date_str = _cell(row, 3)
            start_date_obj = _parse_date(start_date_str) if start_date_str else None
            if start_date_str and start_date_obj is None:
                logger.warning(f"Data inválida na turma {class_name}: {start_date_str}")

            schedule_map = {}
            for i in range(7):
                val = _cell(row, 4 + i)
                if val and val.lower() not in ("sem aula", "-"):
                    schedule_map[str(i)] = val
            schedule_map_json = json.dumps(schedule_map) if schedule_map else None

            # Vertical de Idiomas (opcional): Idioma=Sim → turma de idioma; Nível →
            # proficiência. Ausentes/'-' ⇒ is_language=false / nível NULL (intocado).
            is_language = (_cell(row, 11) or "").strip().lower() in ("sim", "yes", "true", "1")
            proficiency_level = normalize_level(_cell(row, 12))

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
            upserted_course_ids.add(course_id)

            # Disciplina autoritativa da turma (sobrescreve a vinda da aba Alunos).
            subject_id = None
            if subject_name:
                subject_id = await upsert_subject(session, course_id, subject_name)
                turmas_subject[(course_name, class_name)] = subject_name

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
                    INSERT INTO "Classes" (id, course_id, subject_id, teacher_id, name, current_lesson_number, start_date, schedule_map, is_language, proficiency_level)
                    VALUES (:id, :cid, :sid, :tid, :name, :lesson, :start_date, :sched_map, :is_lang, :level)
                    ON CONFLICT(id) DO UPDATE SET
                        teacher_id = excluded.teacher_id,
                        subject_id = COALESCE(excluded.subject_id, "Classes".subject_id),
                        start_date = excluded.start_date,
                        schedule_map = excluded.schedule_map,
                        is_language = excluded.is_language,
                        proficiency_level = excluded.proficiency_level,
                        current_lesson_number = CASE WHEN excluded.start_date > CURRENT_DATE THEN 0 ELSE "Classes".current_lesson_number END,
                        updated_at = CURRENT_TIMESTAMP
                """),
                {
                    "id": class_id,
                    "cid": course_id,
                    "sid": subject_id,
                    "tid": class_tid,
                    "name": class_name,
                    "lesson": current_lesson,
                    "start_date": start_date_obj,
                    "sched_map": schedule_map_json,
                    "is_lang": is_language,
                    "level": proficiency_level,
                },
            )
            upserted_class_ids.add(class_id)

        # ===== Detecção de divergência de Disciplina (Alunos × Turmas) =====
        # Uma turma pertence a UMA disciplina; a aba Turmas é autoritativa. Reporta
        # turmas cuja disciplina na aba Alunos diverge (ou tem mais de uma).
        for (course_name, class_name), alunos_names in alunos_subject.items():
            authoritative = turmas_subject.get((course_name, class_name))
            # Divergência = a aba Alunos lista disciplina(s) diferente(s) da turma.
            # Se a turma não tem linha em Turmas, só conflita quando há +1 disciplina.
            if authoritative is not None:
                divergent = alunos_names != {authoritative}
            else:
                divergent = len(alunos_names) > 1
            if divergent:
                alunos_str = ", ".join(sorted(alunos_names))
                turmas_str = authoritative or "—"
                disciplina_divergente.add(
                    f"{course_name} / {class_name} (Alunos: {alunos_str} | Turmas: {turmas_str})"
                )

        # ===== Pass 2.5: Chaves de ativação (H2.1 — aba "Chaves", opcional) =====
        # Layout: Chave | Curso | Turma | Lote | MaxAtivacoes | Ativa
        # A planilha é a fonte AUTORITATIVA das chaves (o escopo Google é
        # readonly; o bot nunca escreve lá). Regras do upsert:
        #   - Ativa=FALSE (ou linha removida → professor zera a coluna) → 'revoked'
        #   - status 'activated' NUNCA é sobrescrito (histórico é do banco)
        #   - turma inexistente: cria Course/Class mínimos (coorte de leitores)
        keys_upserted = 0
        try:
            chaves_rows = await registry.read_table(sheet_id, "Chaves")
        except Exception:
            chaves_rows = []  # aba opcional

        for row in chaves_rows:
            key = _cell(row, 0).upper()
            course_name = _cell(row, 1)
            class_name = _cell(row, 2)
            batch = _cell(row, 3) or None
            try:
                max_activations = max(1, int(_cell(row, 4) or "1"))
            except ValueError:
                max_activations = 1
            is_key_active = (_cell(row, 5) or "TRUE").strip().lower() in ("true", "1", "sim", "verdadeiro")

            if not key or "-" not in key:
                continue

            kind = "teacher" if key.startswith("PROF-") else "reader"
            class_id = None
            if kind == "reader":
                if not course_name or not class_name:
                    logger.warning("chave_sem_turma_ignorada", key=key)
                    continue
                # Garante a turma-coorte (Course/Class mínimos, mesmo padrão do Pass 2)
                course_id = generate_id("crs", course_name)
                owner_tid = course_owner.get(course_name, BOOTSTRAP_ADMIN_ID)
                await session.execute(
                    text("""
                        INSERT INTO "Courses" (id, teacher_id, name, is_active)
                        VALUES (:id, :tid, :name, true)
                        ON CONFLICT(id) DO NOTHING
                    """),
                    {"id": course_id, "tid": owner_tid, "name": course_name},
                )
                class_id = generate_id("cls", f"{course_id}_{class_name}")
                class_tid = resolve_class_teacher(course_name, class_name)
                await session.execute(
                    text("""
                        INSERT INTO "Classes" (id, course_id, teacher_id, name, current_lesson_number)
                        VALUES (:id, :cid, :tid, :name, 1)
                        ON CONFLICT(id) DO NOTHING
                    """),
                    {"id": class_id, "cid": course_id, "tid": class_tid, "name": class_name},
                )

            await session.execute(
                text("""
                    INSERT INTO "ActivationKeys" (key, kind, class_id, batch, max_activations, status)
                    VALUES (:key, :kind, :cid, :batch, :max_act, :status)
                    ON CONFLICT(key) DO UPDATE SET
                        class_id = excluded.class_id,
                        batch = excluded.batch,
                        max_activations = excluded.max_activations,
                        status = CASE
                            WHEN excluded.status = 'revoked' THEN 'revoked'
                            WHEN "ActivationKeys".status = 'activated' THEN 'activated'
                            ELSE 'available'
                        END,
                        updated_at = CURRENT_TIMESTAMP
                """),
                {"key": key, "kind": kind, "cid": class_id, "batch": batch,
                 "max_act": max_activations,
                 "status": "available" if is_key_active else "revoked"},
            )
            keys_upserted += 1

        stats["keys_upserted"] = keys_upserted

        # ===== Pass 3: Limpeza de matrículas/agendamentos órfãos (alunos sincronizados) =====
        from sqlalchemy import delete
        from src.db.models.course import StudentEnrollment
        from src.db.models.messaging import StudentSchedule

        for sid, class_ids in student_active_classes.items():
            if class_ids:
                stmt = delete(StudentEnrollment).where(StudentEnrollment.student_id == sid).where(StudentEnrollment.class_id.not_in(class_ids))
            else:
                stmt = delete(StudentEnrollment).where(StudentEnrollment.student_id == sid)
            await session.execute(stmt)

        for sid, sched_ids in student_active_schedules.items():
            if sched_ids:
                stmt = delete(StudentSchedule).where(StudentSchedule.student_id == sid).where(StudentSchedule.schedule_id.not_in(sched_ids))
            else:
                stmt = delete(StudentSchedule).where(StudentSchedule.student_id == sid)
            await session.execute(stmt)

        # ===== Pass 3.5: Desativar cursos/disciplinas stale (não vistos na planilha) =====
        # Cursos e disciplinas que existem no banco mas não apareceram em nenhuma
        # aba nesta sync são marcados is_active=false (e.g. renomeados, removidos,
        # ou criados por typo). NÃO deleta — preserva histórico/vetores Qdrant.
        if upserted_course_ids:
            cids = list(upserted_course_ids)
            placeholders = ", ".join(f":cid_{i}" for i in range(len(cids)))
            params = {f"cid_{i}": cid for i, cid in enumerate(cids)}
            params["admin_cid"] = BOOTSTRAP_ADMIN_ID
            await session.execute(
                text(f"""
                    UPDATE "Courses" SET is_active = false, updated_at = CURRENT_TIMESTAMP
                    WHERE is_active = true AND id != :admin_cid AND id NOT IN ({placeholders})
                """),
                params,
            )
        if upserted_subject_ids:
            sids = list(upserted_subject_ids)
            placeholders = ", ".join(f":sid_{i}" for i in range(len(sids)))
            params = {f"sid_{i}": sid for i, sid in enumerate(sids)}
            await session.execute(
                text(f"""
                    UPDATE "Subjects" SET is_active = false, updated_at = CURRENT_TIMESTAMP
                    WHERE is_active = true AND id NOT IN ({placeholders})
                """),
                params,
            )

        await session.commit()

        # Snapshot depois
        stats["teachers_before"] = teachers_before or 0
        stats["teachers_after"] = (await session.execute(text('SELECT count(*) FROM "Teachers" WHERE is_active = true'))).scalar() or 0
        stats["students_before"] = students_before or 0
        stats["students_after"] = (await session.execute(text('SELECT count(*) FROM "Students" WHERE is_active = true'))).scalar() or 0
        stats["courses_before"] = courses_before or 0
        stats["courses_after"] = (await session.execute(text('SELECT count(*) FROM "Courses" WHERE is_active = true'))).scalar() or 0
        stats["subjects_upserted"] = len(upserted_subject_ids)
        stats["subjects_before"] = subjects_before or 0
        stats["subjects_after"] = (await session.execute(text('SELECT count(*) FROM "Subjects"'))).scalar() or 0
        stats["schedules_before"] = schedules_before or 0
        stats["schedules_after"] = (await session.execute(text('SELECT count(*) FROM "StudentSchedule"'))).scalar() or 0

        # Turmas que não têm professor explícito na aba "Professores" (caíram no Admin).
        stats["classes_sem_professor"] = sorted(classes_sem_professor)
        # Turmas com disciplina divergente entre as abas Alunos e Turmas (vale a de Turmas).
        stats["disciplina_divergente"] = sorted(disciplina_divergente)

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
        unlinked_names = [r[0] for r in unlinked_rows]
        stats["unlinked_names"] = unlinked_names
        stats["students_unlinked"] = len(unlinked_names)

        logger.info("Spreadsheet sync completed", stats=stats)
        return stats
