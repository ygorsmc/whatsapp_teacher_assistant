"""
Essay — Correção de Redação por foto (H2.2 + Vertical de Idiomas).

Fluxo do aluno:
  a) `/corrigir` → o bot pede a foto (flag `essay_pending:{sender}` no Redis,
     TTL 10 min) → próxima foto entra na correção; OU
  b) foto com legenda contendo "/corrigir"/"corrigir"/"redação" → correção direta.
  Foto sem contexto → orientação (ESSAY_PHOTO_UNEXPECTED), nunca silêncio.

Dois caminhos de correção:
  - IDIOMAS (o padrão): aluno com matrícula em turma de idioma → o Mistral OCR
    lê a FOTO (ocr_image, sem enriquecimento Gemini) e o TEXTO extraído vai ao
    ESSAY_LANGUAGE_MODEL (gemini-3.5-flash), que corrige como um examinador
    CEFR (LANGUAGE_ESSAY_CORRECTION_PROMPT), com feedback na língua do nível.
  - GENÉRICO (H2.2, atrás de ESSAY_ALL_COURSES_ENABLED): OCR (parser da
    stack) → LLM ancorado na RUBRICA DO PROFESSOR (tabela Rubrics; fallback:
    rubrica padrão com aviso).

Rate limit diário por aluno (ESSAY_DAILY_LIMIT) — correção é o fluxo mais
caro do produto.
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
    ESSAY_RECEIVED_LANGUAGE,
    ESSAY_RATE_LIMITED,
    ESSAY_OCR_FAILED,
    ESSAY_OCR_PREVIEW,
    ESSAY_PHOTO_UNEXPECTED,
    ESSAY_LANGUAGE_ONLY,
)
from src.shared.constants.prompts import (
    ESSAY_CORRECTION_PROMPT,
    DEFAULT_ESSAY_RUBRIC,
    LANGUAGE_ESSAY_CORRECTION_PROMPT,
)

logger = structlog.get_logger(__name__)

ESSAY_PENDING_TTL = 600  # 10 min para mandar a foto após o /corrigir
_CAPTION_TRIGGER = re.compile(r"corrig|reda[çc][ãa]o|disserta", re.IGNORECASE)

# OCR precisa render texto minimamente legível para valer a correção.
MIN_ESSAY_CHARS = 120


def caption_triggers_essay(caption: str) -> bool:
    """Legenda da foto indica pedido de correção? (puro, testável)"""
    return bool(_CAPTION_TRIGGER.search(caption or ""))


async def _maybe_preview_ocr(channel, sender_id: str, essay_text: str) -> None:
    """DEBUG de testes (ESSAY_OCR_PREVIEW_ENABLED): manda ao aluno o texto que o
    OCR leu, para conferir a qualidade da transcrição antes da correção. No-op
    em produção (flag OFF). Nunca derruba a correção se o envio falhar."""
    if not settings.ESSAY_OCR_PREVIEW_ENABLED:
        return
    try:
        await channel.send_text(sender_id, ESSAY_OCR_PREVIEW.format(ocr_text=essay_text[:3500]))
    except Exception as e:
        logger.error("essay_ocr_preview_failed", error=str(e)[:200])


def _pending_key(sender_id: str) -> str:
    return f"essay_pending:{sender_id}"


def _daily_count_key(student_id: str) -> str:
    today = (datetime.now(timezone.utc)).strftime("%Y%m%d")
    return f"essay_count:{student_id}:{today}"


async def _student_id_for_sender(channel, sender_id: str) -> str | None:
    """Resolve o id do aluno pelo endereço do canal (phone/telegram_chat_id)."""
    query_col = channel.get_user_query_column()
    async with AsyncSessionLocal() as session:
        row = (await session.execute(
            text(f'SELECT id FROM "Students" WHERE {query_col} = :sid AND is_active = true LIMIT 1'),
            {"sid": sender_id},
        )).fetchone()
        return row.id if row else None


async def _language_essay_context(student_id: str):
    """(matrícula de idioma, delivery, língua-alvo) do aluno — ou (None, None, None).

    Busca a matrícula em turma de IDIOMA diretamente, independente da matrícula
    default do student_context: aluno em PIBEP + inglês corrige pelo inglês.
    """
    async with AsyncSessionLocal() as session:
        lang = (await session.execute(
            text('''SELECT c.id AS course_id, c.name AS course_name,
                           cl.proficiency_level, cl.subject_id
                    FROM "StudentEnrollment" se
                    JOIN "Classes" cl ON cl.id = se.class_id AND cl.is_language = true
                    JOIN "Courses" c ON c.id = cl.course_id
                    WHERE se.student_id = :sid
                    ORDER BY se.created_at LIMIT 1'''),
            {"sid": student_id},
        )).fetchone()
    if not lang:
        return None, None, None
    # Mesma resolução de língua/nível da revisão (fonte única da verdade).
    from src.rag.review_generator import class_language_context
    delivery, target = class_language_context(lang.course_name, True, lang.proficiency_level)
    if delivery is None or target is None:
        return None, None, None
    return lang, delivery, target


async def run_correct_essay_command(channel, sender_id: str):
    """/corrigir → arma o estado e pede a foto (que também pode vir direto com
    a legenda /corrigir). Por padrão o recurso atende só turmas de idiomas."""
    try:
        if not settings.ESSAY_ALL_COURSES_ENABLED:
            student_id = await _student_id_for_sender(channel, sender_id)
            delivery = None
            if student_id:
                _, delivery, _ = await _language_essay_context(student_id)
            if delivery is None:
                await channel.send_text(sender_id, ESSAY_LANGUAGE_ONLY)
                return
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

    # Vertical de Idiomas: correção VISUAL especialista quando o aluno tem
    # matrícula em turma de idioma. Sem ela, o fluxo genérico (OCR + rubrica)
    # só roda com ESSAY_ALL_COURSES_ENABLED — por padrão, o recurso é de idiomas.
    lang, delivery, target_language = await _language_essay_context(student.id)
    if delivery is None and not settings.ESSAY_ALL_COURSES_ENABLED:
        await channel.send_text(sender_id, ESSAY_LANGUAGE_ONLY)
        return

    # Rate limit diário (custo: visão/OCR + LLM por correção) — depois do gate,
    # para a recusa não consumir crédito.
    redis = await get_redis()
    count_key = _daily_count_key(student.id)
    count = await redis.incr(count_key)
    if count == 1:
        await redis.expire(count_key, 24 * 3600)
    if count > settings.ESSAY_DAILY_LIMIT:
        await channel.send_text(sender_id, ESSAY_RATE_LIMITED.format(limit=settings.ESSAY_DAILY_LIMIT))
        return

    if delivery is not None and target_language is not None:
        feedback = await _correct_language_essay(channel, sender_id, media_id, delivery, target_language)
        log_course_id, log_subject_id, mode = lang.course_id, lang.subject_id, "idiomas"
    else:
        feedback = await _correct_generic_essay(channel, sender_id, media_id, course.id, subject_id)
        log_course_id, log_subject_id, mode = course.id, subject_id, "rubrica"

    if feedback is None:
        return  # a falha já foi comunicada ao aluno no caminho específico

    # LGPD Art. 20 (P1.6): correção dissertativa é decisão automatizada — o
    # aluno tem direito a revisão humana. Convite no rodapé + janela no Redis
    # que arma o intercept de "REVISAR" no llm_worker.
    from src.shared.constants.messages import ESSAY_REVIEW_FOOTER
    from src.workers.privacy_commands import open_essay_review_window
    feedback = feedback + ESSAY_REVIEW_FOOTER
    await open_essay_review_window(student.id)

    # Sem truncar: channel.send_text divide sozinho em várias mensagens se o
    # feedback (correção completa + rodapé) passar do teto de uma mensagem.
    await channel.send_text(sender_id, feedback)

    # Telemetria (alimenta /engajamento e o histórico do aluno)
    async with AsyncSessionLocal() as session:
        await session.execute(
            text('''INSERT INTO "StudentInteractions" (id, student_id, course_id, subject_id, interaction_type, message_content)
                    VALUES (:id, :sid, :cid, :sub, 'essay_corrected', :msg)'''),
            {"id": str(uuid4()), "sid": student.id, "cid": log_course_id,
             "sub": log_subject_id, "msg": f"Redação corrigida ({mode})"},
        )
        await session.commit()

    # Gamificação (Fase 0) — redação enviada acumula XP em silêncio (alto esforço).
    # Isolado; flag OFF ⇒ no-op.
    try:
        from src.workers.gamification import award_xp
        await award_xp(student.id, log_course_id, "essay", subject_id=log_subject_id)
    except Exception as e:
        logger.error("gamification_essay_hook_failed", error=str(e)[:200])

    track_event("essay_corrected", {
        "student_id": student.id, "course_id": log_course_id, "mode": mode,
    })


async def _correct_language_essay(channel, sender_id: str, media_id: str, delivery, target_language: str) -> str | None:
    """Correção da Vertical de Idiomas: o Mistral OCR lê a foto (ocr_image,
    sem enriquecimento Gemini) e o TEXTO extraído vai ao ESSAY_LANGUAGE_MODEL,
    que corrige como examinador CEFR — feedback na língua do TEXTO do nível
    (iniciante lê em L1; avançado, na língua-alvo — convenção da revisão).
    Retorna o feedback, ou None (falha já comunicada ao aluno)."""
    from src.shared.language import language_name

    await channel.send_text(sender_id, ESSAY_RECEIVED_LANGUAGE)
    try:
        image_bytes = await channel.download_media(media_id)
    except Exception as e:
        logger.error("essay_download_failed", error=str(e)[:300])
        await channel.send_text(sender_id, ESSAY_OCR_FAILED)
        return None

    # 1. OCR da foto via Mistral (explícito — o parser default pode ser outro)
    essay_text = ""
    try:
        from src.parsing.factory import get_parser
        essay_text = (await get_parser("mistral").ocr_image(image_bytes)).strip()
    except Exception as e:
        logger.error("essay_mistral_ocr_failed", error=str(e)[:300])

    if len(essay_text) < MIN_ESSAY_CHARS:
        await channel.send_text(sender_id, ESSAY_OCR_FAILED)
        return None

    await _maybe_preview_ocr(channel, sender_id, essay_text)

    # 2. Correção especialista (str.format, nunca f-string)
    prompt = LANGUAGE_ESSAY_CORRECTION_PROMPT.format(
        target_language_name=language_name(target_language),
        feedback_language_name=language_name(delivery.text_language),
        cefr_level=delivery.cefr_level,
        essay_text=essay_text[:8000],
    )
    try:
        # Modelo dedicado (ESSAY_LANGUAGE_MODEL): o DEFAULT_LLM_PROVIDER pode
        # apontar para um modelo mais fraco para correção pedagógica.
        from src.llm.providers import GeminiLLMProvider
        llm = GeminiLLMProvider(settings.ESSAY_LANGUAGE_MODEL)
        return await llm.agenerate(prompt=prompt)
    except Exception as e:
        logger.error("essay_language_correction_failed", error=str(e)[:300])
        await channel.send_text(sender_id, ESSAY_OCR_FAILED)
        return None


async def _correct_generic_essay(channel, sender_id: str, media_id: str, course_id: str, subject_id: str | None) -> str | None:
    """Fluxo H2.2 original (turmas não-idioma, atrás de ESSAY_ALL_COURSES_ENABLED):
    OCR (parser da stack) + rubrica do professor. Retorna o feedback, ou None."""
    await channel.send_text(sender_id, ESSAY_RECEIVED)

    # 1. Download da foto (canal) + OCR (parser da stack, com Tenacity interno)
    try:
        image_bytes = await channel.download_media(media_id)
    except Exception as e:
        logger.error("essay_download_failed", error=str(e)[:300])
        await channel.send_text(sender_id, ESSAY_OCR_FAILED)
        return None

    essay_text = ""
    try:
        from src.parsing.factory import get_parser
        paragraphs = await get_parser().parse(blob_url="", file_bytes=image_bytes, filename="redacao.jpg")
        essay_text = "\n\n".join(paragraphs).strip()
    except Exception as e:
        logger.error("essay_ocr_failed", error=str(e)[:300])

    if len(essay_text) < MIN_ESSAY_CHARS:
        await channel.send_text(sender_id, ESSAY_OCR_FAILED)
        return None

    await _maybe_preview_ocr(channel, sender_id, essay_text)

    # 2. Rubrica do professor (escopo curso+disciplina; fallback: padrão + aviso)
    subject_clause = "subject_id = :sub" if subject_id else "subject_id IS NULL"
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            text(f'''SELECT criteria, scale FROM "Rubrics"
                     WHERE course_id = :cid AND {subject_clause}
                     ORDER BY updated_at DESC LIMIT 1'''),
            {"cid": course_id, **({"sub": subject_id} if subject_id else {})},
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
    return feedback
