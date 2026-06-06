import re
from src.rag.qdrant_client import get_qdrant, init_qdrant_collection
from src.llm.factory import get_embedder
from src.shared.config import settings
from qdrant_client.models import Filter, FieldCondition, MatchValue, Range
from typing import Optional
import structlog

logger = structlog.get_logger(__name__)

# Regex patterns to detect explicit lesson references in Portuguese
_LESSON_PATTERNS = [
    re.compile(r"\baula\s+(\d+)\b", re.IGNORECASE),
    re.compile(r"\blição\s+(\d+)\b", re.IGNORECASE),
    re.compile(r"\bmódulo\s+(\d+)\b", re.IGNORECASE),
    re.compile(r"\bcapítulo\s+(\d+)\b", re.IGNORECASE),
    re.compile(r"\bcapitulo\s+(\d+)\b", re.IGNORECASE),
]


def _extract_lesson_hint(query: str) -> Optional[int]:
    """Extracts an explicit lesson number from the student's query, if present.
    
    Examples:
        'tópicos da aula 3' → 3
        'o que foi visto na lição 2?' → 2
        'explique XYZ' → None
    """
    for pattern in _LESSON_PATTERNS:
        match = pattern.search(query)
        if match:
            return int(match.group(1))
    return None


async def retrieve_context(
    teacher_id: str,
    course_id: str,
    subject_name: str | None,
    current_lesson_number: int,
    query: str,
    top_k: int | None = None,
    score_threshold: float | None = None
) -> str:
    top_k = top_k or settings.RAG_TOP_K
    score_threshold = score_threshold or settings.RAG_SCORE_THRESHOLD
    """Retrieves relevant document context from Qdrant using semantic search.
    
    Uses a two-stage strategy when the student explicitly mentions a lesson number:
      1. First searches ONLY within the requested lesson (pinpointed)
      2. Falls back to a broader search (all lessons up to current) if pinpointed 
         results are insufficient
    
    This prevents generic queries like "tópicos da aula 3" from being drowned out
    by semantically closer but irrelevant content from other lessons.
    """
    await init_qdrant_collection("documents")
    qdrant = await get_qdrant()
    embedder = get_embedder(settings.DEFAULT_EMBEDDER_PROVIDER)
    
    query_vector = await embedder.aembed_query(query)

    # --- Stage 1: Pinpointed search if student mentions a specific lesson ---
    lesson_hint = _extract_lesson_hint(query)
    results = []

    # Se o aluno não especificou uma aula, assumimos que ele quer saber da aula ATUAL.
    # Isso evita que termos genéricos ("essa aula") tragam resultados de aulas antigas.
    target_lesson = lesson_hint if lesson_hint and lesson_hint <= current_lesson_number else current_lesson_number

    logger.info(
        "Retriever target lesson strategy",
        lesson_hint=lesson_hint,
        target_lesson=target_lesson,
        current_lesson=current_lesson_number
    )
    
    # Condições base de isolamento (sempre teacher + curso; disciplina quando houver).
    # O filtro por subject_name isola o RAG na disciplina correta dentro do curso.
    base_conditions = [
        FieldCondition(key="teacher_id", match=MatchValue(value=teacher_id)),
        FieldCondition(key="course_id",  match=MatchValue(value=course_id)),
    ]
    if subject_name:
        base_conditions.append(FieldCondition(key="subject_name", match=MatchValue(value=subject_name)))

    pinpoint_filter = Filter(
        must=base_conditions + [
            FieldCondition(key="lesson_number", match=MatchValue(value=target_lesson)),
        ]
    )
    results = await qdrant.search(
        collection_name="documents",
        query_vector=query_vector,
        query_filter=pinpoint_filter,
        limit=top_k,
        score_threshold=score_threshold,
    )
    logger.info(f"Pinpointed search returned {len(results)} results for lesson {target_lesson}")

    # --- Stage 2: Broad search (fallback or default) ---
    # Se a aula específica não tiver resultados suficientes (ex: pergunta sobre aula passada sem citar o número),
    # abrimos a busca para todo o histórico do aluno.
    if len(results) < 3:
        logger.info("Not enough pinpointed results. Falling back to broad search.")
        broad_filter = Filter(
            must=base_conditions + [
                FieldCondition(
                    key="lesson_number",
                    range=Range(lte=current_lesson_number)
                ),
            ]
        )
        broad_results = await qdrant.search(
            collection_name="documents",
            query_vector=query_vector,
            query_filter=broad_filter,
            limit=top_k,
            score_threshold=score_threshold,
        )
        
        # Merge: pinpointed results first, then fill with broad results (no duplicates)
        seen_ids = {r.id for r in results}
        for hit in broad_results:
            if hit.id not in seen_ids and len(results) < top_k:
                results.append(hit)
                seen_ids.add(hit.id)

    # Debug log — critical for understanding what is actually being retrieved
    for hit in results:
        chunk_idx = hit.payload.get("chunk_index", "?")
        total    = hit.payload.get("total_chunks", "?")
        lesson   = hit.payload.get("lesson_number", "?")
        preview  = (hit.payload.get("content") or "")[:80].replace("\n", " ")
        logger.info(
            "RAG hit",
            score=f"{hit.score:.3f}",
            lesson=lesson,
            chunk=f"{chunk_idx}/{total}",
            preview=preview
        )

    if not results:
        return ""

    context_chunks = []
    for hit in results:
        payload = hit.payload
        if payload and "content" in payload:
            source = payload.get("source", "Unknown")
            lesson_num = payload.get("lesson_number", "?")
            context_chunks.append(f"[Fonte: {source} | Material da Aula: {lesson_num}]\n{payload['content']}")

    return "\n\n".join(context_chunks)