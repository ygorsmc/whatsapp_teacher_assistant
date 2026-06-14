"""
LGPD Art. 14 — Consentimento do responsável legal (Plano P1.1).

Fluxo (atrás da flag `GUARDIAN_CONSENT_ENABLED` — política por contrato da
escola controladora; default OFF = comportamento atual intocado):

  1. A escola informa, na planilha (colunas opcionais "Responsável" e
     "Menor"), o telefone do responsável e se o aluno é criança (<12).
  2. O scheduler (`process_guardian_consent_requests`) cria a linha 'pending'
     em `GuardianConsents` e envia ao responsável o TERMO em destaque com
     botões Autorizo / Não autorizo (dedupe = a própria linha).
  3. Enquanto não houver linha 'granted', o aluno-criança NÃO recebe nem
     dispara interação: gate no `process_inbound` (conversa) e cláusula SQL
     nas entregas proativas (`guardian_consent_sql_clause`).
  4. O responsável pode responder REVOGAR a qualquer momento — todos os
     consentimentos do telefone dele são revogados e o gate volta a fechar.

O responsável NÃO é usuário do bot: os cliques/textos dele chegam pelo
webhook como remetente desconhecido e são roteados com role="guardian"
(regex/payload pré-compilados — nenhum SQL na borda).

NOTA WhatsApp: o 1º contato com o responsável acontece fora de janela de
24h — em produção WhatsApp isso exige um Utility Template aprovado pela Meta
(mesma mecânica do convite de revisão). No Telegram os botões saem direto.
"""

from datetime import datetime, timezone
from uuid import uuid4

import structlog
from sqlalchemy import text

from src.channel.factory import get_channel
from src.db.database import AsyncSessionLocal
from src.shared.config import settings
from src.shared.constants.messages import (
    GUARDIAN_CONSENT_DENIED_ACK,
    GUARDIAN_CONSENT_GRANTED_ACK,
    GUARDIAN_CONSENT_REQUEST,
    GUARDIAN_CONSENT_REVOKED_ACK,
)
from src.shared.privacy import first_name
from src.shared.telemetry import track_event

logger = structlog.get_logger(__name__)

TERM_VERSION = "v1"


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def consent_blocked(student_id: str, is_child: bool) -> bool:
    """Gate de conversa: True = criança sem opt-in 'granted' (bloqueia).

    Flag off ou aluno não-criança → nunca bloqueia. Falha de banco → NÃO
    bloqueia (fail-open: indisponibilidade não pode calar um aluno adulto
    marcado errado; o gate proativo via SQL continua valendo).
    """
    if not settings.GUARDIAN_CONSENT_ENABLED or not is_child:
        return False
    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                text("""
                    SELECT 1 FROM "GuardianConsents"
                    WHERE student_id = :sid AND status = 'granted' LIMIT 1
                """),
                {"sid": student_id},
            )
            return res.scalar_one_or_none() is None
    except Exception as e:
        logger.error("consent_gate_check_failed", error=str(e)[:200])
        return False


async def process_guardian_consent_requests():
    """Rotina do scheduler: dispara o pedido de opt-in para responsáveis de
    crianças que ainda não têm NENHUMA linha de consentimento (dedupe pela
    existência da linha — 1 pedido por aluno)."""
    if not settings.GUARDIAN_CONSENT_ENABLED:
        return

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            text("""
                SELECT s.id, s.name, s.guardian_phone, c.name AS course_name
                FROM "Students" s
                JOIN "StudentEnrollment" se ON se.student_id = s.id
                JOIN "Classes" cl ON cl.id = se.class_id
                JOIN "Courses" c ON c.id = cl.course_id
                WHERE s.is_child = true
                  AND s.is_active = true
                  AND s.guardian_phone IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM "GuardianConsents" gc WHERE gc.student_id = s.id
                  )
                LIMIT 50
            """)
        )).fetchall()

        if not rows:
            return

        # Dedupe defensivo: um aluno pode ter várias matrículas (1 linha basta).
        seen: set[str] = set()
        channel = get_channel(settings.CHANNEL_BACKEND.lower())

        for row in rows:
            if row.id in seen:
                continue
            seen.add(row.id)

            await session.execute(
                text("""
                    INSERT INTO "GuardianConsents"
                        (id, student_id, guardian_phone, status, term_version)
                    VALUES (:id, :sid, :gphone, 'pending', :term)
                """),
                {"id": str(uuid4()), "sid": row.id, "gphone": row.guardian_phone, "term": TERM_VERSION},
            )
            await session.commit()

            try:
                await channel.send_buttons(
                    row.guardian_phone,
                    GUARDIAN_CONSENT_REQUEST.format(
                        class_name=row.course_name,
                        student_first_name=first_name(row.name),
                        term_version=TERM_VERSION,
                    ),
                    buttons=[
                        {"id": f"guardian_consent_yes_{row.id}", "title": "✅ Autorizo"},
                        {"id": f"guardian_consent_no_{row.id}", "title": "❌ Não autorizo"},
                    ],
                )
                logger.info("guardian_consent_requested", student_id=row.id)
            except Exception as e:
                # Entrega falhou (ex.: WhatsApp fora de janela sem template) —
                # a linha 'pending' permanece e o gate segue FECHADO (seguro).
                logger.warning("guardian_consent_send_failed", student_id=row.id, error=str(e)[:200])

        track_event("guardian_consent_batch", {"requested": len(seen)})


async def handle_guardian_message(message_body: dict):
    """Consome mensagens com role='guardian' (botão de consentimento ou
    REVOGAR) vindas do webhook — remetente pode ser desconhecido do sistema."""
    sender_id = str(message_body.get("sender_id", ""))
    channel = get_channel(message_body.get("channel"))
    payload = message_body.get("interactive_payload") or {}
    reply_id = payload.get("id", "")
    now = _naive_utc_now()

    try:
        # ── Botões Autorizo / Não autorizo ───────────────────────────────────
        if reply_id.startswith("guardian_consent_"):
            granted = reply_id.startswith("guardian_consent_yes_")
            student_id = reply_id.replace("guardian_consent_yes_", "").replace("guardian_consent_no_", "")
            new_status = "granted" if granted else "denied"

            async with AsyncSessionLocal() as session:
                res = await session.execute(
                    text("""
                        UPDATE "GuardianConsents"
                        SET status = :st, consented_at = :now, updated_at = CURRENT_TIMESTAMP
                        WHERE student_id = :sid AND status = 'pending'
                        RETURNING id
                    """),
                    {"st": new_status, "now": now if granted else None, "sid": student_id},
                )
                updated = res.fetchone()
                student_name = (await session.execute(
                    text('SELECT name FROM "Students" WHERE id = :sid'), {"sid": student_id},
                )).scalar_one_or_none()
                await session.commit()

            if not updated:
                return  # botão repetido/expirado — silencioso (idempotência)

            ack = GUARDIAN_CONSENT_GRANTED_ACK if granted else GUARDIAN_CONSENT_DENIED_ACK
            await channel.send_text(sender_id, ack.format(student_first_name=first_name(student_name)))
            track_event("guardian_consent_decision", {"student_id": student_id, "granted": granted})
            return

        # ── REVOGAR (texto) — valida o remetente contra guardian_phone ───────
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                text("""
                    UPDATE "GuardianConsents"
                    SET status = 'revoked', revoked_at = :now, updated_at = CURRENT_TIMESTAMP
                    WHERE guardian_phone = :gphone AND status = 'granted'
                    RETURNING student_id
                """),
                {"gphone": sender_id, "now": now},
            )
            revoked_students = [r[0] for r in res.fetchall()]
            await session.commit()

        if not revoked_students:
            return  # remetente não é responsável com consentimento ativo — ignora

        from src.workers.privacy_commands import register_data_request
        for student_id in revoked_students:
            await register_data_request(
                student_id, "consent_revoke", "completed",
                message_body.get("channel"), f"revogado pelo responsável {sender_id[-4:]}",
            )

        await channel.send_text(sender_id, GUARDIAN_CONSENT_REVOKED_ACK)
        track_event("guardian_consent_revoked", {"students": len(revoked_students)})
    except Exception as e:
        logger.error("guardian_message_failed", error=str(e)[:300])
