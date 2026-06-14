"""
Long-Context para Bases Pequenas (F7) — bypass do RAG.

Disciplina com material total pequeno: injetar TUDO no prompt elimina o risco
de recuperação (não há "chunk errado" se todos os chunks estão lá). Flag
`LONG_CONTEXT_ENABLED` (default OFF — custo por conversa).

  - Medidor de corpus por (curso, disciplina, material): atualizado na
    ingestão (Redis, sem TTL).
  - Corpus completo via `qdrant.scroll` (MESMOS filtros de escopo de sempre,
    ordenado por aula/chunk), cacheado no Redis por 24h e invalidado pela
    ingestão.
"""

from typing import Optional

import structlog
from qdrant_client.models import FieldCondition, Filter, MatchAny, MatchValue, Range

from src.shared.config import settings

logger = structlog.get_logger(__name__)

CORPUS_CACHE_TTL_SECONDS = 60 * 60 * 24  # 24h


def corpus_scope_key(course_id: str, subject_name: Optional[str], material_type: Optional[str]) -> str:
    """Chave canônica do escopo (curso, disciplina, material) — helper puro."""
    return f"{course_id}:{subject_name or '-'}:{material_type or '-'}"


def should_bypass_rag(corpus_chars: Optional[int], enabled: bool | None = None, max_chars: int | None = None) -> bool:
    """Decide o bypass (helper puro): só com flag ligada, tamanho CONHECIDO e
    dentro do limite. Corpus desconhecido → pipeline RAG normal (fallback)."""
    enabled = settings.LONG_CONTEXT_ENABLED if enabled is None else enabled
    max_chars = settings.LONG_CONTEXT_MAX_CHARS if max_chars is None else max_chars
    if not enabled or corpus_chars is None or corpus_chars <= 0:
        return False
    return corpus_chars <= max_chars


def _scope_filter(
    teacher_id: str,
    course_id: str,
    subject_name: Optional[str],
    material_type: Optional[str],
    max_lesson: Optional[int] = None,
    allowed_sections: Optional[list[int]] = None,
) -> Filter:
    """Filtros de SEMPRE: teacher + curso (+ disciplina/material/teto de aula)."""
    conditions: list = [
        FieldCondition(key="teacher_id", match=MatchValue(value=teacher_id)),
        FieldCondition(key="course_id", match=MatchValue(value=course_id)),
    ]
    if subject_name:
        conditions.append(FieldCondition(key="subject_name", match=MatchValue(value=subject_name)))
    if material_type:
        conditions.append(FieldCondition(key="material_type", match=MatchValue(value=material_type)))
    if allowed_sections:
        conditions.append(FieldCondition(key="lesson_number", match=MatchAny(any=allowed_sections)))
    if max_lesson is not None:
        conditions.append(FieldCondition(key="lesson_number", range=Range(lte=max_lesson)))
    return Filter(must=conditions)


async def update_corpus_size(
    teacher_id: str,
    course_id: str,
    subject_name: Optional[str],
    material_type: Optional[str],
) -> Optional[int]:
    """Recalcula o tamanho do corpus do escopo (chars) após uma ingestão e
    invalida o cache do corpus completo. Best-effort: falha → None."""
    try:
        from src.cache.redis_client import get_redis
        from src.rag.qdrant_client import get_qdrant

        qdrant = await get_qdrant()
        scope = corpus_scope_key(course_id, subject_name, material_type)
        total_chars = 0
        offset = None
        while True:
            points, offset = await qdrant.scroll(
                collection_name=settings.QDRANT_COLLECTION,
                scroll_filter=_scope_filter(teacher_id, course_id, subject_name, material_type),
                limit=256,
                offset=offset,
                with_payload=["char_count", "content"],
                with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                total_chars += int(payload.get("char_count") or len(payload.get("content") or ""))
            if offset is None:
                break

        redis = await get_redis()
        await redis.set(f"corpus_size:{scope}", total_chars)
        # Material mudou → o corpus completo cacheado está obsoleto.
        async for key in redis.scan_iter(match=f"corpus:{scope}*"):
            await redis.delete(key)

        logger.info("corpus_size_updated", scope=scope, chars=total_chars)
        return total_chars
    except Exception as e:
        logger.warning("corpus_size_update_failed", error=str(e)[:200])
        return None


async def get_corpus_size(course_id: str, subject_name: Optional[str], material_type: Optional[str]) -> Optional[int]:
    """Tamanho conhecido do corpus (chars). None = nunca medido (sem bypass)."""
    try:
        from src.cache.redis_client import get_redis
        redis = await get_redis()
        value = await redis.get(f"corpus_size:{corpus_scope_key(course_id, subject_name, material_type)}")
        return int(value) if value is not None else None
    except Exception:
        return None


async def build_full_context(
    teacher_id: str,
    course_id: str,
    subject_name: Optional[str],
    material_type: Optional[str],
    max_lesson: int,
    allowed_sections: Optional[list[int]] = None,
) -> Optional[str]:
    """Monta o corpus COMPLETO do escopo (ordenado por aula → chunk), com os
    mesmos filtros e teto anti-spoiler do RAG. Cache Redis 24h. None = falha
    (o chamador segue com o pipeline RAG normal)."""
    scope = corpus_scope_key(course_id, subject_name, material_type)
    sections_key = ",".join(map(str, sorted(allowed_sections))) if allowed_sections else "-"
    cache_key = f"corpus:{scope}:lesson{max_lesson}:sec{sections_key}"

    try:
        from src.cache.redis_client import get_redis
        redis = await get_redis()
        cached = await redis.get(cache_key)
        if cached:
            return cached
    except Exception:
        redis = None

    try:
        from src.rag.qdrant_client import get_qdrant
        qdrant = await get_qdrant()

        rows = []
        offset = None
        total_chars = 0
        while True:
            points, offset = await qdrant.scroll(
                collection_name=settings.QDRANT_COLLECTION,
                scroll_filter=_scope_filter(
                    teacher_id, course_id, subject_name, material_type,
                    max_lesson=max_lesson, allowed_sections=allowed_sections,
                ),
                limit=256,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            for point in points:
                payload = point.payload or {}
                content = payload.get("content") or ""
                if not content:
                    continue
                rows.append((
                    int(payload.get("lesson_number") or 0),
                    payload.get("source") or "",
                    int(payload.get("chunk_index") or 0),
                    payload.get("section_title"),
                    content,
                ))
                total_chars += len(content)
                if total_chars > settings.LONG_CONTEXT_MAX_CHARS:
                    # Medidor desatualizado e corpus maior que o limite: aborta
                    # o bypass (o RAG normal assume) em vez de estourar o prompt.
                    logger.warning("full_context_overflow_aborting_bypass", scope=scope)
                    return None
            if offset is None:
                break

        if not rows:
            return None

        rows.sort(key=lambda r: (r[0], r[1], r[2]))
        blocks = []
        for lesson_number, source, _idx, section_title, content in rows:
            section_part = f" | Seção: {section_title}" if section_title else ""
            blocks.append(f"[Fonte: {source} | Material da Aula: {lesson_number}{section_part}]\n{content}")
        full_context = "\n\n".join(blocks)

        # Conta de FinOps: ~4 chars/token em PT-BR — logado para a decisão
        # por disciplina (ver walkthrough).
        logger.info(
            "full_context_built",
            scope=scope, chunks=len(rows), chars=len(full_context),
            approx_tokens=len(full_context) // 4,
        )

        if redis is not None:
            try:
                await redis.set(cache_key, full_context, ex=CORPUS_CACHE_TTL_SECONDS)
            except Exception:
                pass
        return full_context
    except Exception as e:
        logger.warning("full_context_build_failed", error=str(e)[:200])
        return None
