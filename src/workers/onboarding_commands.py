"""
Onboarding Commands — rubrica de correção do professor (H2.2).

/rubrica_[turma]_[critérios] (H2.2): cadastra os critérios oficiais que
ancoram TODA correção dissertativa da turma.

(O onboarding self-service por chat — /criar_turma e /adicionar_alunos — foi
descontinuado: a criação de turmas/alunos passa pela planilha de cadastro
+ /sinc_alunos.)
"""

import re
from uuid import uuid4

from sqlalchemy import text

from src.shared.telemetry import logger
from src.db.database import AsyncSessionLocal
from src.shared.constants.messages import CMD_TEACHER_NOT_FOUND, CMD_CLASS_NOT_FOUND



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
