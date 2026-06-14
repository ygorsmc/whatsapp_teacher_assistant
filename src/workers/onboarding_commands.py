"""
Onboarding Commands — self-service do professor (H3.1) + rubrica (H2.2).

Meta do H3.1: turma no ar em 10 minutos SEM humano no loop:
  1. Professor ativa a chave PROF- (activation_worker cria o Teacher);
  2. /criar_turma_[curso]_[turma]      → Course + Class dele;
  3. /adicionar_alunos_[turma]_[lista] → Students + matrículas
     (lista: "Maria:11999990001; João Silva:11999990002");
  4. Compartilha o Drive e roda /sinc_materiais.

/rubrica_[turma]_[critérios] (H2.2): cadastra os critérios oficiais que
ancoram TODA correção dissertativa da turma.
"""

import re
from typing import cast
from uuid import uuid4

from sqlalchemy import text

from src.shared.telemetry import logger
from src.db.database import AsyncSessionLocal
from src.drive.spreadsheet_sync import clean_phone, generate_id
from src.shared.constants.messages import CMD_TEACHER_NOT_FOUND, CMD_CLASS_NOT_FOUND

# "Maria:11999990001; João Silva:11999990002" — separador ';', par 'Nome:fone'
_STUDENT_ITEM_REGEX = re.compile(r"^\s*(?P<name>[^:]+?)\s*:\s*(?P<phone>[\d\s\(\)\-\+\.]+)\s*$")


def parse_student_list(raw: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Interpreta a lista de alunos do /adicionar_alunos. Retorna
    (validos=[(nome, fone_limpo)], invalidos=[trecho]). Pura, testável."""
    valid: list[tuple[str, str]] = []
    invalid: list[str] = []
    for item in (raw or "").split(";"):
        item = item.strip()
        if not item:
            continue
        match = _STUDENT_ITEM_REGEX.match(item)
        if not match:
            invalid.append(item[:40])
            continue
        name = match.group("name").strip()
        phone = clean_phone(match.group("phone"))
        if len(name) < 2 or len(phone) < 10:
            invalid.append(item[:40])
            continue
        valid.append((name, phone))
    return valid, invalid


async def run_create_class_command(channel, sender_id: str, course_name: str, class_name: str):
    """/criar_turma_[curso]_[turma] — cria Course + Class do professor.

    IDs determinísticos (generate_id), idênticos aos da sync de planilha: se o
    professor depois migrar para a planilha, nada duplica.
    """
    try:
        course_name = (course_name or "").strip()
        class_name = (class_name or "").strip()
        if not course_name or not class_name:
            await channel.send_text(sender_id, "❌ Formato: `/criar_turma_MeuCurso_TurmaA`")
            return

        from src.workers.command_worker import _resolve_teacher
        async with AsyncSessionLocal() as session:
            teacher = await _resolve_teacher(session, channel, sender_id)
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return
            teacher_id = teacher[0]

            course_id = generate_id("crs", course_name)
            await session.execute(
                text('''INSERT INTO "Courses" (id, teacher_id, name, is_active)
                        VALUES (:id, :tid, :name, true)
                        ON CONFLICT(id) DO UPDATE SET is_active = true, updated_at = CURRENT_TIMESTAMP'''),
                {"id": course_id, "tid": teacher_id, "name": course_name},
            )
            class_id = generate_id("cls", f"{course_id}_{class_name}")
            await session.execute(
                text('''INSERT INTO "Classes" (id, course_id, teacher_id, name, current_lesson_number)
                        VALUES (:id, :cid, :tid, :name, 1)
                        ON CONFLICT(id) DO UPDATE SET teacher_id = excluded.teacher_id, updated_at = CURRENT_TIMESTAMP'''),
                {"id": class_id, "cid": course_id, "tid": teacher_id, "name": class_name},
            )
            await session.commit()

        await channel.send_text(
            sender_id,
            f"🏫 *Turma criada!*\n\n📚 Curso: *{course_name}*\n👥 Turma: *{class_name}*\n\n"
            f"Próximos passos:\n"
            f"1️⃣ `/adicionar_alunos_{class_name}_Maria:11999990001; João:11999990002`\n"
            f"2️⃣ Suba o material no Drive (pasta `{course_name}` > Disciplina > Aulas/Livro) e rode /sinc_materiais",
        )
        logger.info("class_created_self_service", class_id=class_id, teacher_id=teacher_id)
    except Exception as e:
        logger.error("create_class_failed", error=str(e)[:300])
        await channel.send_text(sender_id, f"❌ Erro ao criar turma: {str(e)[:100]}")


async def run_add_students_command(channel, sender_id: str, class_query: str, students_raw: str):
    """/adicionar_alunos_[turma]_[Nome:fone; ...] — cria Students + matrículas.
    Alunos criados com registration_source='teacher_command' (o professor é o
    controlador dos dados — LGPD igual ao fluxo de planilha)."""
    try:
        from src.workers.reading_commands import _resolve_teacher_and_class

        students, invalid = parse_student_list(students_raw)
        if not students:
            await channel.send_text(
                sender_id,
                "❌ Nenhum aluno válido. Formato:\n`/adicionar_alunos_TurmaA_Maria:11999990001; João Silva:11999990002`",
            )
            return

        async with AsyncSessionLocal() as session:
            teacher, klass = await _resolve_teacher_and_class(session, channel, sender_id, class_query)
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return
            if not klass:
                await channel.send_text(sender_id, CMD_CLASS_NOT_FOUND.format(class_query=class_query))
                return

            class_id, c_name = klass[0], klass[1]
            added = 0
            for name, phone in students:
                student_id = generate_id("stu", phone)
                res = await session.execute(
                    text('''INSERT INTO "Students" (id, name, phone, is_active, registration_source)
                            VALUES (:id, :name, :phone, true, 'teacher_command')
                            ON CONFLICT(phone) DO UPDATE SET
                                name = excluded.name, is_active = true, updated_at = CURRENT_TIMESTAMP
                            RETURNING id'''),
                    {"id": student_id, "name": name, "phone": phone},
                )
                student_id = cast(str, res.scalar())  # RETURNING id após INSERT/UPSERT
                await session.execute(
                    text('''INSERT INTO "StudentEnrollment" (enrollment_id, student_id, class_id)
                            VALUES (:id, :sid, :cid)
                            ON CONFLICT(student_id, class_id) DO NOTHING'''),
                    {"id": generate_id("enr", f"{student_id}_{class_id}"), "sid": student_id, "cid": class_id},
                )
                added += 1
            await session.commit()

        msg = f"✅ *{added} aluno(s)* matriculado(s) na turma *{c_name}*!"
        if invalid:
            msg += "\n\n⚠️ Itens ignorados (formato inválido):\n" + "\n".join(f"• {i}" for i in invalid)
        msg += "\n\n_Os alunos já podem mandar mensagem para o bot. Quizzes: /quiz_" + c_name.replace(" ", "") + "_"
        await channel.send_text(sender_id, msg[:4000])
        logger.info("students_added_self_service", class_id=class_id, added=added)
    except Exception as e:
        logger.error("add_students_failed", error=str(e)[:300])
        await channel.send_text(sender_id, f"❌ Erro ao adicionar alunos: {str(e)[:100]}")


async def run_rubric_command(channel, sender_id: str, class_query: str, criteria: str):
    """/rubrica_[turma]_[critérios] — upsert da rubrica de correção (H2.2)."""
    try:
        from src.workers.reading_commands import _resolve_teacher_and_class

        criteria = (criteria or "").strip()
        if len(criteria) < 10:
            await channel.send_text(
                sender_id,
                "❌ Descreva os critérios. Exemplo:\n"
                "`/rubrica_TurmaA_1. Tese clara (0-2); 2. Argumentação (0-4); 3. Norma culta (0-4). Escala 0-10`",
            )
            return

        async with AsyncSessionLocal() as session:
            teacher, klass = await _resolve_teacher_and_class(session, channel, sender_id, class_query)
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return
            if not klass:
                await channel.send_text(sender_id, CMD_CLASS_NOT_FOUND.format(class_query=class_query))
                return

            c_name, course_id, subject_id = klass[1], klass[2], klass[3]

            # Escala: se o professor citar "escala X" no texto, extraímos o rótulo
            scale_match = re.search(r"escala\s+([\w\s\-à-ú]{1,30})$", criteria, re.IGNORECASE)
            scale = scale_match.group(1).strip() if scale_match else "0 a 10"

            subject_clause = "subject_id = :sub" if subject_id else "subject_id IS NULL"
            res = await session.execute(
                text(f'SELECT id FROM "Rubrics" WHERE course_id = :cid AND {subject_clause}'),
                {"cid": course_id, **({"sub": subject_id} if subject_id else {})},
            )
            rubric_id = res.scalar_one_or_none()
            if rubric_id:
                await session.execute(
                    text('''UPDATE "Rubrics" SET criteria = :crit, scale = :scale,
                            updated_at = CURRENT_TIMESTAMP WHERE id = :rid'''),
                    {"crit": criteria, "scale": scale, "rid": rubric_id},
                )
            else:
                await session.execute(
                    text('''INSERT INTO "Rubrics" (id, course_id, subject_id, criteria, scale)
                            VALUES (:id, :cid, :sub, :crit, :scale)'''),
                    {"id": str(uuid4()), "cid": course_id, "sub": subject_id,
                     "crit": criteria, "scale": scale},
                )
            await session.commit()

        await channel.send_text(
            sender_id,
            f"📋 *Rubrica cadastrada — {c_name}*\n\nEscala: *{scale}*\n\n{criteria[:1500]}\n\n"
            f"_Toda redação enviada pelos alunos (/corrigir + foto) será corrigida com ESTES critérios._",
        )
    except Exception as e:
        logger.error("rubric_command_failed", error=str(e)[:300])
        await channel.send_text(sender_id, f"❌ Erro ao cadastrar rubrica: {str(e)[:100]}")
