"""
CRAG-lite + Recusa Honesta (F5) — abstenção ancorada em EVIDÊNCIA.

O "avaliador" do CRAG clássico é um LLM extra por mensagem; aqui o avaliador
é o SCORE DO RERANKER (determinístico, já pago na F4). Limiares 🟢🟡🔴:

  🟢 best_score ≥ CRAG_GREEN  → evidência forte: fluxo normal.
  🟡 CRAG_RED ≤ best_score < CRAG_GREEN → evidência fraca: UMA reescrita de
     query (LLM barato) e nova rodada de fusão+rerank. Melhorou → segue com
     aviso de cautela; não melhorou → 🔴.
  🔴 best_score < CRAG_RED → abstenção: responde com o out_of_scope_message
     do professor ENRIQUECIDO e NÃO chama o LLM gerador (zero alucinação e
     zero custo de geração). HITL: o professor da turma é avisado (dedupe
     diário) — falha de busca vira insumo de conteúdo.

Anti-loop: no máximo UMA reescrita por mensagem (garantido pelo chamador).
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

import structlog

from src.shared.config import settings

logger = structlog.get_logger(__name__)

EVIDENCE_GREEN = "green"
EVIDENCE_YELLOW = "yellow"
EVIDENCE_RED = "red"


def classify_evidence(
    best_score: Optional[float],
    green: float | None = None,
    red: float | None = None,
) -> str:
    """Classifica a evidência pelo melhor score do reranker (helper puro).

    `best_score=None` (nenhum candidato recuperado) é evidência vermelha.
    """
    green = settings.CRAG_GREEN if green is None else green
    red = settings.CRAG_RED if red is None else red
    if best_score is None:
        return EVIDENCE_RED
    if best_score >= green:
        return EVIDENCE_GREEN
    if best_score >= red:
        return EVIDENCE_YELLOW
    return EVIDENCE_RED


async def rewrite_query(query: str, subject_name: Optional[str], lesson_number: int) -> Optional[str]:
    """UMA reescrita de query via LLM barato (limiar 🟡). None = falhou
    (o chamador segue com a query original — fallback sempre)."""
    try:
        from src.llm.factory import get_provider
        from src.shared.constants.prompts import QUERY_REWRITE_PROMPT

        llm = get_provider()
        prompt = QUERY_REWRITE_PROMPT.format(
            query=query,
            subject_name=subject_name or "a disciplina do aluno",
            lesson_number=lesson_number,
        )
        rewritten = (await llm.agenerate(prompt=prompt)).strip().strip('"').strip()
        if not rewritten or len(rewritten) > 500 or rewritten.lower() == query.lower():
            return None
        logger.info("crag_rewrite", original=query[:120], rewritten=rewritten[:120])
        return rewritten
    except Exception as e:
        logger.warning("crag_rewrite_failed", error=str(e)[:200])
        return None


def build_abstention_message(out_of_scope_message: str) -> str:
    """Recusa Honesta: a mensagem fora-de-escopo do professor + convite a
    reformular + transparência de que o professor foi avisado."""
    from src.shared.constants.messages import CRAG_ABSTENTION_SUFFIX
    base = (out_of_scope_message or "").strip() or "Não encontrei isso no material da disciplina."
    return base + CRAG_ABSTENTION_SUFFIX


async def register_abstention(
    student_id: str,
    course_id: str,
    class_id: str,
    class_name: str,
    teacher_phone: Optional[str],
    teacher_telegram_chat_id: Optional[str],
    query: str,
) -> None:
    """HITL da abstenção 🔴 (best-effort — falha NUNCA derruba a resposta):

      1. Registra `StudentInteractions(interaction_type='unanswered_question')`
         — alimenta o /corpus_duvidas e o eval.
      2. Agrega as perguntas do dia por turma no Redis e enfileira UM aviso
         diário ao professor (`outbound_messages`, dedupe por turma+dia).
    """
    # 1. Registro da interação (insumo de conteúdo + telemetria)
    try:
        from sqlalchemy import text as sql_text
        from src.db.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            await session.execute(
                sql_text("""
                    INSERT INTO "StudentInteractions" (id, student_id, course_id, interaction_type, message_content)
                    VALUES (:id, :sid, :cid, 'unanswered_question', :msg)
                """),
                {"id": str(uuid4()), "sid": student_id, "cid": course_id, "msg": query[:1000]},
            )
            await session.commit()
    except Exception as e:
        logger.error("crag_abstention_log_failed", error=str(e)[:200])

    # 2. Aviso ao professor (agregado por dia, dedupe diário por turma)
    try:
        from src.cache.redis_client import get_redis
        from src.shared.constants.messages import CRAG_TEACHER_NOTICE

        redis = await get_redis()
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        list_key = f"crag:unanswered:{class_id}:{day}"
        await redis.rpush(list_key, query[:200])
        await redis.expire(list_key, 60 * 60 * 48)

        dedupe_key = f"crag:notified:{class_id}:{day}"
        first_today = await redis.set(dedupe_key, "1", nx=True, ex=60 * 60 * 26)
        if not first_today:
            return  # professor já avisado hoje — só acumula a agregação

        default_ct = settings.CHANNEL_BACKEND.lower()
        channel_type = "telegram" if (default_ct == "telegram" and teacher_telegram_chat_id) else "whatsapp"
        address = teacher_telegram_chat_id if channel_type == "telegram" else teacher_phone
        if not address:
            return

        questions = await redis.lrange(list_key, 0, 2)
        examples = "\n".join(f"• _{q}_" for q in questions)
        count = await redis.llen(list_key)

        from src.queue.factory import get_queue
        await get_queue().enqueue("outbound_messages", {
            "recipient_id": address,
            "channel": channel_type,
            "text": CRAG_TEACHER_NOTICE.format(class_name=class_name, count=count, examples=examples)[:4000],
            "origin": "crag_abstention_notice",
        })
        logger.info("crag_abstention_teacher_notified", class_id=class_id, count=count)
    except Exception as e:
        logger.error("crag_abstention_notice_failed", error=str(e)[:200])
