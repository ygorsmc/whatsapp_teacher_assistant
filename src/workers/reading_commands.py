"""
Reading Commands — handlers dos comandos do Addon de Livros e Apostilas.

Professor (validação de posse via Classes.teacher_id, como todo comando
gerencial):
  /plano_leitura_[turma]_[ritmo]    → cria a Jornada de Leitura da turma
  /simulado_[turma]_[até_capítulo]  → prova cruzando capítulos do QuizBank
  /vincular_aula_[turma]_[aula]_[capítulo] → mapeamento Aula ↔ Capítulo
  /errata_[turma]_[seção]_[correção]       → Errata Viva
  /ativar_capitulos_[turma]_[1,3,5]        → Modo Professor Curador
  /gabarito_[turma]_[cap3_ex2]_[resposta]  → Gabarito Verificado (âncora da IA)
  /mapa_dificuldade_[turma]  → heatmap de erros por capítulo (Telemetria)
  /funil_conclusao_[turma]   → onde a turma "abandona" o livro (Telemetria)
  /corpus_duvidas_[turma]    → dúvidas agrupadas por capítulo (Telemetria)

Aluno (allowlist roteada pelo webhook):
  /pausar_leitura  /retomar_leitura  /meu_progresso  /flashcards

LGPD (Telemetria Editorial): os relatórios são agregados e anonimizados —
coortes, nunca dados individuais. Células com menos de MIN_COHORT_SIZE
respostas são suprimidas.
"""

import json
import re
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import text

from src.shared.config import settings
from src.shared.telemetry import logger
from src.db.database import AsyncSessionLocal
from src.shared.constants.messages import (
    CMD_TEACHER_NOT_FOUND,
    CMD_CLASS_NOT_FOUND,
    READING_PAUSED,
    READING_RESUMED,
    READING_NO_PLAN,
    SIMULADO_STUDENT_INTRO,
)
from src.workers.reading_journey import (
    MATERIAL_LABELS,
    compute_reading_next_trigger,
    build_progress_line,
    progress_percent,
    section_label_for,
    _get_class_context,
)

# LGPD: tamanho mínimo da coorte para exibir uma célula de telemetria.
MIN_COHORT_SIZE = 3

# Limite de questões por simulado (taxa de conclusão > profundidade no WhatsApp)
SIMULADO_MAX_QUESTIONS = 5

VALID_PACES = {
    "diario": "daily", "diário": "daily", "daily": "daily", "diaria": "daily", "diária": "daily",
    "semanal": "weekly", "weekly": "weekly",
}

_ANSWER_KEY_REGEX = re.compile(r"^(?P<ref>cap\d+_ex[\w\.]+)_(?P<answer>.+)$", re.IGNORECASE | re.DOTALL)
_LINK_REGEX = re.compile(r"^(?P<lesson>\d+)_(?P<chapter>\d+)$")
_ERRATA_SPLIT_REGEX = re.compile(r"^(?P<ref>[^_]+)_(?P<correction>.+)$", re.DOTALL)


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _progress_bar(percent: int, width: int = 10) -> str:
    filled = round(width * percent / 100)
    return "▓" * filled + "░" * (width - filled)


async def _resolve_teacher_and_class(session, channel, sender_id: str, class_query: str):
    """Resolve (teacher, class) com a MESMA validação de posse dos comandos
    gerenciais clássicos. Retorna (teacher, klass) — qualquer um pode ser None.
    """
    from src.workers.command_worker import _resolve_teacher, _resolve_owned_class
    teacher = await _resolve_teacher(session, channel, sender_id)
    if not teacher:
        return None, None
    klass = await _resolve_owned_class(session, teacher[0], teacher[1], class_query)
    return teacher, klass


# ==============================================================================
# PROFESSOR — /plano_leitura_[turma]_[ritmo] (Fase 2)
# ==============================================================================

async def run_reading_plan_command(channel, sender_id: str, class_query: str, pace_arg: str):
    """Cria (ou reativa) o plano de leitura para TODOS os alunos matriculados.

    O total de seções vem da estrutura do Google Drive (pasta Livro/Apostila
    da disciplina): total_sections = maior número de seção indexável. Alunos
    que já têm plano preservam o current_section (replanejar nunca pune).
    """
    try:
        pace = VALID_PACES.get((pace_arg or "").strip().lower())
        if not pace:
            await channel.send_text(
                sender_id,
                "❌ Ritmo inválido. Use: `/plano_leitura_Turma_diario` ou `/plano_leitura_Turma_semanal`",
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

            class_id, c_name, course_id, subject_id, subject_name = klass[0], klass[1], klass[2], klass[3], klass[5]

            res_course = await session.execute(
                text('SELECT name FROM "Courses" WHERE id = :cid'), {"cid": course_id}
            )
            course_name = res_course.scalar()

            students = (await session.execute(
                text('''SELECT s.id FROM "Students" s
                        JOIN "StudentEnrollment" se ON se.student_id = s.id
                        WHERE se.class_id = :cid AND s.is_active = true'''),
                {"cid": class_id},
            )).fetchall()

        if not students:
            await channel.send_text(sender_id, f"❌ Nenhum aluno ativo encontrado na turma '{c_name}'.")
            return

        # Descobre o material denso no Drive (estrutura Curso > Disciplina >
        # Livro/Apostila > Seções). Preferência: Livro > Apostila.
        await channel.send_text(sender_id, "⏳ Verificando o material (Livro/Apostila) no Google Drive...")
        from src.drive.factory import get_material_source
        try:
            files = await get_material_source().list_materials(settings.GOOGLE_DRIVE_FOLDER_ID)
        except Exception as e:
            logger.error("reading_plan_drive_failed", error=str(e)[:300])
            files = []

        material_type, total_sections = None, 0
        for wanted in ("book", "workbook"):
            sections = [
                f.lesson_number for f in files
                if f.material_type == wanted
                and f.course_name == course_name
                and (not subject_name or f.subject_name == subject_name)
            ]
            if sections:
                material_type, total_sections = wanted, max(sections)
                break

        if not material_type or total_sections < 1:
            await channel.send_text(
                sender_id,
                "❌ Não encontrei material de *Livro* ou *Apostila* para esta disciplina no Google Drive.\n\n"
                "Organize as pastas como `Curso > Disciplina > Livro > Cap 1, Cap 2...`, "
                "rode /sinc_materiais e tente novamente.",
            )
            return

        # Upsert por aluno (SELECT→UPDATE/INSERT explícito: subject_id NULL não
        # deduplica via UNIQUE no Postgres). current_section é PRESERVADO.
        next_trigger = compute_reading_next_trigger(pace, "07:00")
        created, updated = 0, 0
        subject_clause = "subject_id = :sub" if subject_id else "subject_id IS NULL"

        async with AsyncSessionLocal() as session:
            for st in students:
                res = await session.execute(
                    text(f'''SELECT id FROM "ReadingPlans"
                             WHERE student_id = :stid AND course_id = :cid
                               AND {subject_clause} AND material_type = :mat'''),
                    {"stid": st[0], "cid": course_id, "mat": material_type,
                     **({"sub": subject_id} if subject_id else {})},
                )
                plan_id = res.scalar_one_or_none()
                if plan_id:
                    await session.execute(
                        text('''UPDATE "ReadingPlans"
                                SET pace = :pace, total_sections = :total,
                                    is_paused = false, next_trigger_at = :next,
                                    last_suggestion_at = NULL, next_checkin_at = NULL,
                                    updated_at = CURRENT_TIMESTAMP
                                WHERE id = :pid'''),
                        {"pace": pace, "total": total_sections, "next": next_trigger, "pid": plan_id},
                    )
                    updated += 1
                else:
                    await session.execute(
                        text('''INSERT INTO "ReadingPlans"
                                    (id, student_id, course_id, subject_id, material_type,
                                     total_sections, current_section, pace, preferred_time,
                                     streak_days, is_paused, next_trigger_at)
                                VALUES
                                    (:id, :stid, :cid, :sub, :mat,
                                     :total, 0, :pace, '07:00',
                                     0, false, :next)'''),
                        {"id": str(uuid4()), "stid": st[0], "cid": course_id,
                         "sub": subject_id, "mat": material_type,
                         "total": total_sections, "pace": pace, "next": next_trigger},
                    )
                    created += 1
            await session.commit()

        label = MATERIAL_LABELS.get(material_type, "livro")
        pace_label = "diário" if pace == "daily" else "semanal"
        msg = (
            f"📖 *Jornada de Leitura criada — {c_name}*\n\n"
            f"Material: *{label.capitalize()}* ({total_sections} seções)\n"
            f"Ritmo: *{pace_label}* | Sugestões às *07:00*, check-in às *19:00*\n\n"
            f"✅ {created} plano(s) novo(s) | 🔄 {updated} reativado(s)/ajustado(s)\n\n"
            f"_Cada aluno recebe microdoses de 10-15 min e avança ao passar no quiz de check-in. "
            f"Alunos podem usar /pausar_leitura, /retomar_leitura e /meu_progresso._"
        )
        await channel.send_text(sender_id, msg)
        logger.info("reading_plan_created", class_id=class_id, created=created, updated=updated)
    except Exception as e:
        logger.error("reading_plan_command_failed", error=str(e)[:300])
        await channel.send_text(sender_id, f"❌ Erro ao criar plano de leitura: {str(e)[:100]}")


# ==============================================================================
# PROFESSOR — /simulado_[turma]_[até_capítulo] (Fase 6.3)
# ==============================================================================

async def run_simulado_command(channel, sender_id: str, class_query: str, max_chapter_arg: str):
    """Monta um simulado cruzando questões de múltiplos capítulos/tópicos do
    QuizBank (capítulos 1 a N) e o envia a todos os alunos na janela de 24h.
    Custo $0: todas as questões já estão cacheadas no banco."""
    try:
        try:
            max_chapter = int(max_chapter_arg.strip())
        except (ValueError, AttributeError):
            await channel.send_text(sender_id, "❌ Capítulo inválido. Exemplo: `/simulado_TurmaA_5` (capítulos 1 a 5).")
            return

        async with AsyncSessionLocal() as session:
            teacher, klass = await _resolve_teacher_and_class(session, channel, sender_id, class_query)
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return
            if not klass:
                await channel.send_text(sender_id, CMD_CLASS_NOT_FOUND.format(class_query=class_query))
                return

            class_id, c_name, course_id, subject_id = klass[0], klass[1], klass[2], klass[3]
            subject_name = klass[5]                 # _resolve_owned_class
            is_language = bool(getattr(klass, "is_language", False))
            teacher_id = teacher[0]

            students = (await session.execute(
                text('''SELECT s.id, s.phone, s.name, s.telegram_chat_id, s.preferred_channel,
                               (SELECT MAX(timestamp) FROM "StudentInteractions"
                                WHERE student_id = s.id
                                  AND interaction_type IN ('message_received', 'quiz_answered')
                               ) AS last_interaction
                        FROM "Students" s
                        JOIN "StudentEnrollment" se ON s.id = se.student_id
                        WHERE se.class_id = :cid AND s.is_active = true'''),
                {"cid": class_id},
            )).fetchall()

        from src.rag.quiz_bank import list_simulado_questions
        # F8 — Exam prep: turma de idioma com EXAM_PREP_ENABLED → simulado ESTILO-
        # PROVA (banco material_type='exam'; semeia se faltar). Senão, simulado normal.
        from src.workers.exam_prep import exam_prep_enabled, ensure_exam_questions, EXAM_MATERIAL_TYPE
        exam_mode = exam_prep_enabled() and is_language
        if exam_mode:
            await channel.send_text(sender_id, "⏳ Preparando simulado estilo-prova (TOEFL/IELTS)...")
            await ensure_exam_questions(
                teacher_id=teacher_id, course_id=course_id, course_name=c_name,
                subject_name=subject_name, subject_id=subject_id,
                max_chapter=max_chapter, target_count=SIMULADO_MAX_QUESTIONS,
            )
        questions = await list_simulado_questions(
            course_id=course_id, subject_id=subject_id,
            max_section=max_chapter, limit=SIMULADO_MAX_QUESTIONS,
            material_type=EXAM_MATERIAL_TYPE if exam_mode else None,
        )

        if not questions:
            await channel.send_text(
                sender_id,
                f"❌ O Banco de Questões ainda não tem questões até o capítulo {max_chapter} para esta turma.\n\n"
                "_O banco cresce conforme os alunos recebem quizzes e check-ins de leitura._",
            )
            return

        if not students:
            await channel.send_text(sender_id, f"❌ Nenhum aluno ativo encontrado na turma '{c_name}'.")
            return

        from datetime import timedelta
        from uuid import uuid4 as _uuid4
        from src.channel.factory import get_channel
        from src.workers.micro_learning import send_quiz_to_student
        from src.workers.spaced_repetition import _shuffled_quiz_from_bank
        from src.shared.constants.messages import SIMULADO_TIMED_NOTE

        # H2.3 — Simulado CRONOMETRADO: registra o challenge; o scheduler fecha
        # a janela e devolve a cada aluno o percentil anônimo da turma.
        now = _naive_utc_now()
        challenge_id = str(_uuid4())
        async with AsyncSessionLocal() as session:
            await session.execute(
                text('''INSERT INTO "ClassChallenges"
                            (id, class_id, kind, question_count, sent_at, closes_at, status)
                        VALUES (:id, :cid, 'simulado', :qc, :sent, :closes, 'open')'''),
                {"id": challenge_id, "cid": class_id, "qc": len(questions),
                 "sent": now, "closes": now + timedelta(hours=settings.SIMULADO_WINDOW_HOURS)},
            )
            await session.commit()

        default_ct = settings.CHANNEL_BACKEND.lower()
        twenty_four_hours_ago = now - timedelta(hours=24)
        delivered, skipped = 0, []

        intro = SIMULADO_STUDENT_INTRO.format(
            scope_label=c_name, max_section=max_chapter, count=len(questions),
        ) + SIMULADO_TIMED_NOTE.format(hours=settings.SIMULADO_WINDOW_HOURS)

        for st in students:
            ct = (st[4] or default_ct).lower()
            addr = st[3] if ct == "telegram" else st[1]
            if not addr:
                skipped.append(st[2])
                continue
            if ct == "whatsapp":
                last_int = st[5]
                if isinstance(last_int, str):
                    last_int = datetime.fromisoformat(last_int)
                if last_int is not None and last_int.tzinfo is not None:
                    last_int = last_int.replace(tzinfo=None)
                if not (last_int and last_int > twenty_four_hours_ago):
                    skipped.append(st[2])
                    continue

            try:
                c = get_channel(ct)
                await c.send_text(addr, intro)
                for q in questions:
                    quiz_output = _shuffled_quiz_from_bank(q)
                    await send_quiz_to_student(
                        channel=c,
                        student={
                            "id": st[0],
                            "sender_id": addr,
                            "course_id": course_id,
                            "lesson_pointer": str(q["lesson_number"]),
                        },
                        quiz=quiz_output,
                        quiz_bank_id=q["id"],
                        challenge_id=challenge_id,
                    )
                delivered += 1
            except Exception as e:
                logger.error("simulado_delivery_failed", student_id=st[0], error=str(e)[:200])

        chapters = sorted({q["lesson_number"] for q in questions})
        msg = (
            f"🎯 *Simulado enviado — {c_name}*\n\n"
            f"📄 {len(questions)} questões (capítulos: {', '.join(map(str, chapters))})\n"
            f"✅ Entregue a *{delivered}* aluno(s).\n"
            f"⏰ Fecha em *{settings.SIMULADO_WINDOW_HOURS}h* — cada aluno receberá o percentil anônimo e você, o resumo."
        )
        if skipped:
            msg += f"\n⚠️ {len(skipped)} aluno(s) fora da janela de 24h ou sem vínculo não receberam."
        await channel.send_text(sender_id, msg)
    except Exception as e:
        logger.error("simulado_command_failed", error=str(e)[:300])
        await channel.send_text(sender_id, f"❌ Erro ao montar simulado: {str(e)[:100]}")


# ==============================================================================
# PROFESSOR — /vincular_aula_[turma]_[aula]_[capítulo] (Fase 6.5)
# ==============================================================================

async def run_link_lesson_chapter_command(channel, sender_id: str, class_query: str, extra: str):
    try:
        match = _LINK_REGEX.match((extra or "").strip())
        if not match:
            await channel.send_text(
                sender_id,
                "❌ Formato inválido. Use: `/vincular_aula_Turma_2_3` (Aula 2 → Capítulo 3).",
            )
            return

        lesson_number = int(match.group("lesson"))
        chapter_number = int(match.group("chapter"))

        async with AsyncSessionLocal() as session:
            teacher, klass = await _resolve_teacher_and_class(session, channel, sender_id, class_query)
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return
            if not klass:
                await channel.send_text(sender_id, CMD_CLASS_NOT_FOUND.format(class_query=class_query))
                return

            c_name, course_id, subject_id = klass[1], klass[2], klass[3]
            subject_clause = "subject_id = :sub" if subject_id else "subject_id IS NULL"

            res = await session.execute(
                text(f'''SELECT id FROM "LessonChapterLinks"
                         WHERE course_id = :cid AND {subject_clause} AND lesson_number = :lesson'''),
                {"cid": course_id, "lesson": lesson_number,
                 **({"sub": subject_id} if subject_id else {})},
            )
            link_id = res.scalar_one_or_none()
            if link_id:
                await session.execute(
                    text('''UPDATE "LessonChapterLinks"
                            SET chapter_number = :chapter, updated_at = CURRENT_TIMESTAMP
                            WHERE id = :lid'''),
                    {"chapter": chapter_number, "lid": link_id},
                )
            else:
                await session.execute(
                    text('''INSERT INTO "LessonChapterLinks"
                                (id, course_id, subject_id, lesson_number, chapter_number)
                            VALUES (:id, :cid, :sub, :lesson, :chapter)'''),
                    {"id": str(uuid4()), "cid": course_id, "sub": subject_id,
                     "lesson": lesson_number, "chapter": chapter_number},
                )
            await session.commit()

        await channel.send_text(
            sender_id,
            f"🔗 *Vínculo criado — {c_name}*\n\nAula *{lesson_number}* ↔ Capítulo *{chapter_number}* do livro.\n\n"
            f"_Quando um aluno responder o quiz da Aula {lesson_number}, o bot vai sugerir revisar o Capítulo {chapter_number}._",
        )
    except Exception as e:
        logger.error("link_lesson_chapter_failed", error=str(e)[:300])
        await channel.send_text(sender_id, f"❌ Erro ao vincular: {str(e)[:100]}")


# ==============================================================================
# PROFESSOR — /errata_[turma]_[seção]_[correção] (Fase 6.6)
# ==============================================================================

async def run_errata_command(channel, sender_id: str, class_query: str, extra: str):
    try:
        match = _ERRATA_SPLIT_REGEX.match((extra or "").strip())
        if not match:
            await channel.send_text(
                sender_id,
                "❌ Formato inválido. Use: `/errata_Turma_cap3_Onde diz 'X', leia 'Y'`",
            )
            return

        section_ref = match.group("ref").strip()
        correction = match.group("correction").strip()
        num_match = re.search(r"(\d+)", section_ref)
        section_number = int(num_match.group(1)) if num_match else None

        async with AsyncSessionLocal() as session:
            teacher, klass = await _resolve_teacher_and_class(session, channel, sender_id, class_query)
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return
            if not klass:
                await channel.send_text(sender_id, CMD_CLASS_NOT_FOUND.format(class_query=class_query))
                return

            c_name, course_id, subject_id = klass[1], klass[2], klass[3]
            await session.execute(
                text('''INSERT INTO "Erratas"
                            (id, course_id, subject_id, section_ref, section_number, correction, is_active)
                        VALUES (:id, :cid, :sub, :ref, :num, :corr, true)'''),
                {"id": str(uuid4()), "cid": course_id, "sub": subject_id,
                 "ref": section_ref, "num": section_number, "corr": correction},
            )
            await session.commit()

        await channel.send_text(
            sender_id,
            f"⚠️ *Errata cadastrada — {c_name}*\n\nSeção: *{section_ref}*\nCorreção: {correction}\n\n"
            f"_Os alunos verão este aviso automaticamente ao ler ou perguntar sobre essa seção._",
        )
    except Exception as e:
        logger.error("errata_command_failed", error=str(e)[:300])
        await channel.send_text(sender_id, f"❌ Erro ao cadastrar errata: {str(e)[:100]}")


# ==============================================================================
# PROFESSOR — /ativar_capitulos_[turma]_[1,3,5] (Fase 6.7)
# ==============================================================================

async def run_activate_chapters_command(channel, sender_id: str, class_query: str, extra: str):
    """Modo Professor Curador: só os capítulos listados contam para o scheduler
    da leitura e para o RAG de material denso. `todos` reativa tudo."""
    try:
        raw = (extra or "").strip().lower()
        chapters: list[int] | None
        if raw in ("todos", "all", "tudo"):
            chapters = None
        else:
            try:
                chapters = sorted({int(c) for c in raw.replace(" ", "").split(",") if c})
            except ValueError:
                chapters = []
            if not chapters:
                await channel.send_text(
                    sender_id,
                    "❌ Formato inválido. Use: `/ativar_capitulos_Turma_1,3,5` ou `/ativar_capitulos_Turma_todos`",
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
            await session.execute(
                text('''UPDATE "Classes"
                        SET active_chapters = :chapters, updated_at = CURRENT_TIMESTAMP
                        WHERE id = :cid'''),
                {"chapters": json.dumps(chapters) if chapters else None, "cid": class_id},
            )
            await session.commit()

        if chapters:
            await channel.send_text(
                sender_id,
                f"📑 *Capítulos ativos — {c_name}:* {', '.join(map(str, chapters))}\n\n"
                f"_Os demais capítulos serão ignorados pelo plano de leitura e pelo RAG do livro._",
            )
        else:
            await channel.send_text(sender_id, f"📑 *Todos os capítulos reativados* para a turma {c_name}.")
    except Exception as e:
        logger.error("activate_chapters_failed", error=str(e)[:300])
        await channel.send_text(sender_id, f"❌ Erro ao ativar capítulos: {str(e)[:100]}")


# ==============================================================================
# PROFESSOR — /gabarito_[turma]_[cap3_ex2]_[resposta] (Fase 5)
# ==============================================================================

async def run_answer_key_command(channel, sender_id: str, class_query: str, extra: str):
    """Cadastra o Gabarito Verificado: a IA passa a usar a resposta oficial
    como âncora das explicações socráticas (elimina alucinação)."""
    try:
        match = _ANSWER_KEY_REGEX.match((extra or "").strip())
        if not match:
            await channel.send_text(
                sender_id,
                "❌ Formato inválido. Use: `/gabarito_Turma_cap3_ex2_42J`\n\n"
                "_(capítulo 3, exercício 2, resposta '42J' — a resposta pode ser um texto livre)_",
            )
            return

        section_ref = match.group("ref").lower()
        answer = match.group("answer").strip()

        async with AsyncSessionLocal() as session:
            teacher, klass = await _resolve_teacher_and_class(session, channel, sender_id, class_query)
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return
            if not klass:
                await channel.send_text(sender_id, CMD_CLASS_NOT_FOUND.format(class_query=class_query))
                return

            c_name, course_id, subject_id = klass[1], klass[2], klass[3]
            subject_clause = "subject_id = :sub" if subject_id else "subject_id IS NULL"

            res = await session.execute(
                text(f'''SELECT id FROM "AnswerKeys"
                         WHERE course_id = :cid AND {subject_clause} AND section_ref = :ref'''),
                {"cid": course_id, "ref": section_ref,
                 **({"sub": subject_id} if subject_id else {})},
            )
            key_id = res.scalar_one_or_none()
            if key_id:
                await session.execute(
                    text('''UPDATE "AnswerKeys"
                            SET correct_answer = :ans, updated_at = CURRENT_TIMESTAMP
                            WHERE id = :kid'''),
                    {"ans": answer, "kid": key_id},
                )
            else:
                await session.execute(
                    text('''INSERT INTO "AnswerKeys"
                                (id, course_id, subject_id, section_ref, correct_answer)
                            VALUES (:id, :cid, :sub, :ref, :ans)'''),
                    {"id": str(uuid4()), "cid": course_id, "sub": subject_id,
                     "ref": section_ref, "ans": answer},
                )
            await session.commit()

        await channel.send_text(
            sender_id,
            f"🔑 *Gabarito cadastrado — {c_name}*\n\n*{section_ref}* → {answer}\n\n"
            f"_A IA usará esta resposta oficial como âncora ao guiar os alunos (modo socrático), sem revelá-la de imediato._",
        )
    except Exception as e:
        logger.error("answer_key_command_failed", error=str(e)[:300])
        await channel.send_text(sender_id, f"❌ Erro ao cadastrar gabarito: {str(e)[:100]}")


# ==============================================================================
# PROFESSOR — TELEMETRIA EDITORIAL (Fase 7)
# ==============================================================================

async def run_difficulty_map_command(channel, sender_id: str, class_query: str):
    """/mapa_dificuldade_[turma] — heatmap textual do índice de erro por
    capítulo/seção (QuizBank × PendingQuizzes). LGPD: agregados por coorte;
    células com menos de MIN_COHORT_SIZE respostas são suprimidas."""
    try:
        async with AsyncSessionLocal() as session:
            teacher, klass = await _resolve_teacher_and_class(session, channel, sender_id, class_query)
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return
            if not klass:
                await channel.send_text(sender_id, CMD_CLASS_NOT_FOUND.format(class_query=class_query))
                return

            class_id, c_name = klass[0], klass[1]

            rows = (await session.execute(
                text('''SELECT qb.material_type, qb.lesson_number,
                               COUNT(*) AS total,
                               SUM(CASE WHEN pq.is_correct THEN 0 ELSE 1 END) AS erros
                        FROM "PendingQuizzes" pq
                        JOIN "QuizBank" qb ON qb.id = pq.quiz_bank_id
                        JOIN "StudentEnrollment" se ON se.student_id = pq.student_id
                        WHERE se.class_id = :cid AND pq.status = 'answered'
                        GROUP BY qb.material_type, qb.lesson_number
                        ORDER BY qb.material_type, qb.lesson_number'''),
                {"cid": class_id},
            )).fetchall()

        if not rows:
            await channel.send_text(
                sender_id,
                f"🗺️ *Mapa de Dificuldade — {c_name}*\n\nAinda não há respostas de quizzes vinculadas ao Banco de Questões nesta turma.",
            )
            return

        msg = f"🗺️ *Mapa de Dificuldade — {c_name}*\n_(índice de erro por capítulo/seção — dados agregados)_\n\n"
        current_material = None
        suppressed = 0
        for r in rows:
            mat, section, total, erros = r[0], r[1], r[2] or 0, r[3] or 0
            if mat != current_material:
                current_material = mat
                label = MATERIAL_LABELS.get(mat, mat).capitalize()
                msg += f"*{label}:*\n"
            if total < MIN_COHORT_SIZE:
                suppressed += 1
                continue
            error_pct = round(100 * erros / total)
            emoji = "🟩" if error_pct < 25 else "🟨" if error_pct < 50 else "🟧" if error_pct < 75 else "🟥"
            section_word = "Cap." if mat == "book" else ("Mód." if mat == "workbook" else "Aula")
            msg += f"{emoji} {section_word} {section} — {error_pct}% de erro ({total} respostas)\n"

        if suppressed:
            msg += f"\n_{suppressed} seção(ões) ocultada(s): menos de {MIN_COHORT_SIZE} respostas (LGPD/significância)._"
        msg += "\n💡 _Seções 🟧/🟥 merecem reforço na próxima aula — e atenção do autor na próxima edição._"
        await channel.send_text(sender_id, msg[:4000])
    except Exception as e:
        logger.error("difficulty_map_failed", error=str(e)[:300])
        await channel.send_text(sender_id, f"❌ Erro ao gerar mapa: {str(e)[:100]}")


async def run_completion_funnel_command(channel, sender_id: str, class_query: str):
    """/funil_conclusao_[turma] — quantos alunos chegaram a cada capítulo.
    Mostra onde a turma "abandona" o livro. LGPD: somente contagens."""
    try:
        async with AsyncSessionLocal() as session:
            teacher, klass = await _resolve_teacher_and_class(session, channel, sender_id, class_query)
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return
            if not klass:
                await channel.send_text(sender_id, CMD_CLASS_NOT_FOUND.format(class_query=class_query))
                return

            class_id, c_name, course_id, subject_id = klass[0], klass[1], klass[2], klass[3]
            subject_clause = "rp.subject_id = :sub" if subject_id else "rp.subject_id IS NULL"

            rows = (await session.execute(
                text(f'''SELECT rp.current_section, rp.total_sections
                         FROM "ReadingPlans" rp
                         JOIN "StudentEnrollment" se ON se.student_id = rp.student_id
                         WHERE se.class_id = :clid
                           AND rp.course_id = :cid
                           AND {subject_clause}'''),
                {"clid": class_id, "cid": course_id,
                 **({"sub": subject_id} if subject_id else {})},
            )).fetchall()

        if not rows:
            await channel.send_text(
                sender_id,
                f"📉 *Funil de Conclusão — {c_name}*\n\nNenhum plano de leitura ativo nesta turma. Crie com `/plano_leitura_{class_query}_diario`.",
            )
            return

        total_students = len(rows)
        max_sections = max(r[1] for r in rows)
        msg = f"📉 *Funil de Conclusão — {c_name}*\n_{total_students} aluno(s) com plano de leitura_\n\n"

        for k in range(1, max_sections + 1):
            reached = sum(1 for r in rows if (r[0] or 0) >= k)
            pct = round(100 * reached / total_students) if total_students else 0
            msg += f"Cap. {k}: {_progress_bar(pct)} {reached}/{total_students} ({pct}%)\n"

        finished = sum(1 for r in rows if (r[0] or 0) >= r[1])
        msg += f"\n🏁 Concluíram o material: *{finished}/{total_students}*"

        # Maior queda entre capítulos consecutivos = ponto de abandono
        drops = []
        prev = total_students
        for k in range(1, max_sections + 1):
            reached = sum(1 for r in rows if (r[0] or 0) >= k)
            drops.append((prev - reached, k))
            prev = reached
        worst_drop, worst_chapter = max(drops, key=lambda d: d[0]) if drops else (0, 0)
        if worst_drop > 0:
            msg += f"\n⚠️ Maior abandono: *Capítulo {worst_chapter}* (-{worst_drop} aluno(s))."

        await channel.send_text(sender_id, msg[:4000])
    except Exception as e:
        logger.error("completion_funnel_failed", error=str(e)[:300])
        await channel.send_text(sender_id, f"❌ Erro ao gerar funil: {str(e)[:100]}")


async def run_doubts_corpus_command(channel, sender_id: str, class_query: str):
    """/corpus_duvidas_[turma] — dúvidas dos últimos 30 dias agrupadas por
    capítulo/seção via LLM. LGPD: o prompt exige anonimização e agregação."""
    try:
        from datetime import timedelta
        async with AsyncSessionLocal() as session:
            teacher, klass = await _resolve_teacher_and_class(session, channel, sender_id, class_query)
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return
            if not klass:
                await channel.send_text(sender_id, CMD_CLASS_NOT_FOUND.format(class_query=class_query))
                return

            class_id, c_name = klass[0], klass[1]
            cutoff = _naive_utc_now() - timedelta(days=30)

            rows = (await session.execute(
                text('''SELECT si.message_content
                        FROM "StudentInteractions" si
                        JOIN "StudentEnrollment" se ON se.student_id = si.student_id
                        WHERE se.class_id = :cid
                          AND si.interaction_type = 'message_received'
                          AND si.timestamp >= :cutoff
                          AND si.message_content IS NOT NULL
                        ORDER BY si.timestamp ASC
                        LIMIT 500'''),
                {"cid": class_id, "cutoff": cutoff},
            )).fetchall()

        candidates = []
        for r in rows:
            content = (r[0] or "").strip()
            if not content or content.startswith("/") or len(content) < 4:
                continue
            candidates.append(content)

        if not candidates:
            await channel.send_text(
                sender_id,
                f"📭 *Corpus de Dúvidas — {c_name}*\n\nNenhuma mensagem de aluno encontrada nos últimos 30 dias.",
            )
            return

        await channel.send_text(sender_id, f"⏳ Analisando {len(candidates)} mensagens e agrupando por capítulo...")

        corpus_lines, total_chars = [], 0
        for m in candidates:
            if total_chars + len(m) > 12000:
                break
            corpus_lines.append(f"- {m}")
            total_chars += len(m)

        from src.llm.factory import get_provider
        from src.shared.constants.prompts import CORPUS_DUVIDAS_PROMPT

        llm = get_provider(settings.DEFAULT_LLM_PROVIDER)
        report = await llm.agenerate(prompt=CORPUS_DUVIDAS_PROMPT.format(messages="\n".join(corpus_lines)))

        await channel.send_text(
            sender_id,
            f"📚 *Corpus de Dúvidas — {c_name}* (últimos 30 dias)\n\n{report}"[:4000],
        )
    except Exception as e:
        logger.error("doubts_corpus_failed", error=str(e)[:300])
        await channel.send_text(sender_id, f"❌ Erro ao analisar corpus: {str(e)[:100]}")


# ==============================================================================
# ALUNO — /pausar_leitura, /retomar_leitura, /meu_progresso, /flashcards
# ==============================================================================

async def _resolve_student(session, channel, sender_id: str):
    """Resolve o aluno pelo identificador do canal (phone/telegram_chat_id)."""
    query_col = channel.get_user_query_column()
    res = await session.execute(
        text(f'SELECT id, name, phone FROM "Students" WHERE {query_col} = :sid AND is_active = true'),
        {"sid": sender_id},
    )
    return res.fetchone()


async def run_pause_reading_command(channel, sender_id: str):
    """Modo férias: pausa TODAS as jornadas do aluno, sem perder progresso."""
    try:
        async with AsyncSessionLocal() as session:
            student = await _resolve_student(session, channel, sender_id)
            if not student:
                return
            res = await session.execute(
                text('''UPDATE "ReadingPlans"
                        SET is_paused = true, next_checkin_at = NULL, updated_at = CURRENT_TIMESTAMP
                        WHERE student_id = :sid AND is_paused = false'''),
                {"sid": student[0]},
            )
            await session.commit()
            paused = res.rowcount or 0  # type: ignore[attr-defined]  # CursorResult tem rowcount

        await channel.send_text(sender_id, READING_PAUSED if paused else READING_NO_PLAN)
    except Exception as e:
        logger.error("pause_reading_failed", error=str(e)[:300])


async def run_resume_reading_command(channel, sender_id: str):
    """Retoma e RECALCULA o plano sem penalidades (replanejamento sem culpa)."""
    try:
        async with AsyncSessionLocal() as session:
            student = await _resolve_student(session, channel, sender_id)
            if not student:
                return
            plans = (await session.execute(
                text('''SELECT id, pace, preferred_time, current_section,
                               total_sections, streak_days, material_type
                        FROM "ReadingPlans"
                        WHERE student_id = :sid AND is_paused = true
                          AND current_section < total_sections'''),
                {"sid": student[0]},
            )).fetchall()

            if not plans:
                await channel.send_text(sender_id, READING_NO_PLAN)
                return

            for plan in plans:
                next_trigger = compute_reading_next_trigger(plan.pace, plan.preferred_time)
                await session.execute(
                    text('''UPDATE "ReadingPlans"
                            SET is_paused = false, next_trigger_at = :next,
                                last_suggestion_at = NULL, next_checkin_at = NULL,
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = :pid'''),
                    {"next": next_trigger, "pid": plan.id},
                )
            await session.commit()

        first = plans[0]
        await channel.send_text(sender_id, READING_RESUMED.format(
            progress_line=build_progress_line(
                first.current_section, first.total_sections, first.streak_days, first.material_type
            )
        ))
    except Exception as e:
        logger.error("resume_reading_failed", error=str(e)[:300])


async def run_my_progress_command(channel, sender_id: str):
    """Progresso visível: % do material, streak e status de cada jornada."""
    try:
        async with AsyncSessionLocal() as session:
            student = await _resolve_student(session, channel, sender_id)
            if not student:
                return
            plans = (await session.execute(
                text('''SELECT rp.material_type, rp.current_section, rp.total_sections,
                               rp.streak_days, rp.is_paused, rp.pace,
                               c.name AS course_name, sub.name AS subject_name
                        FROM "ReadingPlans" rp
                        JOIN "Courses" c ON c.id = rp.course_id
                        LEFT JOIN "Subjects" sub ON sub.id = rp.subject_id
                        WHERE rp.student_id = :sid
                        ORDER BY rp.created_at'''),
                {"sid": student[0]},
            )).fetchall()

        if not plans:
            await channel.send_text(sender_id, READING_NO_PLAN)
            return

        msg = "📊 *Seu progresso de leitura*\n"
        for p in plans:
            label = MATERIAL_LABELS.get(p.material_type, "livro").capitalize()
            scope = p.subject_name or p.course_name
            pct = progress_percent(p.current_section, p.total_sections)
            if p.current_section >= p.total_sections:
                status = "🏆 concluído"
            elif p.is_paused:
                status = "🏖️ pausado"
            else:
                status = "▶️ ativo"
            # H1.4: maior medalha conquistada pelo streak atual
            from src.workers.reading_journey import best_streak_badge
            badge = best_streak_badge(p.streak_days)
            msg += (
                f"\n📖 *{label} — {scope}* ({status})\n"
                f"{_progress_bar(pct)} *{pct}%* "
                f"({p.current_section}/{p.total_sections} seções) | 🔥 {p.streak_days} dia(s) seguidos"
                + (f" {badge}" if badge else "") + "\n"
            )
        msg += "\n_Comandos: /pausar_leitura · /retomar_leitura · /flashcards_"
        await channel.send_text(sender_id, msg[:4000])
    except Exception as e:
        logger.error("my_progress_failed", error=str(e)[:300])


async def run_flashcards_command(channel, sender_id: str):
    """Flashcards estilo Anki da última seção lida (Fase 6.2). Entram no
    QuizBank e, ao serem respondidos, caem na Revisão Espaçada (Fase 4) —
    flashcard sem repetição agendada é truque; com ela, é retenção."""
    try:
        async with AsyncSessionLocal() as session:
            student = await _resolve_student(session, channel, sender_id)
            if not student:
                return
            plan = (await session.execute(
                text('''SELECT rp.id, rp.student_id, rp.course_id, rp.subject_id,
                               rp.material_type, rp.current_section, rp.total_sections,
                               c.name AS course_name, sub.name AS subject_name
                        FROM "ReadingPlans" rp
                        JOIN "Courses" c ON c.id = rp.course_id
                        LEFT JOIN "Subjects" sub ON sub.id = rp.subject_id
                        WHERE rp.student_id = :sid
                        ORDER BY rp.is_paused ASC, rp.updated_at DESC
                        LIMIT 1'''),
                {"sid": student[0]},
            )).fetchone()

            if not plan:
                await channel.send_text(sender_id, READING_NO_PLAN)
                return

            klass = await _get_class_context(session, plan.student_id, plan.course_id, plan.subject_id)

        if not klass or not klass.teacher_id:
            await channel.send_text(sender_id, READING_NO_PLAN)
            return

        # Flashcard sobre a última seção LIDA (revisão do que já foi estudado).
        section = max(1, plan.current_section)
        section_label = section_label_for(plan.material_type, section)

        await channel.send_text(sender_id, f"🃏 Preparando seu flashcard do *{section_label}*...")

        # F2 — Vocabulário (curso de idioma): o flashcard vira um item de
        # vocabulário na língua-alvo (palavra + significado + áudio) que entra na
        # Repetição Espaçada ao ser respondido. Sem conteúdo de vocab → flashcard
        # normal da seção (fallback). Flags off ou turma não-idioma ⇒ intocado.
        from src.workers.vocabulary import vocab_enabled, deliver_vocab_flashcard
        if vocab_enabled() and getattr(klass, "is_language", False):
            sent = await deliver_vocab_flashcard(
                channel, sender_id,
                teacher_id=klass.teacher_id,
                student_id=plan.student_id,
                student_name=student[1],
                course_id=plan.course_id,
                course_name=plan.course_name,
                subject_name=plan.subject_name,
                subject_id=plan.subject_id,
                section=section,
                section_label=section_label,
                material_type=plan.material_type,
            )
            if sent:
                return

        from src.rag.review_generator import generate_review, FLASHCARD_PERSPECTIVES
        review = await generate_review(
            teacher_id=klass.teacher_id,
            teacher_name=klass.teacher_name or "Professor",
            teacher_subject=klass.teacher_subject or "Geral",
            student_id=plan.student_id,
            student_name=student[1],
            course_id=plan.course_id,
            course_name=plan.course_name,
            subject_name=plan.subject_name,
            current_lesson_number=section,
            lesson_label=section_label,
            subject_id=plan.subject_id,
            from_bank=True,
            material_type=plan.material_type,
            perspectives=FLASHCARD_PERSPECTIVES,
        )

        if not review:
            await channel.send_text(sender_id, "😕 Ainda não tenho conteúdo indexado dessa seção. Tente novamente após o professor sincronizar os materiais.")
            return

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
                "lesson_pointer": str(section),
            },
            quiz=quiz_output,
            quiz_bank_id=review.quiz_bank_id,
        )
    except Exception as e:
        logger.error("flashcards_failed", error=str(e)[:300])
        await channel.send_text(sender_id, "❌ Erro ao gerar flashcard. Tente novamente.")
