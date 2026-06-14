"""
Opt-out de mensagens proativas (F9 — exigência de Quality Rating da Meta).

O aluno pode encerrar o recebimento de mensagens automáticas enviando *SAIR*
(convenção da Meta) e retomar com *VOLTAR*. Persistimos a escolha em
`Students.messaging_opted_out`; as filas proativas respeitam a flag via
`messaging_optout_clause`. Não é flag-gated — é um direito do aluno.

A detecção de intenção (`optout_intent`) é pura/testável. A "saída" do roleplay usa
outras palavras (parar/encerrar), então *SAIR* fica reservado ao opt-out.
"""

import re

import structlog

from src.shared.constants.messages import OPTIN_CONFIRMED, OPTOUT_CONFIRMED

logger = structlog.get_logger(__name__)

_OUT_REGEX = re.compile(r"^\s*(sair|cancelar)\s*$", re.IGNORECASE)
_IN_REGEX = re.compile(r"^\s*(voltar|retomar)\s*$", re.IGNORECASE)


def optout_intent(text: str) -> str | None:
    """'out' (SAIR/CANCELAR), 'in' (VOLTAR/RETOMAR) ou None. Puro/testável."""
    if not text:
        return None
    if _OUT_REGEX.match(text):
        return "out"
    if _IN_REGEX.match(text):
        return "in"
    return None


async def handle_optout(channel, sender_id: str, student_context: dict, text: str) -> bool:
    """Trata SAIR/VOLTAR. Retorna True se consumiu a mensagem (era opt-out/in)."""
    intent = optout_intent(text)
    if intent is None:
        return False

    student = student_context["student"]
    opted_out = intent == "out"
    try:
        from src.db.database import AsyncSessionLocal
        from sqlalchemy import text as sql_text
        async with AsyncSessionLocal() as session:
            await session.execute(
                sql_text('''UPDATE "Students" SET messaging_opted_out = :v, updated_at = CURRENT_TIMESTAMP
                            WHERE id = :sid'''),
                {"v": opted_out, "sid": student.id},
            )
            await session.commit()
    except Exception as e:
        logger.error("optout_persist_failed", error=str(e)[:200])
        return True  # consumimos a intenção mesmo se a persistência falhar (não cai no pipeline)

    from src.shared.telemetry import track_event
    track_event("messaging_optout" if opted_out else "messaging_optin", {"student_id": student.id})
    await channel.send_text(sender_id, OPTOUT_CONFIRMED if opted_out else OPTIN_CONFIRMED)
    logger.info("messaging_optout_set", student_id=student.id, opted_out=opted_out)
    return True
