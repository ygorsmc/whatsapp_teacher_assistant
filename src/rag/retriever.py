"""
Retriever — pipeline de recuperação de contexto de alta precisão.

Funil (camadas atrás de flags; TUDO desligado = comportamento clássico):

  query → roteador determinístico (F6) → busca [dense | híbrida+RRF (F3)]
        → 2 estágios (pinpoint da aula citada → broad até o ponteiro)
        → expansão por vínculo Aula↔Capítulo (F6, grafo-lite no Postgres)
        → reranking cross-encoder (F4, juiz do funil)
        → CRAG-lite 🟢🟡🔴 (F5: reescrita única / Recusa Honesta)

Regras invioláveis:
  - Filtros de escopo (teacher_id + course_id + disciplina/material) SEMPRE
    antes da similaridade — em TODOS os braços e estágios.
  - Fallback sempre: híbrido sem sparse → dense; reranker fora do ar → ordem
    da fusão; CRAG sem score → fluxo normal. NUNCA derruba a resposta.
"""

from dataclasses import dataclass, field
from typing import Optional

import structlog
from qdrant_client.models import (
    FieldCondition,
    Filter,
    Fusion,
    FusionQuery,
    MatchAny,
    MatchValue,
    Prefetch,
    Range,
    SparseVector,
)

from src.llm.factory import get_embedder
from src.llm.interfaces import HybridEmbeddingProvider
from src.rag.qdrant_client import (
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
    get_collection_mode,
    get_qdrant,
    init_qdrant_collection,
)
from src.rag.query_router import RouteResult, route_query, sparse_query_text
from src.shared.config import settings

logger = structlog.get_logger(__name__)

_qdrant_initialized = False


def _extract_lesson_hint(query: str) -> Optional[int]:
    """Extracts an explicit lesson number from the student's query, if present.

    Examples:
        'tópicos da aula 3' → 3
        'o que foi visto na lição 2?' → 2
        'explique XYZ' → None
    """
    route = route_query(query)
    return route.lesson_ref or route.chapter_ref


@dataclass
class RetrievedHit:
    """Um chunk recuperado, com os scores de cada estágio do funil."""
    id: str
    score: float                          # cosseno (dense) ou score RRF (híbrido)
    payload: dict
    rerank_score: Optional[float] = None  # score do juiz (F4) — alimenta o CRAG


@dataclass
class RetrievalResult:
    """Resultado completo da recuperação — contexto + sinais de evidência."""
    hits: list[RetrievedHit] = field(default_factory=list)
    context: str = ""
    query_used: str = ""
    best_score: Optional[float] = None    # melhor score do RERANKER (None = juiz off)
    rerank_applied: bool = False
    evidence: str = "off"                 # green | yellow | red | off (CRAG F5)
    abstained: bool = False
    used_rewrite: bool = False
    bypassed_rag: bool = False            # F7: corpus inteiro no prompt


async def _ensure_collection():
    global _qdrant_initialized
    if not _qdrant_initialized:
        await init_qdrant_collection(settings.QDRANT_COLLECTION)
        _qdrant_initialized = True


async def _embed_query(query: str, route: RouteResult, want_sparse: bool) -> tuple[list[float], Optional[dict]]:
    """Vetores da query. Híbrido (BGE-M3): 1 passo devolve dense+sparse — com
    referência exata detectada, o texto vai REFORÇADO (termos literais
    repetidos pesam no braço lexical). Plano B: sparse local via fastembed.
    Falha de qualquer braço esparso degrada para dense-only (fallback sempre).
    """
    embedder = get_embedder(settings.DEFAULT_EMBEDDER_PROVIDER)

    if want_sparse and isinstance(embedder, HybridEmbeddingProvider):
        try:
            hybrid_dense, hybrid_sparse = await embedder.aembed_query_hybrid(sparse_query_text(query, route))
            if hybrid_sparse and hybrid_sparse.get("indices"):
                return hybrid_dense, hybrid_sparse
            return hybrid_dense, None
        except Exception as e:
            logger.warning("hybrid_query_embedding_failed_falling_back_dense", error=str(e)[:200])

    dense = await embedder.aembed_query(query)
    sparse: Optional[dict] = None
    if want_sparse:
        try:
            from src.llm.bge_m3 import encode_sparse_fallback
            encoded = await encode_sparse_fallback([sparse_query_text(query, route)])
            if encoded and encoded[0].get("indices"):
                sparse = encoded[0]
        except Exception as e:
            logger.warning("sparse_query_encoding_failed", error=str(e)[:200])
    return dense, sparse


async def _search_stage(
    qdrant,
    named_dense: bool,
    dense_vector: list[float],
    sparse_vector: Optional[dict],
    query_filter: Filter,
    limit: int,
    score_threshold: Optional[float],
) -> list:
    """Uma rodada de busca com escopo. Híbrido (sparse presente): fusão RRF
    server-side com o MESMO filtro nos dois braços. Senão: dense puro."""
    collection = settings.QDRANT_COLLECTION

    if sparse_vector is not None:
        try:
            response = await qdrant.query_points(
                collection_name=collection,
                prefetch=[
                    Prefetch(
                        query=dense_vector,
                        using=DENSE_VECTOR_NAME,
                        filter=query_filter,
                        limit=limit,
                        score_threshold=score_threshold,
                    ),
                    Prefetch(
                        query=SparseVector(
                            indices=sparse_vector["indices"],
                            values=sparse_vector["values"],
                        ),
                        using=SPARSE_VECTOR_NAME,
                        filter=query_filter,
                        limit=limit,
                    ),
                ],
                query=FusionQuery(fusion=Fusion.RRF),
                limit=limit,
                with_payload=True,
            )
            return list(response.points)
        except Exception as e:
            logger.warning("hybrid_search_failed_falling_back_dense", error=str(e)[:200])

    response = await qdrant.query_points(
        collection_name=collection,
        query=dense_vector,
        using=DENSE_VECTOR_NAME if named_dense else None,
        query_filter=query_filter,
        limit=limit,
        score_threshold=score_threshold,
        with_payload=True,
    )
    return list(response.points)


async def _find_linked_scope(
    course_id: str,
    subject_name: Optional[str],
    lesson_number: int,
    from_material: Optional[str],
) -> Optional[tuple[str, int]]:
    """Expansão por vínculo (F6 — grafo-lite): LessonChapterLinks no Postgres.

    Fluxo de aulas (from_material None/'lesson'): aula N → ('book', cap M).
    Fluxo de livro: capítulo M → ('lesson', aula N). Best-effort (None = sem
    vínculo ou PG indisponível — a busca segue sem a expansão).
    """
    try:
        from sqlalchemy import text as sql_text
        from src.db.database import AsyncSessionLocal

        is_book_flow = from_material in ("book", "workbook")
        source_col = "chapter_number" if is_book_flow else "lesson_number"
        target_col = "lesson_number" if is_book_flow else "chapter_number"

        subject_clause = (
            "s.name = :sname" if subject_name else "lcl.subject_id IS NULL"
        )
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                sql_text(f'''
                    SELECT lcl.{target_col}
                    FROM "LessonChapterLinks" lcl
                    LEFT JOIN "Subjects" s ON s.id = lcl.subject_id
                    WHERE lcl.course_id = :cid AND lcl.{source_col} = :num
                      AND {subject_clause}
                    LIMIT 1
                '''),
                {"cid": course_id, "num": lesson_number,
                 **({"sname": subject_name} if subject_name else {})},
            )
            target = res.scalar_one_or_none()
        if target is None:
            return None
        return ("lesson" if is_book_flow else "book", int(target))
    except Exception as e:
        logger.warning("linked_scope_lookup_failed", error=str(e)[:200])
        return None


def _build_context(hits: list[RetrievedHit]) -> str:
    """Formata os chunks com citação rastreável (fonte | aula | seção).
    Reordena cronologicamente (por arquivo e índice) para garantir que o LLM 
    leia o material na sequência pedagógica correta."""
    
    def get_sort_key(hit: RetrievedHit):
        payload = hit.payload or {}
        source = payload.get("source", "")
        chunk_idx = payload.get("chunk_index", 0)
        return (source, chunk_idx)
        
    sorted_hits = sorted(hits, key=get_sort_key)
    
    blocks = []
    for hit in sorted_hits:
        payload = hit.payload or {}
        if "content" not in payload:
            continue
        source = payload.get("source", "Unknown")
        lesson_num = payload.get("lesson_number", "?")
        section_title = payload.get("section_title")
        section_part = f" | Seção: {section_title}" if section_title else ""
        blocks.append(f"[Fonte: {source} | Material da Aula: {lesson_num}{section_part}]\n{payload['content']}")
    return "\n\n".join(blocks)


def _judge_text(payload: dict) -> str:
    """Texto que o cross-encoder julga: o contexto situacional (F2) ajuda o
    juiz tanto quanto ajudou o embedding."""
    content = payload.get("content") or ""
    prefix = payload.get("context_prefix")
    return f"{prefix}\n{content}" if prefix else content


async def retrieve(
    teacher_id: str,
    course_id: str,
    subject_name: Optional[str],
    current_lesson_number: int,
    query: str,
    top_k: int | None = None,
    score_threshold: float | None = None,
    material_type: Optional[str] = None,
    allowed_sections: Optional[list[int]] = None,
    apply_rerank: bool = True,
    use_hybrid: bool | None = None,
) -> RetrievalResult:
    """Executa o funil de recuperação (sem CRAG — ver retrieve_for_chat).

    Estratégia em 2 estágios preservada do retriever clássico:
      1. Pinpoint: SÓ a aula citada (ou a atual) — referência exata detectada
         pelo roteador torna o pinpoint obrigatório.
      2. Broad: todas as aulas até o ponteiro, se o pinpoint render < 3 hits.

    Addon de Livros: material_type restringe ao material; allowed_sections é
    o Modo Professor Curador; current_lesson_number é o teto anti-spoiler.
    """
    top_k = top_k or settings.RAG_TOP_K
    base_threshold = score_threshold or settings.RAG_SCORE_THRESHOLD

    await _ensure_collection()
    qdrant = await get_qdrant()

    # Layout da collection decide o que é possível (fallback sempre).
    try:
        mode = await get_collection_mode(settings.QDRANT_COLLECTION)
    except Exception:
        from src.rag.qdrant_client import CollectionMode
        mode = CollectionMode(named_dense=False, has_sparse=False)

    hybrid_requested = settings.HYBRID_SEARCH_ENABLED if use_hybrid is None else use_hybrid
    hybrid_on = hybrid_requested and mode.has_sparse

    from src.llm.reranker import get_reranker
    reranker = get_reranker() if apply_rerank else None
    rerank_on = reranker is not None

    # Funil: com juiz a jusante, busca larga (RERANK_CANDIDATES) e cosseno
    # vira pré-filtro frouxo; sem juiz, comportamento clássico (top_k/threshold).
    candidate_limit = settings.RERANK_CANDIDATES if (rerank_on or hybrid_on) else top_k
    dense_threshold = settings.RAG_PREFILTER_THRESHOLD if rerank_on else base_threshold

    route = route_query(query)
    dense_vector, sparse_vector = await _embed_query(query, route, want_sparse=hybrid_on)

    # --- Stage 1: Pinpointed search if student mentions a specific lesson ---
    lesson_hint = route.lesson_ref or route.chapter_ref
    # Se o aluno não especificou uma aula, assumimos que ele quer saber da
    # aula ATUAL (evita que termos genéricos tragam aulas antigas).
    target_lesson = lesson_hint if lesson_hint and lesson_hint <= current_lesson_number else current_lesson_number

    logger.info(
        "Retriever target lesson strategy",
        lesson_hint=lesson_hint,
        target_lesson=target_lesson,
        current_lesson=current_lesson_number,
        hybrid=hybrid_on,
        rerank=rerank_on,
        exact_terms=route.exact_terms[:5],
    )

    # Condições base de isolamento (sempre teacher + curso; disciplina quando houver).
    base_conditions: list = [
        FieldCondition(key="teacher_id", match=MatchValue(value=teacher_id)),
        FieldCondition(key="course_id", match=MatchValue(value=course_id)),
    ]
    if subject_name:
        base_conditions.append(FieldCondition(key="subject_name", match=MatchValue(value=subject_name)))
    if material_type:
        base_conditions.append(FieldCondition(key="material_type", match=MatchValue(value=material_type)))
    if allowed_sections:
        base_conditions.append(FieldCondition(key="lesson_number", match=MatchAny(any=allowed_sections)))

    pinpoint_filter = Filter(
        must=base_conditions + [
            FieldCondition(key="lesson_number", match=MatchValue(value=target_lesson)),
        ]
    )
    results = await _search_stage(
        qdrant, mode.named_dense, dense_vector, sparse_vector,
        pinpoint_filter, candidate_limit, dense_threshold,
    )
    logger.info(f"Pinpointed search returned {len(results)} results for lesson {target_lesson}")

    # --- Stage 2: Broad search (fallback or default) ---
    if len(results) < 3:
        logger.info("Not enough pinpointed results. Falling back to broad search.")
        broad_filter = Filter(
            must=base_conditions + [
                FieldCondition(key="lesson_number", range=Range(lte=current_lesson_number)),
            ]
        )
        broad_results = await _search_stage(
            qdrant, mode.named_dense, dense_vector, sparse_vector,
            broad_filter, candidate_limit, dense_threshold,
        )
        seen_ids = {r.id for r in results}
        for hit in broad_results:
            if hit.id not in seen_ids and len(results) < candidate_limit:
                results.append(hit)
                seen_ids.add(hit.id)

    # --- F6: expansão por vínculo Aula↔Capítulo (grafo-lite no Postgres) ---
    linked = await _find_linked_scope(course_id, subject_name, target_lesson, material_type)
    if linked:
        linked_material, linked_number = linked
        linked_conditions: list = [
            FieldCondition(key="teacher_id", match=MatchValue(value=teacher_id)),
            FieldCondition(key="course_id", match=MatchValue(value=course_id)),
            FieldCondition(key="material_type", match=MatchValue(value=linked_material)),
            FieldCondition(key="lesson_number", match=MatchValue(value=linked_number)),
        ]
        if subject_name:
            linked_conditions.append(FieldCondition(key="subject_name", match=MatchValue(value=subject_name)))
        linked_results = await _search_stage(
            qdrant, mode.named_dense, dense_vector, sparse_vector,
            Filter(must=linked_conditions), max(top_k, 5), dense_threshold,
        )
        seen_ids = {r.id for r in results}
        for hit in linked_results:
            if hit.id not in seen_ids and len(results) < candidate_limit:
                results.append(hit)
                seen_ids.add(hit.id)
        if linked_results:
            logger.info("linked_scope_expansion", material=linked_material, number=linked_number, added=len(linked_results))

    hits = [RetrievedHit(id=str(r.id), score=float(r.score), payload=r.payload or {}) for r in results]

    # --- F4: reranking (juiz do funil) ---
    rerank_applied = False
    best_score: Optional[float] = None
    if rerank_on and hits:
        from src.llm.reranker import rerank_with_fallback
        instruction = f"Material didático da disciplina {subject_name}" if subject_name else None
        rerank_results = await rerank_with_fallback(
            query, [_judge_text(h.payload) for h in hits],
            top_n=settings.RERANK_TOP_N, instruction=instruction, provider=reranker,
        )
        if rerank_results is not None:
            rerank_applied = True
            reordered = []
            for rr in rerank_results:
                if 0 <= rr.index < len(hits):
                    hit = hits[rr.index]
                    hit.rerank_score = rr.score
                    reordered.append(hit)
            best_score = max((h.rerank_score for h in reordered if h.rerank_score is not None), default=None)
            # O corte passa a ser o score do JUIZ (cosseno foi pré-filtro frouxo).
            hits = [h for h in reordered if (h.rerank_score or 0.0) >= settings.RERANK_SCORE_THRESHOLD][:top_k]
        else:
            hits = hits[:top_k]
    else:
        hits = hits[:top_k]

    # Debug log — critical for understanding what is actually being retrieved
    for hit in hits:
        payload = hit.payload
        preview = (payload.get("content") or "")[:80].replace("\n", " ")
        logger.info(
            "RAG hit",
            score=f"{hit.score:.3f}",
            rerank_score=f"{hit.rerank_score:.3f}" if hit.rerank_score is not None else None,
            lesson=payload.get("lesson_number", "?"),
            section=payload.get("section_title"),
            chunk=f"{payload.get('chunk_index', '?')}/{payload.get('total_chunks', '?')}",
            preview=preview,
        )

    return RetrievalResult(
        hits=hits,
        context=_build_context(hits),
        query_used=query,
        best_score=best_score,
        rerank_applied=rerank_applied,
    )


async def retrieve_for_chat(
    teacher_id: str,
    course_id: str,
    subject_name: Optional[str],
    current_lesson_number: int,
    query: str,
    top_k: int | None = None,
    material_type: Optional[str] = None,
    allowed_sections: Optional[list[int]] = None,
) -> RetrievalResult:
    """Funil completo + CRAG-lite (F5) para a CONVERSA do aluno.

    O avaliador é o score do reranker (determinístico). Sem reranker ativo (ou
    juiz fora do ar), o CRAG degrada para 'off' — nunca abstém sem evidência
    DE score (abster por palpite seria pior que responder).
    """
    from src.rag.crag import (
        EVIDENCE_GREEN,
        EVIDENCE_RED,
        EVIDENCE_YELLOW,
        classify_evidence,
        rewrite_query,
    )

    result = await retrieve(
        teacher_id=teacher_id,
        course_id=course_id,
        subject_name=subject_name,
        current_lesson_number=current_lesson_number,
        query=query,
        top_k=top_k,
        material_type=material_type,
        allowed_sections=allowed_sections,
    )

    if not settings.CRAG_LITE_ENABLED or not result.rerank_applied:
        return result

    evidence = classify_evidence(result.best_score)

    # 🟡 Evidência fraca: UMA reescrita de query (anti-loop garantido aqui —
    # a reescrita NÃO passa de novo pelo CRAG) e nova rodada de fusão+rerank.
    if evidence == EVIDENCE_YELLOW:
        rewritten = await rewrite_query(query, subject_name, current_lesson_number)
        if rewritten:
            retry = await retrieve(
                teacher_id=teacher_id,
                course_id=course_id,
                subject_name=subject_name,
                current_lesson_number=current_lesson_number,
                query=rewritten,
                top_k=top_k,
                material_type=material_type,
                allowed_sections=allowed_sections,
            )
            if retry.rerank_applied and (retry.best_score or 0.0) > (result.best_score or 0.0):
                result = retry
            result.used_rewrite = True
            evidence = classify_evidence(result.best_score)
            if evidence == EVIDENCE_GREEN:
                logger.info("crag_rewrite_recovered", best_score=result.best_score)

    if evidence == EVIDENCE_RED:
        # 🔴 Recusa Honesta: o chamador responde com o out_of_scope_message
        # enriquecido e NÃO chama o LLM gerador.
        result.abstained = True
        result.context = ""
        logger.info("crag_abstention", query=query[:120], best_score=result.best_score, used_rewrite=result.used_rewrite)

    result.evidence = evidence
    return result


async def retrieve_or_full_context(
    teacher_id: str,
    course_id: str,
    subject_name: Optional[str],
    current_lesson_number: int,
    query: str,
    top_k: int | None = None,
    material_type: Optional[str] = None,
    allowed_sections: Optional[list[int]] = None,
) -> RetrievalResult:
    """F7 — Long-Context para bases pequenas: corpus do escopo dentro do
    limite → injeta TUDO (zero risco de recuperação); senão, funil normal."""
    if settings.LONG_CONTEXT_ENABLED:
        from src.rag.corpus import build_full_context, get_corpus_size, should_bypass_rag

        corpus_chars = await get_corpus_size(course_id, subject_name, material_type)
        if should_bypass_rag(corpus_chars):
            full_context = await build_full_context(
                teacher_id=teacher_id,
                course_id=course_id,
                subject_name=subject_name,
                material_type=material_type,
                max_lesson=current_lesson_number,
                allowed_sections=allowed_sections,
            )
            if full_context:
                logger.info("rag_bypassed_long_context", chars=len(full_context))
                return RetrievalResult(
                    context=full_context,
                    query_used=query,
                    evidence="off",
                    bypassed_rag=True,
                )

    return await retrieve_for_chat(
        teacher_id=teacher_id,
        course_id=course_id,
        subject_name=subject_name,
        current_lesson_number=current_lesson_number,
        query=query,
        top_k=top_k,
        material_type=material_type,
        allowed_sections=allowed_sections,
    )


async def retrieve_context(
    teacher_id: str,
    course_id: str,
    subject_name: str | None,
    current_lesson_number: int,
    query: str,
    top_k: int | None = None,
    score_threshold: float | None = None,
    material_type: str | None = None,
    allowed_sections: list[int] | None = None,
) -> str:
    """Retrieves relevant document context from Qdrant using semantic search.

    Interface clássica (string) — usada pelo review_generator e pelo Addon de
    Livros. Passa pelo MESMO funil (híbrido/rerank conforme flags), mas sem
    CRAG (abstenção é decisão do fluxo de conversa, não dos geradores).

    Addon de Livros:
      - material_type: 'book' | 'workbook' | 'lesson' | None (sem filtro).
      - allowed_sections: Modo Professor Curador (capítulos ativos da turma).
    """
    result = await retrieve(
        teacher_id=teacher_id,
        course_id=course_id,
        subject_name=subject_name,
        current_lesson_number=current_lesson_number,
        query=query,
        top_k=top_k,
        score_threshold=score_threshold,
        material_type=material_type,
        allowed_sections=allowed_sections,
    )
    return result.context
