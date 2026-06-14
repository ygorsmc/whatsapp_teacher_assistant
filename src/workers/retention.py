"""
LGPD — Retenção e expurgo programado (Plano P1.3, Art. 15/16).

Rotina MENSAL (idempotente via marcador no Redis) atrás da flag
`DATA_RETENTION_ENABLED` (ligar após a validação jurídica dos prazos):

  1. Anula o CONTEÚDO de `StudentInteractions` com mais de
     `RETENTION_INTERACTION_MONTHS` meses (o evento agregado sobrevive —
     estatística sem texto livre do titular).
  2. Anonimiza alunos INATIVOS há mais de `RETENTION_INACTIVE_STUDENT_MONTHS`
     meses (mesmo expurgo do /apagar_meus_dados — fecha o ciclo dos pedidos
     encaminhados à escola: o professor desativa, a retenção conclui).

Retenções que NÃO são daqui: histórico de conversa (TTL 30d no Redis, nativo),
logs (retenção do App Insights via Bicep), `KeyActivations` (5 anos — defesa
em juízo, Art. 7º VI — expurgo futuro quando houver volume).
"""

from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import text

from src.db.database import AsyncSessionLocal
from src.shared.config import settings
from src.shared.telemetry import track_event

logger = structlog.get_logger(__name__)


def retention_month_marker(now: datetime | None = None) -> str:
    """Chave de idempotência mensal (helper puro)."""
    now = now or datetime.now(timezone.utc)
    return f"retention_done:{now.strftime('%Y%m')}"


async def run_data_retention():
    """Hook do scheduler (roda no loop de 5 min; executa 1×/mês de fato)."""
    if not settings.DATA_RETENTION_ENABLED:
        return

    try:
        from src.cache.redis_client import get_redis
        redis = await get_redis()
        marker = retention_month_marker()
        first_run = await redis.set(marker, "1", nx=True, ex=60 * 60 * 24 * 40)
        if not first_run:
            return  # já rodou neste mês
    except Exception as e:
        logger.warning("retention_marker_failed_skipping", error=str(e)[:200])
        return  # sem Redis não há idempotência — não roda (seguro)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    interactions_cutoff = now - timedelta(days=30 * settings.RETENTION_INTERACTION_MONTHS)
    inactive_cutoff = now - timedelta(days=30 * settings.RETENTION_INACTIVE_STUDENT_MONTHS)

    scrubbed_interactions = 0
    anonymized_students = 0
    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                text("""
                    UPDATE "StudentInteractions"
                    SET message_content = NULL
                    WHERE timestamp < :cutoff AND message_content IS NOT NULL
                """),
                {"cutoff": interactions_cutoff},
            )
            scrubbed_interactions = res.rowcount or 0

            stale = (await session.execute(
                text("""
                    SELECT id, phone, telegram_chat_id FROM "Students"
                    WHERE is_active = false
                      AND updated_at < :cutoff
                      AND name != '(removido a pedido — LGPD)'
                """),
                {"cutoff": inactive_cutoff},
            )).fetchall()
            await session.commit()

        from src.workers.privacy_commands import erase_student_data
        for row in stale:
            await erase_student_data(row.id, [row.phone, row.telegram_chat_id])
            anonymized_students += 1

        logger.info(
            "data_retention_completed",
            interactions_scrubbed=scrubbed_interactions,
            students_anonymized=anonymized_students,
        )
        track_event("data_retention_run", {
            "interactions_scrubbed": scrubbed_interactions,
            "students_anonymized": anonymized_students,
        })
    except Exception as e:
        logger.error("data_retention_failed", error=str(e)[:300])
