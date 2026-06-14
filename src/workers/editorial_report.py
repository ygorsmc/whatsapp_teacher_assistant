"""
Editorial Report — Relatório Editorial Mensal automático (H2.4).

Todo dia 1º (08h local), cada turma com Jornada de Leitura ativa gera um
relatório agregado para o professor/autor: mapa de dificuldade por capítulo +
funil de conclusão + ativações de chave do mês. É o entregável de VENDA da
vertical editoras — o autor descobre onde os leitores erram e abandonam, sem
nunca ver dados individuais (LGPD: coortes mínimas de 3).

O corpus de dúvidas (LLM, custo variável) fica fora do automático: o rodapé
lembra o comando /corpus_duvidas sob demanda.
"""

from datetime import datetime, timezone, timedelta

from sqlalchemy import text

from src.shared.config import settings
from src.shared.telemetry import logger, track_event
from src.db.database import AsyncSessionLocal

LOCAL_TZ_OFFSET = timedelta(hours=settings.TIMEZONE_OFFSET_HOURS)
REPORT_DAY = 1
REPORT_HOUR_LOCAL = 8
MIN_COHORT_SIZE = 3

_LAST_REPORT_MONTH: str | None = None


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _build_class_report(session, class_id: str, class_name: str,
                              course_id: str, subject_id: str | None) -> str | None:
    """Monta o relatório de UMA turma. None se não há dados suficientes."""
    cutoff_30d = _naive_utc_now() - timedelta(days=30)

    # Mapa de dificuldade (últimos 30 dias)
    difficulty = (await session.execute(
        text('''SELECT qb.material_type, qb.lesson_number,
                       COUNT(*) AS total,
                       SUM(CASE WHEN pq.is_correct THEN 0 ELSE 1 END) AS erros
                FROM "PendingQuizzes" pq
                JOIN "QuizBank" qb ON qb.id = pq.quiz_bank_id
                JOIN "StudentEnrollment" se ON se.student_id = pq.student_id
                WHERE se.class_id = :cid AND pq.status = 'answered'
                  AND pq.answered_at >= :cutoff
                GROUP BY qb.material_type, qb.lesson_number
                ORDER BY qb.material_type, qb.lesson_number'''),
        {"cid": class_id, "cutoff": cutoff_30d},
    )).fetchall()

    # Funil de leitura
    subject_clause = "rp.subject_id = :sub" if subject_id else "rp.subject_id IS NULL"
    plans = (await session.execute(
        text(f'''SELECT rp.current_section, rp.total_sections
                 FROM "ReadingPlans" rp
                 JOIN "StudentEnrollment" se ON se.student_id = rp.student_id
                 WHERE se.class_id = :clid AND rp.course_id = :cid AND {subject_clause}'''),
        {"clid": class_id, "cid": course_id,
         **({"sub": subject_id} if subject_id else {})},
    )).fetchall()

    if not plans and not difficulty:
        return None

    msg = f"📰 *Relatório Editorial Mensal — {class_name}*\n"

    if plans:
        total_readers = len(plans)
        max_sections = max(p[1] for p in plans)
        finished = sum(1 for p in plans if (p[0] or 0) >= p[1])
        avg_pct = round(100 * sum((p[0] or 0) / p[1] for p in plans if p[1]) / total_readers)
        msg += (
            f"\n📖 *Leitura:* {total_readers} leitor(es) | progresso médio *{avg_pct}%* | "
            f"concluíram: *{finished}*\n"
        )
        # Ponto de maior abandono
        prev, worst_drop, worst_chapter = total_readers, 0, 0
        for k in range(1, max_sections + 1):
            reached = sum(1 for p in plans if (p[0] or 0) >= k)
            if prev - reached > worst_drop:
                worst_drop, worst_chapter = prev - reached, k
            prev = reached
        if worst_drop > 0:
            msg += f"⚠️ Maior abandono: *Capítulo {worst_chapter}* (-{worst_drop} leitor(es))\n"

    if difficulty:
        msg += "\n🗺️ *Dificuldade (30 dias):*\n"
        suppressed = 0
        for r in difficulty[:12]:
            mat, section, total, erros = r[0], r[1], int(r[2] or 0), int(r[3] or 0)
            if total < MIN_COHORT_SIZE:
                suppressed += 1
                continue
            error_pct = round(100 * erros / total)
            emoji = "🟩" if error_pct < 25 else "🟨" if error_pct < 50 else "🟧" if error_pct < 75 else "🟥"
            word = "Cap." if mat == "book" else ("Mód." if mat == "workbook" else "Aula")
            msg += f"{emoji} {word} {section}: {error_pct}% de erro ({total} resp.)\n"
        if suppressed:
            msg += f"_({suppressed} seção(ões) com menos de {MIN_COHORT_SIZE} respostas omitidas — LGPD)_\n"

    # Ativações de chave no mês (vertical editoras)
    activations = (await session.execute(
        text('''SELECT COUNT(*) FROM "KeyActivations" ka
                JOIN "ActivationKeys" ak ON ak.key = ka.key
                WHERE ak.class_id = :cid AND ka.activated_at >= :cutoff'''),
        {"cid": class_id, "cutoff": cutoff_30d},
    )).scalar() or 0
    if activations:
        msg += f"\n🔑 Novas ativações de chave (30 dias): *{activations}*\n"

    msg += "\n💬 _Dúvidas agrupadas por capítulo sob demanda: /corpus_duvidas_" + class_name.replace(" ", "") + "_"
    return msg


async def process_monthly_editorial_report():
    """Dia 1º, 08h local, 1x por mês: envia o relatório de cada turma com
    Jornada de Leitura ao professor/autor dela (via fila outbound)."""
    global _LAST_REPORT_MONTH
    now_local = _naive_utc_now() + LOCAL_TZ_OFFSET
    month_tag = now_local.strftime("%Y-%m")
    if now_local.day != REPORT_DAY or now_local.hour != REPORT_HOUR_LOCAL or _LAST_REPORT_MONTH == month_tag:
        return
    _LAST_REPORT_MONTH = month_tag

    default_ct = settings.CHANNEL_BACKEND.lower()
    async with AsyncSessionLocal() as session:
        classes = (await session.execute(
            text('''SELECT DISTINCT cl.id, cl.name, cl.course_id, cl.subject_id,
                           t.phone, t.telegram_chat_id
                    FROM "Classes" cl
                    JOIN "StudentEnrollment" se ON se.class_id = cl.id
                    JOIN "ReadingPlans" rp ON rp.student_id = se.student_id
                                          AND rp.course_id = cl.course_id
                    LEFT JOIN "Teachers" t ON t.id = cl.teacher_id'''),
        )).fetchall()

        if not classes:
            return

        from src.queue.factory import get_queue
        queue = get_queue()
        sent = 0
        for cl in classes:
            try:
                report = await _build_class_report(session, cl.id, cl.name, cl.course_id, cl.subject_id)
                if not report:
                    continue
                ct = "telegram" if (default_ct == "telegram" and cl.telegram_chat_id) else "whatsapp"
                addr = cl.telegram_chat_id if ct == "telegram" else cl.phone
                if not addr:
                    continue
                await queue.enqueue("outbound_messages", {
                    "recipient_id": addr, "channel": ct, "text": report[:4000],
                    "origin": "editorial_report",
                })
                sent += 1
            except Exception as e:
                logger.error("editorial_report_failed", class_id=cl.id, error=str(e)[:300])

    track_event("editorial_report_sent", {"month": month_tag, "classes": sent})
    logger.info("editorial_report_sent", month=month_tag, classes=sent)
