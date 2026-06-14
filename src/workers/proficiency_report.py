"""
Relatório de Proficiência — "gamificação invisível" (F6 — Vertical de Idiomas).

Todo dia 1º (08h local), cada TURMA DE IDIOMA gera:
  - ao ALUNO: um inventário executivo do mês (evolução da pronúncia + tópicos a
    reforçar) — só dentro da janela de 24h no WhatsApp (Telegram sempre), nunca
    via template proativo (FinOps/Quality Rating);
  - ao PROFESSOR/ESCOLA: o agregado da turma (coorte >= MIN_COHORT_SIZE, LGPD).

Fontes ($0, agregação on-the-fly): `PronunciationAttempts` (F3 — só scores) +
erros de quiz (`PendingQuizzes` × `QuizBank`). É o moat do público B2B/executivo.

Atrás de `PROFICIENCY_REPORT_ENABLED` + `LANGUAGE_MODE_ENABLED`. Estende a mesma
disciplina do `editorial_report.py` (mensal, coorte, fila outbound).
"""

from datetime import datetime, timezone, timedelta

from sqlalchemy import text

from src.shared.config import settings
from src.shared.telemetry import logger, track_event
from src.shared.privacy import messaging_optout_clause
from src.db.database import AsyncSessionLocal

LOCAL_TZ_OFFSET = timedelta(hours=settings.TIMEZONE_OFFSET_HOURS)
REPORT_DAY = 1
REPORT_HOUR_LOCAL = 8
MIN_COHORT_SIZE = 3

_LAST_REPORT_MONTH: str | None = None


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def pronunciation_trend(first_accuracy: float | None, last_accuracy: float | None, threshold: float = 5.0) -> str:
    """Compara a 1ª e a última precisão do mês → rótulo de evolução. Puro/testável.
    Sem dados suficientes → estável."""
    if first_accuracy is None or last_accuracy is None:
        return "➡️ estável"
    delta = last_accuracy - first_accuracy
    if delta > threshold:
        return "📈 evoluindo"
    if delta < -threshold:
        return "📉 atenção"
    return "➡️ estável"


def enough_cohort(n: int) -> bool:
    """Coorte mínima p/ agregado da turma (LGPD/significância). Puro/testável."""
    return n >= MIN_COHORT_SIZE


async def _weak_topics(session, student_id: str, course_id: str, cutoff, limit: int = 4) -> list[str]:
    """Tópicos de quiz com mais erros do aluno no período (proxy de 'erros
    gramaticais recorrentes')."""
    rows = (await session.execute(
        text('''SELECT qb.topic,
                       SUM(CASE WHEN pq.is_correct THEN 0 ELSE 1 END) AS erros
                FROM "PendingQuizzes" pq
                JOIN "QuizBank" qb ON qb.id = pq.quiz_bank_id
                WHERE pq.student_id = :sid AND qb.course_id = :cid
                  AND pq.status = 'answered' AND pq.answered_at >= :cutoff
                GROUP BY qb.topic
                HAVING SUM(CASE WHEN pq.is_correct THEN 0 ELSE 1 END) > 0
                ORDER BY erros DESC
                LIMIT :lim'''),
        {"sid": student_id, "cid": course_id, "cutoff": cutoff, "lim": limit},
    )).fetchall()
    return [r[0] for r in rows if r[0]]


async def _build_student_inventory(session, student_id: str, course_id: str, cutoff) -> str | None:
    """Inventário executivo do aluno (dados PRÓPRIOS — sem restrição de coorte).
    None se não houver nenhum sinal no período."""
    pron = (await session.execute(
        text('''SELECT COUNT(*) AS n, AVG(accuracy) AS acc, AVG(fluency) AS flu
                FROM "PronunciationAttempts"
                WHERE student_id = :sid AND course_id = :cid AND created_at >= :cutoff'''),
        {"sid": student_id, "cid": course_id, "cutoff": cutoff},
    )).fetchone()
    n_attempts = int(pron.n or 0) if pron else 0

    weak = await _weak_topics(session, student_id, course_id, cutoff)

    if n_attempts == 0 and not weak:
        return None

    msg = "📊 *Seu mês de idioma*\n"
    if n_attempts > 0:
        # Evolução: 1ª vs última precisão do período.
        bounds = (await session.execute(
            text('''SELECT accuracy, created_at FROM "PronunciationAttempts"
                    WHERE student_id = :sid AND course_id = :cid AND created_at >= :cutoff
                      AND accuracy IS NOT NULL
                    ORDER BY created_at'''),
            {"sid": student_id, "cid": course_id, "cutoff": cutoff},
        )).fetchall()
        first_acc = bounds[0][0] if bounds else None
        last_acc = bounds[-1][0] if bounds else None
        trend = pronunciation_trend(first_acc, last_acc)
        avg_acc = round(pron.acc) if pron.acc is not None else 0
        msg += f"🎙️ Pronúncia: *{n_attempts}* áudio(s) | precisão média *{avg_acc}%* {trend}\n"

    if weak:
        msg += "📚 Tópicos a reforçar: " + ", ".join(f"*{t}*" for t in weak) + "\n"

    msg += "\n💪 Continue praticando — mande áudios e responda os quizzes para o próximo relatório!"
    return msg


async def _build_class_aggregate(session, class_id: str, course_id: str, class_name: str, cutoff) -> str | None:
    """Agregado da turma p/ a escola/professor. Coorte >= MIN_COHORT_SIZE (LGPD).
    None se a turma não atinge a coorte ou não tem dados."""
    agg = (await session.execute(
        text('''SELECT COUNT(DISTINCT pa.student_id) AS alunos, COUNT(*) AS tentativas,
                       AVG(pa.accuracy) AS acc, AVG(pa.fluency) AS flu
                FROM "PronunciationAttempts" pa
                JOIN "StudentEnrollment" se ON se.student_id = pa.student_id
                WHERE se.class_id = :clid AND pa.course_id = :cid AND pa.created_at >= :cutoff'''),
        {"clid": class_id, "cid": course_id, "cutoff": cutoff},
    )).fetchone()
    n_students = int(agg.alunos or 0) if agg else 0

    if not enough_cohort(n_students):
        return None  # coorte insuficiente → não há relatório agregado (LGPD)

    avg_acc = round(agg.acc) if agg.acc is not None else 0
    avg_flu = round(agg.flu) if agg.flu is not None else 0
    msg = (
        f"📊 *Proficiência da turma — {class_name}* (30 dias)\n"
        f"🎙️ {n_students} aluno(s) avaliados | {int(agg.tentativas or 0)} áudio(s)\n"
        f"🗣️ Precisão média da turma: *{avg_acc}%* | fluência: *{avg_flu}%*\n"
    )

    # Tópicos mais difíceis da turma (erros de quiz), com coorte por tópico.
    hard = (await session.execute(
        text('''SELECT qb.topic, COUNT(*) AS total,
                       SUM(CASE WHEN pq.is_correct THEN 0 ELSE 1 END) AS erros
                FROM "PendingQuizzes" pq
                JOIN "QuizBank" qb ON qb.id = pq.quiz_bank_id
                JOIN "StudentEnrollment" se ON se.student_id = pq.student_id
                WHERE se.class_id = :clid AND qb.course_id = :cid
                  AND pq.status = 'answered' AND pq.answered_at >= :cutoff
                GROUP BY qb.topic
                HAVING COUNT(*) >= :cohort
                ORDER BY (SUM(CASE WHEN pq.is_correct THEN 0 ELSE 1 END) * 1.0 / COUNT(*)) DESC
                LIMIT 5'''),
        {"clid": class_id, "cid": course_id, "cutoff": cutoff, "cohort": MIN_COHORT_SIZE},
    )).fetchall()
    if hard:
        msg += "\n🗺️ *Tópicos mais difíceis:*\n"
        for r in hard:
            topic, total, erros = r[0], int(r[1] or 0), int(r[2] or 0)
            msg += f"🟥 {topic}: {round(100 * erros / total)}% de erro ({total} resp.)\n"

    return msg


async def process_monthly_proficiency_report():
    """Dia 1º, 08h local, 1x/mês: para cada TURMA DE IDIOMA, envia o agregado ao
    professor e o inventário executivo a cada aluno ATIVO (janela 24h no WhatsApp).
    """
    global _LAST_REPORT_MONTH
    if not (settings.LANGUAGE_MODE_ENABLED and settings.PROFICIENCY_REPORT_ENABLED):
        return

    now_local = _naive_utc_now() + LOCAL_TZ_OFFSET
    month_tag = now_local.strftime("%Y-%m")
    if now_local.day != REPORT_DAY or now_local.hour != REPORT_HOUR_LOCAL or _LAST_REPORT_MONTH == month_tag:
        return
    _LAST_REPORT_MONTH = month_tag

    cutoff_30d = _naive_utc_now() - timedelta(days=30)
    default_ct = settings.CHANNEL_BACKEND.lower()
    twenty_four_hours_ago = _naive_utc_now() - timedelta(hours=24)

    from src.queue.factory import get_queue
    queue = get_queue()
    students_sent = 0
    classes_sent = 0

    async with AsyncSessionLocal() as session:
        classes = (await session.execute(
            text('''SELECT cl.id, cl.name, cl.course_id, t.phone, t.telegram_chat_id
                    FROM "Classes" cl
                    LEFT JOIN "Teachers" t ON t.id = cl.teacher_id
                    WHERE cl.is_language = true'''),
        )).fetchall()

        for cl in classes:
            # 1. Agregado da escola/professor (coorte >= 3).
            try:
                agg = await _build_class_aggregate(session, cl.id, cl.course_id, cl.name, cutoff_30d)
                if agg:
                    ct = "telegram" if (default_ct == "telegram" and cl.telegram_chat_id) else "whatsapp"
                    addr = cl.telegram_chat_id if ct == "telegram" else cl.phone
                    if addr:
                        await queue.enqueue("outbound_messages", {
                            "recipient_id": addr, "channel": ct, "text": agg[:4000],
                            "origin": "proficiency_report",
                        })
                        classes_sent += 1
            except Exception as e:
                logger.error("proficiency_class_report_failed", class_id=cl.id, error=str(e)[:300])

            # 2. Inventário executivo por aluno ATIVO (FinOps: só na janela 24h /
            #    Telegram — nunca template proativo de Marketing).
            students = (await session.execute(
                text('''SELECT s.id, s.phone, s.telegram_chat_id, s.preferred_channel,
                               (SELECT MAX(si.timestamp) FROM "StudentInteractions" si
                                WHERE si.student_id = s.id
                                  AND si.interaction_type IN ('message_received', 'quiz_answered')
                               ) AS last_interaction
                        FROM "Students" s
                        JOIN "StudentEnrollment" se ON se.student_id = s.id
                        WHERE se.class_id = :clid AND s.is_active = true'''
                        + messaging_optout_clause("s")),  # F9 — respeita opt-out
                {"clid": cl.id},
            )).fetchall()

            for st in students:
                try:
                    sct = (st.preferred_channel or default_ct).lower()
                    addr = st.telegram_chat_id if sct == "telegram" else st.phone
                    if not addr:
                        continue
                    if sct == "whatsapp":
                        last_int = st.last_interaction
                        if isinstance(last_int, str):
                            last_int = datetime.fromisoformat(last_int)
                        if last_int is not None and last_int.tzinfo is not None:
                            last_int = last_int.replace(tzinfo=None)
                        if not (last_int and last_int > twenty_four_hours_ago):
                            continue  # fora da janela → não dispara (sem template)
                    inventory = await _build_student_inventory(session, st.id, cl.course_id, cutoff_30d)
                    if not inventory:
                        continue
                    await queue.enqueue("outbound_messages", {
                        "recipient_id": addr, "channel": sct, "text": inventory[:4000],
                        "origin": "proficiency_report_student",
                    })
                    students_sent += 1
                except Exception as e:
                    logger.error("proficiency_student_report_failed", student_id=st.id, error=str(e)[:300])

    track_event("proficiency_report_sent", {"month": month_tag, "classes": classes_sent, "students": students_sent})
    logger.info("proficiency_report_sent", month=month_tag, classes=classes_sent, students=students_sent)
