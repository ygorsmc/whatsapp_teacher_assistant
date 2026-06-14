"""
Class Challenges — Desafio de Turma (H1.1) e fechamento do Simulado
Cronometrado (H2.3).

Accountability social SEM grupo aberto: a turma inteira recebe a MESMA questão
(comparável); quando a janela fecha, cada participante recebe um placar
ANÔNIMO ("73% da turma acertou — você foi um deles 🏅") e o professor recebe o
resumo. Ninguém é exposto individualmente para a turma (LGPD: coorte mínima).

Fluxo:
  /desafio_[turma]  → run_class_challenge_command (1 questão, janela de 6h)
  /simulado_...     → reading_commands cria o challenge kind='simulado' (24h)
  scheduler         → process_challenge_closures() agrega e comunica
"""

from datetime import datetime, timezone, timedelta
from uuid import uuid4

from sqlalchemy import text

from src.shared.config import settings
from src.shared.telemetry import logger, track_event
from src.db.database import AsyncSessionLocal
from src.shared.constants.messages import (
    CMD_TEACHER_NOT_FOUND,
    CMD_CLASS_NOT_FOUND,
    CHALLENGE_STUDENT_INTRO,
    CHALLENGE_RESULT_WINNER,
    CHALLENGE_RESULT_MISSED,
    CHALLENGE_RESULT_SMALL_COHORT,
    SIMULADO_RESULT_PERCENTILE,
    SIMULADO_RESULT_SMALL_COHORT,
)

# LGPD/significância: abaixo disso não circula placar coletivo.
MIN_COHORT_SIZE = 3


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ==============================================================================
# HELPERS PUROS (testáveis sem I/O)
# ==============================================================================

def percent_correct(correct: int, total: int) -> int:
    if total <= 0:
        return 0
    return round(100 * correct / total)


def percentile_below(my_score: int, all_scores: list[int]) -> int:
    """Percentual de participantes com pontuação ESTRITAMENTE menor que a minha
    (percentil clássico de ranking, anônimo). all_scores inclui o meu."""
    others = [s for s in all_scores]
    if len(others) <= 1:
        return 0
    below = sum(1 for s in others if s < my_score)
    return round(100 * below / (len(others) - 1)) if len(others) > 1 else 0


def build_challenge_result(is_correct: bool, correct: int, total: int) -> str:
    """Placar anônimo do desafio. Coorte < MIN_COHORT_SIZE → só feedback
    individual (nunca expõe um grupo pequeno demais)."""
    if total < MIN_COHORT_SIZE:
        return CHALLENGE_RESULT_SMALL_COHORT
    pct = percent_correct(correct, total)
    if is_correct:
        return CHALLENGE_RESULT_WINNER.format(percent=pct)
    return CHALLENGE_RESULT_MISSED.format(percent=pct)


def build_simulado_result(my_correct: int, question_count: int, all_scores: list[int]) -> str:
    """Resultado individual do simulado com percentil anônimo."""
    if len(all_scores) < MIN_COHORT_SIZE:
        return SIMULADO_RESULT_SMALL_COHORT.format(correct=my_correct, total=question_count)
    pct = percentile_below(my_correct, all_scores)
    return SIMULADO_RESULT_PERCENTILE.format(correct=my_correct, total=question_count, percentile=pct)


# ==============================================================================
# COMANDO DO PROFESSOR — /desafio_[turma]
# ==============================================================================

async def run_class_challenge_command(channel, sender_id: str, class_query: str):
    """Envia UMA questão (a mesma para todos — placar comparável) aos alunos na
    janela de 24h e agenda o fechamento (CHALLENGE_WINDOW_HOURS)."""
    try:
        from src.workers.reading_commands import _resolve_teacher_and_class

        async with AsyncSessionLocal() as session:
            teacher, klass = await _resolve_teacher_and_class(session, channel, sender_id, class_query)
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return
            if not klass:
                await channel.send_text(sender_id, CMD_CLASS_NOT_FOUND.format(class_query=class_query))
                return

            class_id, c_name, course_id, subject_id, current_lesson, subject_name = (
                klass[0], klass[1], klass[2], klass[3], klass[4], klass[5]
            )

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

        if not students:
            await channel.send_text(sender_id, f"❌ Nenhum aluno ativo encontrado na turma '{c_name}'.")
            return

        await channel.send_text(sender_id, f"⏳ Preparando o desafio da turma {c_name} (aula {current_lesson})...")

        # Questão ÚNICA para todos: pega a mais recente do banco da aula atual;
        # se o banco estiver vazio, gera uma nova (e ela entra no banco).
        from src.rag import quiz_bank
        pool = await quiz_bank.list_simulado_questions(
            course_id=course_id, subject_id=subject_id, max_section=current_lesson, limit=1
        )
        bank_row = pool[0] if pool else None

        if not bank_row:
            from src.rag.review_generator import generate_review
            from src.rag.prompt_builder import resolve_lesson_label
            lesson_label = await resolve_lesson_label(course_id, str(current_lesson))
            review = await generate_review(
                teacher_id=teacher[0],
                teacher_name="Professor", teacher_subject="Geral",
                student_id="broadcast", student_name="Turma",
                course_id=course_id, course_name=c_name,
                subject_name=subject_name,
                current_lesson_number=current_lesson,
                lesson_label=lesson_label,
                subject_id=subject_id,
                from_bank=False,
            )
            if not review or not review.quiz_bank_id:
                await channel.send_text(sender_id, f"❌ Sem material indexado para a aula {current_lesson}.")
                return
            bank_row = await quiz_bank.get_quiz_package(review.quiz_bank_id)
            if not bank_row:
                await channel.send_text(sender_id, "❌ Erro ao preparar a questão do desafio.")
                return

        now = _naive_utc_now()
        closes_at = now + timedelta(hours=settings.CHALLENGE_WINDOW_HOURS)
        challenge_id = str(uuid4())

        async with AsyncSessionLocal() as session:
            await session.execute(
                text('''INSERT INTO "ClassChallenges"
                            (id, class_id, quiz_bank_id, kind, question_count, sent_at, closes_at, status)
                        VALUES (:id, :cid, :qbid, 'challenge', 1, :sent, :closes, 'open')'''),
                {"id": challenge_id, "cid": class_id, "qbid": bank_row["id"], "sent": now, "closes": closes_at},
            )
            await session.commit()

        # Entrega (janela de 24h do WhatsApp; Telegram sempre)
        from src.channel.factory import get_channel
        from src.workers.micro_learning import send_quiz_to_student
        from src.workers.spaced_repetition import _shuffled_quiz_from_bank

        default_ct = settings.CHANNEL_BACKEND.lower()
        cutoff_24h = now - timedelta(hours=24)
        delivered, skipped = 0, 0

        for st in students:
            ct = (st[4] or default_ct).lower()
            addr = st[3] if ct == "telegram" else st[1]
            if not addr:
                skipped += 1
                continue
            if ct == "whatsapp":
                last_int = st[5]
                if isinstance(last_int, str):
                    last_int = datetime.fromisoformat(last_int)
                if last_int is not None and last_int.tzinfo is not None:
                    last_int = last_int.replace(tzinfo=None)
                if not (last_int and last_int > cutoff_24h):
                    skipped += 1
                    continue
            try:
                c = get_channel(ct)
                await c.send_text(addr, CHALLENGE_STUDENT_INTRO.format(
                    class_name=c_name, hours=settings.CHALLENGE_WINDOW_HOURS
                ))
                await send_quiz_to_student(
                    channel=c,
                    student={"id": st[0], "sender_id": addr, "course_id": course_id,
                             "lesson_pointer": str(bank_row["lesson_number"])},
                    quiz=_shuffled_quiz_from_bank(bank_row),
                    quiz_bank_id=bank_row["id"],
                    challenge_id=challenge_id,
                )
                delivered += 1
            except Exception as e:
                logger.error("challenge_delivery_failed", student_id=st[0], error=str(e)[:200])

        msg = (
            f"🏆 *Desafio lançado — {c_name}!*\n\n"
            f"📨 Entregue a *{delivered}* aluno(s)"
            + (f" ({skipped} fora da janela/sem vínculo)" if skipped else "")
            + f".\n⏰ Fecha em *{settings.CHALLENGE_WINDOW_HOURS}h* — aí todo mundo recebe o placar anônimo da turma."
        )
        await channel.send_text(sender_id, msg)
        track_event("challenge_created", {"challenge_id": challenge_id, "class_id": class_id, "delivered": delivered})
    except Exception as e:
        logger.error("challenge_command_failed", error=str(e)[:300])
        await channel.send_text(sender_id, f"❌ Erro ao lançar desafio: {str(e)[:100]}")


# ==============================================================================
# SCHEDULER — FECHAMENTO (desafio E simulado)
# ==============================================================================

async def process_challenge_closures():
    """Fecha challenges vencidos: agrega respostas, envia o placar anônimo a
    cada participante e o resumo ao professor da turma."""
    now = _naive_utc_now()
    default_ct = settings.CHANNEL_BACKEND.lower()

    async with AsyncSessionLocal() as session:
        challenges = (await session.execute(
            text('''SELECT ch.id, ch.class_id, ch.kind, ch.question_count,
                           cl.name AS class_name, cl.teacher_id,
                           t.phone AS teacher_phone, t.telegram_chat_id AS teacher_tg,
                           qb.topic
                    FROM "ClassChallenges" ch
                    JOIN "Classes" cl ON cl.id = ch.class_id
                    LEFT JOIN "Teachers" t ON t.id = cl.teacher_id
                    LEFT JOIN "QuizBank" qb ON qb.id = ch.quiz_bank_id
                    WHERE ch.status = 'open' AND ch.closes_at <= :now
                    FOR UPDATE OF ch SKIP LOCKED'''),
            {"now": now},
        )).fetchall()

        if not challenges:
            return

        logger.info("challenges_closing", count=len(challenges))

        for ch in challenges:
            try:
                # Respostas por aluno (answered): score = nº de acertos no challenge
                rows = (await session.execute(
                    text('''SELECT pq.student_id,
                                   SUM(CASE WHEN pq.is_correct THEN 1 ELSE 0 END) AS corrects,
                                   s.phone, s.telegram_chat_id, s.preferred_channel
                            FROM "PendingQuizzes" pq
                            JOIN "Students" s ON s.id = pq.student_id
                            WHERE pq.challenge_id = :chid AND pq.status = 'answered'
                            GROUP BY pq.student_id, s.phone, s.telegram_chat_id, s.preferred_channel'''),
                    {"chid": ch.id},
                )).fetchall()

                participants = len(rows)
                all_scores = [int(r.corrects or 0) for r in rows]
                total_correct = sum(all_scores)

                await session.execute(
                    text('''UPDATE "ClassChallenges"
                            SET status = 'closed', participants_count = :p,
                                correct_count = :c, updated_at = CURRENT_TIMESTAMP
                            WHERE id = :chid'''),
                    {"p": participants, "c": total_correct, "chid": ch.id},
                )
                await session.commit()

                # Placar a cada participante (responder = estar na janela de 24h)
                from src.channel.factory import get_channel
                for r in rows:
                    ct = (r.preferred_channel or default_ct).lower()
                    addr = r.telegram_chat_id if ct == "telegram" else r.phone
                    if not addr:
                        continue
                    my_correct = int(r.corrects or 0)
                    if ch.kind == "simulado":
                        msg = build_simulado_result(my_correct, ch.question_count, all_scores)
                    else:
                        msg = build_challenge_result(my_correct >= 1, total_correct, participants)
                    try:
                        await get_channel(ct).send_text(addr, msg)
                    except Exception as e:
                        logger.error("challenge_result_send_failed", student_id=r.student_id, error=str(e)[:200])

                # Resumo ao professor (via fila outbound)
                if ch.teacher_id and (ch.teacher_phone or ch.teacher_tg):
                    kind_label = "Simulado" if ch.kind == "simulado" else "Desafio"
                    pct = percent_correct(total_correct, participants * ch.question_count)
                    summary = (
                        f"🏁 *{kind_label} encerrado — {ch.class_name}*\n\n"
                        f"👥 Participaram: *{participants}* aluno(s)\n"
                        f"✅ Aproveitamento da turma: *{pct}%*"
                    )
                    if ch.topic:
                        summary += f"\n📍 Tópico: {ch.topic}"
                    if participants == 0:
                        summary += "\n\n_Ninguém respondeu — vale reengajar a turma (ex.: /aviso)._"
                    ct = "telegram" if (default_ct == "telegram" and ch.teacher_tg) else "whatsapp"
                    addr = ch.teacher_tg if ct == "telegram" else ch.teacher_phone
                    if addr:
                        from src.queue.factory import get_queue
                        await get_queue().enqueue("outbound_messages", {
                            "recipient_id": addr, "channel": ct, "text": summary,
                            "origin": "challenge_summary",
                        })

                track_event("challenge_closed", {
                    "challenge_id": ch.id, "kind": ch.kind,
                    "participants": participants, "correct": total_correct,
                })
            except Exception as e:
                logger.error("challenge_closure_failed", challenge_id=ch.id, error=str(e)[:400])
