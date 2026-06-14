"""
Sentinela Emocional (H1.2) — o bot como sensor, o professor como humano certo.

O produto real do mercado premium (relatório de mercado) é alguém que PERCEBE
o aluno derrapando antes do abandono. Regras determinísticas (custo $0, sem
LLM) detectam três sinais e entregam ao professor UM digest diário com ação
sugerida:

  - consecutive_errors: 3+ erros seguidos nos últimos quizzes do aluno;
  - streak_broken:      streak de leitura ≥ 7 dias que quebrou (48h+ sem ler);
  - gone_quiet:         aluno antes ativo (4+ interações/semana) sumido há 5+ dias.

Anti-spam: SentinelAlerts deduplica (aluno, regra) por 7 dias e o digest roda
no máximo 1x/dia (janela das 12h locais), agrupado por professor.
"""

from datetime import datetime, timezone, timedelta
from uuid import uuid4

from sqlalchemy import text

from src.shared.config import settings
from src.shared.telemetry import logger, track_event
from src.db.database import AsyncSessionLocal
from src.shared.constants.messages import SENTINEL_DIGEST_HEADER, SENTINEL_FOOTER

LOCAL_TZ_OFFSET = timedelta(hours=settings.TIMEZONE_OFFSET_HOURS)

DEDUPE_DAYS = 7
CONSECUTIVE_ERRORS_MIN = 3
GONE_QUIET_DAYS = 5
PREVIOUSLY_ACTIVE_MIN_INTERACTIONS = 4

# Janela diária do digest (hora local). Roda 1x por dia.
DIGEST_HOUR_LOCAL = 12
_LAST_DIGEST_DATE = None


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_naive(dt):
    if dt is None:
        return None
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt


# ==============================================================================
# REGRAS PURAS (testáveis sem I/O)
# ==============================================================================

def rule_consecutive_errors(last_results: list[bool], minimum: int = CONSECUTIVE_ERRORS_MIN) -> bool:
    """True se as últimas respostas (mais recente primeiro) formam uma
    sequência de erros >= minimum."""
    if len(last_results) < minimum:
        return False
    return not any(last_results[:minimum])


def rule_streak_broken(streak_days: int, last_read_at, now_utc: datetime) -> bool:
    """Streak relevante (>=7) parado há mais de 48h = quebra recente."""
    if streak_days < 7 or last_read_at is None:
        return False
    return (now_utc - last_read_at) > timedelta(hours=48)


def rule_gone_quiet(last_interaction, prior_week_interactions: int, now_utc: datetime) -> bool:
    """Aluno que ERA ativo e silenciou: sem interagir há GONE_QUIET_DAYS+,
    mas com atividade relevante na janela anterior (não é abandono antigo)."""
    if last_interaction is None:
        return False
    idle = now_utc - last_interaction
    return (
        timedelta(days=GONE_QUIET_DAYS) <= idle <= timedelta(days=14)
        and prior_week_interactions >= PREVIOUSLY_ACTIVE_MIN_INTERACTIONS
    )


# ==============================================================================
# COLETA + DIGEST
# ==============================================================================

async def _collect_alerts(session, now_utc: datetime) -> list[dict]:
    """Avalia as 3 regras para os alunos ativos e devolve os alertas inéditos
    (respeitando o dedupe de 7 dias). Cada alerta carrega o professor dono."""
    dedupe_cutoff = now_utc - timedelta(days=DEDUPE_DAYS)
    alerts: list[dict] = []

    async def _already_alerted(student_id: str, rule: str) -> bool:
        res = await session.execute(
            text('''SELECT 1 FROM "SentinelAlerts"
                    WHERE student_id = :sid AND rule = :rule AND notified_at > :cutoff
                    LIMIT 1'''),
            {"sid": student_id, "rule": rule, "cutoff": dedupe_cutoff},
        )
        return res.scalar() is not None

    # ── Regra 1: erros consecutivos ─────────────────────────────────────────
    rows = (await session.execute(
        text('''SELECT pq.student_id, s.name, cl.id AS class_id, cl.name AS class_name,
                       cl.teacher_id,
                       array_agg(pq.is_correct ORDER BY pq.answered_at DESC) AS results
                FROM "PendingQuizzes" pq
                JOIN "Students" s ON s.id = pq.student_id AND s.is_active = true
                JOIN "StudentEnrollment" se ON se.student_id = pq.student_id
                JOIN "Classes" cl ON cl.id = se.class_id AND cl.course_id = pq.course_id
                WHERE pq.status = 'answered'
                  AND pq.answered_at > :recent
                GROUP BY pq.student_id, s.name, cl.id, cl.name, cl.teacher_id'''),
        {"recent": now_utc - timedelta(days=14)},
    )).fetchall()
    for r in rows:
        results = [bool(x) for x in (r.results or []) if x is not None]
        if rule_consecutive_errors(results) and not await _already_alerted(r.student_id, "consecutive_errors"):
            errors = 0
            for ok in results:
                if ok:
                    break
                errors += 1
            alerts.append({
                "student_id": r.student_id, "class_id": r.class_id,
                "teacher_id": r.teacher_id, "rule": "consecutive_errors",
                "details": f"📉 *{r.name}* ({r.class_name}) errou os últimos {errors} quizzes seguidos. "
                           f"Sugestão: uma mensagem de incentivo + revisar o tópico com a turma.",
            })

    # ── Regra 2: streak de leitura quebrado ─────────────────────────────────
    rows = (await session.execute(
        text('''SELECT rp.student_id, rp.streak_days, rp.last_read_at,
                       s.name, cl.id AS class_id, cl.name AS class_name, cl.teacher_id
                FROM "ReadingPlans" rp
                JOIN "Students" s ON s.id = rp.student_id AND s.is_active = true
                JOIN "StudentEnrollment" se ON se.student_id = rp.student_id
                JOIN "Classes" cl ON cl.id = se.class_id AND cl.course_id = rp.course_id
                WHERE rp.is_paused = false AND rp.streak_days >= 7'''),
    )).fetchall()
    for r in rows:
        if rule_streak_broken(r.streak_days, _as_naive(r.last_read_at), now_utc) \
                and not await _already_alerted(r.student_id, "streak_broken"):
            alerts.append({
                "student_id": r.student_id, "class_id": r.class_id,
                "teacher_id": r.teacher_id, "rule": "streak_broken",
                "details": f"🔥➡️💨 *{r.name}* ({r.class_name}) tinha {r.streak_days} dias seguidos de leitura "
                           f"e parou. Sugestão: um \"sentimos sua falta\" pessoal resgata o hábito.",
            })

    # ── Regra 3: aluno ativo que silenciou ──────────────────────────────────
    rows = (await session.execute(
        text('''SELECT s.id AS student_id, s.name, cl.id AS class_id, cl.name AS class_name,
                       cl.teacher_id,
                       (SELECT MAX(si.timestamp) FROM "StudentInteractions" si
                        WHERE si.student_id = s.id) AS last_interaction,
                       (SELECT COUNT(*) FROM "StudentInteractions" si
                        WHERE si.student_id = s.id
                          AND si.timestamp BETWEEN :w_start AND :w_end) AS prior_week
                FROM "Students" s
                JOIN "StudentEnrollment" se ON se.student_id = s.id
                JOIN "Classes" cl ON cl.id = se.class_id
                WHERE s.is_active = true'''),
        {"w_start": now_utc - timedelta(days=14), "w_end": now_utc - timedelta(days=GONE_QUIET_DAYS)},
    )).fetchall()
    seen_quiet: set[str] = set()
    for r in rows:
        if r.student_id in seen_quiet:
            continue  # aluno em várias turmas: 1 alerta basta
        if rule_gone_quiet(_as_naive(r.last_interaction), int(r.prior_week or 0), now_utc) \
                and not await _already_alerted(r.student_id, "gone_quiet"):
            seen_quiet.add(r.student_id)
            idle_days = (now_utc - _as_naive(r.last_interaction)).days
            alerts.append({
                "student_id": r.student_id, "class_id": r.class_id,
                "teacher_id": r.teacher_id, "rule": "gone_quiet",
                "details": f"📵 *{r.name}* ({r.class_name}) era participativo e está em silêncio há {idle_days} dias. "
                           f"Sugestão: pergunte como ele está — antes de cobrar estudo.",
            })

    return alerts


async def process_sentinel_alerts():
    """Roda 1x/dia (janela das 12h locais): coleta alertas inéditos, registra o
    dedupe e envia UM digest por professor via fila outbound."""
    global _LAST_DIGEST_DATE
    now_utc = _naive_utc_now()
    now_local = now_utc + LOCAL_TZ_OFFSET
    if now_local.hour != DIGEST_HOUR_LOCAL or _LAST_DIGEST_DATE == now_local.date():
        return
    _LAST_DIGEST_DATE = now_local.date()

    async with AsyncSessionLocal() as session:
        alerts = await _collect_alerts(session, now_utc)
        if not alerts:
            logger.debug("sentinel_no_alerts")
            return

        # Registra o dedupe ANTES do envio (falha de entrega não duplica amanhã)
        for a in alerts:
            await session.execute(
                text('''INSERT INTO "SentinelAlerts" (id, student_id, class_id, rule, details, notified_at)
                        VALUES (:id, :sid, :cid, :rule, :details, :now)'''),
                {"id": str(uuid4()), "sid": a["student_id"], "cid": a["class_id"],
                 "rule": a["rule"], "details": a["details"], "now": now_utc},
            )
        await session.commit()

        # Endereços dos professores
        teacher_ids = {a["teacher_id"] for a in alerts if a["teacher_id"]}
        teachers = {}
        for tid in teacher_ids:
            res = await session.execute(
                text('SELECT phone, telegram_chat_id FROM "Teachers" WHERE id = :tid AND is_active = true'),
                {"tid": tid},
            )
            row = res.fetchone()
            if row:
                teachers[tid] = row

    # Digest por professor (1 mensagem com todos os alunos dele)
    from src.queue.factory import get_queue
    default_ct = settings.CHANNEL_BACKEND.lower()
    queue = get_queue()
    digests_sent = 0

    for tid in teacher_ids:
        teacher = teachers.get(tid)
        if not teacher:
            continue
        items = [a["details"] for a in alerts if a["teacher_id"] == tid]
        if not items:
            continue
        body = SENTINEL_DIGEST_HEADER + "\n" + "\n\n".join(items) + SENTINEL_FOOTER
        ct = "telegram" if (default_ct == "telegram" and teacher.telegram_chat_id) else "whatsapp"
        addr = teacher.telegram_chat_id if ct == "telegram" else teacher.phone
        if not addr:
            continue
        await queue.enqueue("outbound_messages", {
            "recipient_id": addr, "channel": ct, "text": body[:4000],
            "origin": "sentinel_digest",
        })
        digests_sent += 1

    track_event("sentinel_digest_sent", {"alerts": len(alerts), "teachers": digests_sent})
    logger.info("sentinel_digest_sent", alerts=len(alerts), teachers=digests_sent)
