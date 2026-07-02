"""
Speaking + Pronúncia (F3) — o diferencial da vertical de idiomas.

Espelha o padrão do `essay.py` (mídia → análise → feedback), agora para ÁUDIO:

  borda (regex/Redis, media_id, SLA 200ms)
    → worker: download_media → ISpeechToText.transcribe
      → [se PRONUNCIATION_PROVIDER != none] IPronunciationAssessor.assess
        → feedback socrático ancorado (PRONUNCIATION_FEEDBACK_PROMPT)

LGPD (voz é dado pessoal sensível): o áudio NUNCA é persistido — após a análise só
guardamos os SCORES (números) em `PronunciationAttempts`. O array de bytes do áudio
sai de escopo ao fim da função (descarte imediato; `SPEECH_AUDIO_RETENTION_SECONDS=0`).

Fail-safe (fallback sempre): STT off → "manda por texto"; STT erro → idem; provider
de pronúncia off/erro → só correção textual. Tudo atrás de SPEECH_INPUT_ENABLED +
LANGUAGE_MODE_ENABLED + turma de idioma. Idempotência por media_id (replay não
re-analisa nem re-debita).
"""

import json
from uuid import uuid4

import structlog

from src.cache.redis_client import get_redis
from src.shared.config import settings
from src.shared.constants.messages import (
    SPEAKING_RECEIVED,
    SPEAKING_STT_FAILED,
    SPEAKING_STT_UNAVAILABLE,
    UNSUPPORTED_MESSAGE_TYPE,
)
from src.shared.constants.prompts import PRONUNCIATION_FEEDBACK_PROMPT

logger = structlog.get_logger(__name__)

# Janela de idempotência do media_id (replay protection). Curta o bastante para
# o ciclo de entrega, longa o bastante para cobrir reentregas da fila.
_MEDIA_IDEMPOTENCY_TTL = 86400  # 24h


def speaking_enabled() -> bool:
    """Recurso de fala ligado (mestre da vertical + submodo de entrada)."""
    return bool(settings.LANGUAGE_MODE_ENABLED and settings.SPEECH_INPUT_ENABLED)


def _processed_key(media_id: str) -> str:
    return f"speaking_done:{media_id}"


def format_scores(score) -> str:
    """Resumo legível da matriz de pronúncia p/ o prompt. None → 'indisponível'.
    Puro — testável."""
    if score is None:
        return "indisponível"
    weak = sorted((w for w in score.words if w.score < 70), key=lambda w: w.score)[:3]
    weak_str = "; ".join(f"{w.word} ({int(w.score)})" for w in weak) if weak else "—"
    return (
        f"accuracy {int(score.accuracy)}, fluency {int(score.fluency)}, "
        f"completeness {int(score.completeness)}; palavras a melhorar: {weak_str}"
    )


async def handle_audio_message(channel, sender_id: str, student_context: dict, media_id: str, message_id: str = "") -> None:
    """Roteia a nota de voz recebida. Chamado pelo llm_worker ANTES do debounce.

    Flag off / turma não-idioma ⇒ resposta de tipo não suportado (comportamento
    de antes do F3). Replay do mesmo media_id ⇒ ignorado (idempotência).
    """
    if not speaking_enabled():
        await channel.send_text(sender_id, UNSUPPORTED_MESSAGE_TYPE)
        return

    class_obj = student_context.get("class_obj")
    if not (class_obj and getattr(class_obj, "is_language", False)):
        await channel.send_text(sender_id, UNSUPPORTED_MESSAGE_TYPE)
        return

    if not media_id:
        await channel.send_text(sender_id, SPEAKING_STT_FAILED)
        return

    # Idempotência (replay): CAS atômico no media_id — só o 1º vencedor analisa.
    redis = await get_redis()
    won = await redis.set(_processed_key(media_id), "1", nx=True, ex=_MEDIA_IDEMPOTENCY_TTL)
    if not won:
        logger.info("speaking_replay_ignored", media_id=media_id, message_id=message_id)
        return

    try:
        await analyze_speaking(channel, sender_id, student_context, media_id)
    except Exception:
        # Falha inesperada APÓS o CAS: libera o media_id para que a reentrega da
        # fila possa reprocessar (senão o aluno ficaria 24h sem feedback). O erro
        # continua subindo para o tratamento padrão do worker.
        await redis.delete(_processed_key(media_id))
        raise


async def analyze_speaking(channel, sender_id: str, student_context: dict, media_id: str) -> None:
    """Baixa o áudio, transcreve, avalia a pronúncia (opcional) e devolve feedback
    socrático. O áudio é descartado ao fim (só scores persistem)."""
    from src.speech.factory import get_stt, get_pronunciation
    from src.shared.language import detect_language_course, language_name, level_to_cefr
    from src.shared.privacy import llm_safe_name
    from src.shared.telemetry import track_event

    student = student_context["student"]
    course = student_context["course"]
    class_obj = student_context.get("class_obj")

    stt = get_stt()
    if stt is None:
        # STT desligado/sem provider → degrada para texto (fallback sempre).
        await channel.send_text(sender_id, SPEAKING_STT_UNAVAILABLE)
        return

    spec = detect_language_course(course.name, default_target=settings.DEFAULT_TARGET_LANGUAGE)
    target_lang = spec.target_language if spec else settings.DEFAULT_TARGET_LANGUAGE
    cefr = level_to_cefr(getattr(class_obj, "proficiency_level", None))

    await channel.send_text(sender_id, SPEAKING_RECEIVED)

    # 1. Download do áudio (canal). O download NUNCA acontece na borda (SLA 200ms).
    try:
        audio_bytes = await channel.download_media(media_id)
    except Exception as e:
        logger.error("speaking_download_failed", error=str(e)[:300])
        await channel.send_text(sender_id, SPEAKING_STT_FAILED)
        return

    # 2. STT (Tenacity interno). Erro/silêncio → pede texto (fallback).
    try:
        transcription = await stt.transcribe(audio_bytes, target_lang)
    except Exception as e:
        logger.warning("speaking_stt_failed", provider=getattr(stt, "name", "?"), error=str(e)[:300])
        track_event("speech_provider_fallback", {"student_id": student.id, "stage": "stt"})
        await channel.send_text(sender_id, SPEAKING_STT_FAILED)
        return

    said = (transcription.text or "").strip()
    if not said:
        await channel.send_text(sender_id, SPEAKING_STT_FAILED)
        return

    # 3. Pronúncia (opcional). Referência = o que o aluno disse (fala livre).
    #    Provider 'none'/erro → só correção textual (degrada com graça).
    score = None
    pron = get_pronunciation()
    if pron is not None:
        try:
            score = await pron.assess(audio_bytes, reference=said, language=target_lang)
        except Exception as e:
            logger.warning("pronunciation_assess_failed", error=str(e)[:300])
            track_event("speech_provider_fallback", {"student_id": student.id, "stage": "pronunciation"})

    # ── LGPD: a partir daqui o áudio NÃO é mais usado; sai de escopo (descarte). ──
    del audio_bytes

    # 4. Feedback socrático ancorado (str.format, nunca f-string).
    from src.llm.factory import get_provider
    llm = get_provider(settings.DEFAULT_LLM_PROVIDER)
    feedback = await llm.agenerate(
        prompt=PRONUNCIATION_FEEDBACK_PROMPT.format(
            target_language_name=language_name(target_lang),
            native_language_name=language_name(settings.DEFAULT_NATIVE_LANGUAGE),
            cefr_level=cefr,
            student_name=llm_safe_name(student.name),
            transcription=said[:1500],
            scores=format_scores(score),
        )
    )
    await channel.send_text(sender_id, feedback[:4000])

    # 5. Persiste SÓ os scores (sem áudio) — insumo do Inventário de Erros (F6).
    if score is not None:
        try:
            from src.db.database import AsyncSessionLocal
            from sqlalchemy import text
            details = json.dumps(
                [{"word": w.word, "score": w.score} for w in score.words], ensure_ascii=False
            )
            async with AsyncSessionLocal() as session:
                await session.execute(
                    text('''INSERT INTO "PronunciationAttempts"
                                (id, student_id, course_id, language, item_ref,
                                 accuracy, fluency, completeness, details_json)
                            VALUES (:id, :sid, :cid, :lang, :ref,
                                 :acc, :flu, :comp, :details)'''),
                    {
                        "id": str(uuid4()), "sid": student.id, "cid": course.id,
                        # LGPD: NÃO persistir a transcrição (conteúdo de fala = dado
                        # pessoal). Guardamos só os scores numéricos → item_ref = NULL.
                        "lang": target_lang, "ref": None,
                        "acc": score.accuracy, "flu": score.fluency,
                        "comp": score.completeness, "details": details,
                    },
                )
                await session.commit()
        except Exception as e:
            # Persistir score não pode derrubar o feedback já entregue ao aluno.
            logger.error("pronunciation_persist_failed", error=str(e)[:300])

        # Gamificação (Fase 0) — nota de voz AVALIADA acumula XP em silêncio
        # (alto esforço + retrieval ativo). Isolado; flag OFF ⇒ no-op.
        try:
            from src.workers.gamification import award_xp
            _subj = student_context.get("subject")
            await award_xp(student.id, course.id, "voice",
                           subject_id=_subj.id if _subj else None)
        except Exception as e:
            logger.error("gamification_voice_hook_failed", error=str(e)[:200])

    track_event("pronunciation_assessed" if score is not None else "speaking_received", {
        "student_id": student.id, "course_id": course.id, "lang": target_lang,
        "has_scores": score is not None,
    })
    logger.info("speaking_analyzed", student_id=student.id, has_scores=score is not None)
