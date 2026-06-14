"""
Essay — Correção Dissertativa com a Rubrica do Professor (H2.2).

Fluxo do aluno:
  a) `/corrigir` → o bot pede a foto (flag `essay_pending:{sender}` no Redis,
     TTL 10 min) → próxima foto entra na correção; OU
  b) foto com legenda contendo "corrigir"/"redação" → correção direta.
  Foto sem contexto → orientação (ESSAY_PHOTO_UNEXPECTED), nunca silêncio.

Pipeline: download da mídia (canal) → OCR (Azure Document Intelligence, o
mesmo parser da ingestão) → LLM ancorado na RUBRICA DO PROFESSOR (tabela
Rubrics; fallback: rubrica padrão com aviso). Rate limit diário por aluno
(ESSAY_DAILY_LIMIT) — correção é o fluxo mais caro do produto.
"""

import re
from datetime import datetime, timezone
from uuid import uuid4

import structlog
from sqlalchemy import text

from src.shared.config import settings
from src.shared.telemetry import track_event
from src.db.database import AsyncSessionLocal
from src.cache.redis_client import get_redis
from src.shared.constants.messages import (
    ESSAY_PROMPT_PHOTO,
    ESSAY_RECEIVED,
    ESSAY_RATE_LIMITED,
    ESSAY_OCR_FAILED,
    ESSAY_PHOTO_UNEXPECTED,
)
from src.shared.constants.prompts import ESSAY_CORRECTION_PROMPT, DEFAULT_ESSAY_RUBRIC

logger = structlog.get_logger(__name__)

ESSAY_PENDING_TTL = 600  # 10 min para mandar a foto após o /corrigir
_CAPTION_TRIGGER = re.compile(r"corrig|reda[çc][ãa]o|disserta", re.IGNORECASE)

# OCR precisa render texto minimamente legível para valer a correção.
MIN_ESSAY_CHARS = 120


def caption_triggers_essay(caption: str) -> bool:
    """Legenda da foto indica pedido de correção? (puro, testável)"""
    return bool(_CAPTION_TRIGGER.search(caption or ""))


def _pending_key(sender_id: str) -> str:
    return f"essay_pending:{sender_id}"


def _daily_count_key(student_id: str) -> str:
    today = (datetime.now(timezone.utc)).strftime("%Y%m%d")
    return f"essay_count:{student_id}:{today}"


async def run_correct_essay_command(channel, sender_id: str):
    """/corrigir → arma o estado e pede a foto."""
    try:
        redis = await get_redis()
        await redis.set(_pending_key(sender_id), "1", ex=ESSAY_PENDING_TTL)
        await channel.send_text(sender_id, ESSAY_PROMPT_PHOTO)
    except Exception as e:
        logger.error("correct_essay_command_failed", error=str(e)[:200])


async def handle_image_message(channel, sender_id: str, student_context: dict, media_id: str, caption: str) -> None:
    """Roteia a foto recebida: correção (estado armado ou legenda-gatilho) ou
    orientação. Chamado pelo llm_worker ANTES do debounce."""
    redis = await get_redis()
    pending = await redis.get(_pending_key(sender_id))

    if not pending and not caption_triggers_essay(caption):
        await channel.send_text(sender_id, ESSAY_PHOTO_UNEXPECTED)
        return

    await redis.delete(_pending_key(sender_id))
    await correct_essay(channel, sender_id, student_context, media_id)


async def correct_essay(channel, sender_id: str, student_context: dict, media_id: str) -> None:
    student = student_context["student"]
    course = student_context["course"]
    subject = student_context.get("subject")
    subject_id = subject.id if subject else None

    # Rate limit diário (custo: OCR + LLM por correção)
    redis = await get_redis()
    count_key = _daily_count_key(student.id)
    count = await redis.incr(count_key)
    if count == 1:
        await redis.expire(count_key, 24 * 3600)
    if count > settings.ESSAY_DAILY_LIMIT:
        await channel.send_text(sender_id, ESSAY_RATE_LIMITED.format(limit=settings.ESSAY_DAILY_LIMIT))
        return

    await channel.send_text(sender_id, ESSAY_RECEIVED)

    # 1. Download da foto (canal) + OCR (parser da stack, com Tenacity interno)
    try:
        image_bytes = await channel.download_media(media_id)
    except Exception as e:
        logger.error("essay_download_failed", error=str(e)[:300])
        await channel.send_text(sender_id, ESSAY_OCR_FAILED)
        return

    essay_text = ""
    try:
        from src.parsing.factory import get_parser
        paragraphs = await get_parser().parse(blob_url="", file_bytes=image_bytes, filename="redacao.jpg")
        essay_text = "\n\n".join(paragraphs).strip()
    except Exception as e:
        logger.error("essay_ocr_failed", error=str(e)[:300])

    if len(essay_text) < MIN_ESSAY_CHARS:
        await channel.send_text(sender_id, ESSAY_OCR_FAILED)
        return

    # 2. Rubrica do professor (escopo curso+disciplina; fallback: padrão + aviso)
    subject_clause = "subject_id = :sub" if subject_id else "subject_id IS NULL"
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            text(f'''SELECT criteria, scale FROM "Rubrics"
                     WHERE course_id = :cid AND {subject_clause}
                     ORDER BY updated_at DESC LIMIT 1'''),
            {"cid": course.id, **({"sub": subject_id} if subject_id else {})},
        )
        rubric = res.fetchone()

    criteria = rubric.criteria if rubric else DEFAULT_ESSAY_RUBRIC
    scale = rubric.scale if rubric else "0 a 10"

    # 3. Correção ancorada (str.format, nunca f-string)
    from src.llm.factory import get_provider
    llm = get_provider(settings.DEFAULT_LLM_PROVIDER)
    feedback = await llm.agenerate(
        prompt=ESSAY_CORRECTION_PROMPT.format(
            criteria=criteria, scale=scale, essay_text=essay_text[:8000]
        )
    )

    if not rubric:
        feedback += "\n\n_ℹ️ Corrigido com a rubrica padrão — seu professor ainda não cadastrou critérios próprios (/rubrica)._"

    # LGPD Art. 20 (P1.6): correção dissertativa é decisão automatizada — o
    # aluno tem direito a revisão humana. Convite no rodapé + janela no Redis
    # que arma o intercept de "REVISAR" no llm_worker.
    from src.shared.constants.messages import ESSAY_REVIEW_FOOTER
    from src.workers.privacy_commands import open_essay_review_window
    feedback = feedback[: 4000 - len(ESSAY_REVIEW_FOOTER)] + ESSAY_REVIEW_FOOTER
    await open_essay_review_window(student.id)

    await channel.send_text(sender_id, feedback[:4000])

    # 4. Telemetria (alimenta /engajamento e o histórico do aluno)
    async with AsyncSessionLocal() as session:
        await session.execute(
            text('''INSERT INTO "StudentInteractions" (id, student_id, course_id, interaction_type, message_content)
                    VALUES (:id, :sid, :cid, 'essay_corrected', :msg)'''),
            {"id": str(uuid4()), "sid": student.id, "cid": course.id,
             "msg": f"Redação corrigida ({len(essay_text)} caracteres)"},
        )
        await session.commit()

    track_event("essay_corrected", {
        "student_id": student.id, "course_id": course.id,
        "chars": len(essay_text), "has_rubric": bool(rubric),
    })
