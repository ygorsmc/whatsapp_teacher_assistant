import asyncio
import uuid
import structlog
from sqlalchemy import text
from tenacity import retry, wait_exponential, stop_after_attempt
from src.shared.config import settings
from src.db.database import AsyncSessionLocal
from src.drive.factory import get_material_source
from src.rag.qdrant_client import get_qdrant, init_qdrant_collection, get_collection_mode
from src.rag.text_splitter import (
    StructuredChunk,
    assemble_structural_chunks,
    extract_heading_outline,
)
from src.llm.factory import get_embedder
from src.llm.interfaces import HybridEmbeddingProvider
from qdrant_client.models import PointStruct, SparseVector

logger = structlog.get_logger(__name__)


def build_document_excerpt(full_text: str, raw_paragraphs: list[str], max_chars: int | None = None) -> str:
    """Excerto do documento para o enriquecimento contextual (F2): início do
    texto + sumário de headings, com teto de CONTEXTUAL_MAX_DOC_CHARS.

    Este excerto é o PREFIXO FIXO do prompt entre todas as chamadas do mesmo
    arquivo — é ele que o context caching do provider reaproveita (-69% do
    custo de anotação segundo o relatório)."""
    max_chars = max_chars or settings.CONTEXTUAL_MAX_DOC_CHARS
    outline = extract_heading_outline(raw_paragraphs) if raw_paragraphs else []
    outline_block = ""
    if outline:
        outline_block = "\n\nSUMÁRIO DE SEÇÕES:\n" + "\n".join(f"- {h}" for h in outline)
        # O sumário nunca passa de metade do teto — garante espaço para o início.
        outline_block = outline_block[: max_chars // 2]
    head_budget = max_chars - len(outline_block)
    return full_text[:head_budget] + outline_block


async def _enrich_chunks_with_context(
    chunks: list[StructuredChunk],
    document_excerpt: str,
    file_name: str,
) -> list[str | None]:
    """F2 — Contextual Retrieval: o LLM situa cada chunk no documento ANTES da
    vetorização. Best-effort POR CHUNK: falha → chunk cru (None). Custo logado
    por arquivo (FinOps)."""
    from src.llm.factory import get_provider
    from src.shared.constants.prompts import CONTEXTUAL_ENRICHMENT_PROMPT

    llm = get_provider()
    prefixes: list[str | None] = []
    prompt_chars = 0
    output_chars = 0
    failures = 0

    for chunk in chunks:
        try:
            prompt = CONTEXTUAL_ENRICHMENT_PROMPT.format(
                document_excerpt=document_excerpt,
                chunk=chunk.text,
            )
            prompt_chars += len(prompt)
            context_prefix = (await llm.agenerate(prompt=prompt)).strip()
            # Saída esperada: 1-2 frases. Resposta anômala → chunk cru.
            if not context_prefix or len(context_prefix) > 600:
                prefixes.append(None)
                failures += 1
                continue
            output_chars += len(context_prefix)
            prefixes.append(context_prefix)
        except Exception as e:
            failures += 1
            prefixes.append(None)
            logger.warning("contextual_enrichment_chunk_failed", error=str(e)[:150])

    logger.info(
        "contextual_enrichment_cost",
        file=file_name,
        chunks=len(chunks),
        enriched=len(chunks) - failures,
        failed=failures,
        prompt_chars=prompt_chars,
        output_chars=output_chars,
        approx_prompt_tokens=prompt_chars // 4,
    )
    return prefixes

async def process_file(file_id: str, file_name: str, mime_type: str, teacher_id: str, course_id: str, subject_name: str, lesson_number: int, material_type: str = "lesson"):
    """
    Downloads a file from Google Drive, parses it (via factory parsers),
    embeds chunks with Gemini, and upserts to Qdrant.

    material_type ('lesson' | 'book' | 'workbook') é gravado no payload de cada
    vetor — é ele que permite ao RAG isolar a busca por tipo de material
    (Addon de Livros). Para 'book'/'workbook', lesson_number = capítulo/seção.
    """
    logger.info(f"Starting ingestion for file: {file_name} ({mime_type})")
    
    # 1. Download from Google Drive (com retry para proteger contra SSL cold-start)
    try:
        @retry(wait=wait_exponential(multiplier=2, min=3, max=30), stop=stop_after_attempt(3), reraise=True)
        async def _download_with_retry():
            return await get_material_source().download(file_id)
        
        file_bytes = await _download_with_retry()
        logger.info(f"Downloaded {len(file_bytes)} bytes from Drive.")
    except Exception as e:
        logger.error("Failed to download from Drive after 3 retries", error=str(e))
        return False

    full_text = ""
    raw_paragraphs = []
    
    # --- 2. ROTEADOR DE EXTRATORES ---
    is_office = "wordprocessingml" in mime_type.lower() or "presentationml" in mime_type.lower() or file_name.endswith(('.docx', '.pptx'))
    is_pdf = "pdf" in mime_type.lower() or file_name.endswith('.pdf')
    is_text = "markdown" in mime_type.lower() or "text/plain" in mime_type.lower() or file_name.endswith(('.md', '.txt'))

    if is_text:
        # Markdown/TXT: leitura direta dos bytes (sem parser/Gotenberg).
        full_text = file_bytes.decode("utf-8", errors="ignore")
    elif is_pdf or is_office:
        pdf_bytes = file_bytes

        if is_office:
            import httpx
            logger.info(f"Converting {file_name} to PDF via Gotenberg...")
            try:
                async with httpx.AsyncClient() as client:
                    files = {'files': (file_name, file_bytes)}
                    resp = await client.post(f"{settings.GOTENBERG_URL}/forms/libreoffice/convert", files=files, timeout=120.0)
                    resp.raise_for_status()
                    pdf_bytes = resp.content
                logger.info("Gotenberg conversion successful.")
            except Exception as e:
                logger.error("Gotenberg conversion failed", error=str(e))
                return False

        # --- COMPRESSÃO FINOPS: PARSER ABSTRAÍDO ---
        from src.parsing.factory import get_parser
        from src.math.routing import choose_parser_backend, needs_density_probe

        # M2: usa o parser ciente de matemática SÓ onde vale (disciplina de
        # exatas pelo nome, tag [exatas], ou documento math-denso). Flag OFF →
        # math_backend None → get_parser(None) = parser padrão (comportamento atual).
        probe_text = None
        if needs_density_probe(subject_name):
            try:
                probe_blocks = await get_parser("local").parse(blob_url="", file_bytes=pdf_bytes, filename=file_name)
                probe_text = "\n".join(probe_blocks)[:8000]
            except Exception as e:
                logger.warning("math_density_probe_failed", error=str(e)[:150])
        math_backend = choose_parser_backend(subject_name, probe_text=probe_text)
        if math_backend:
            logger.info("math_parser_selected", subject_name=subject_name, backend=math_backend)
        parser = get_parser(math_backend)
        
        try:
            raw_paragraphs = await parser.parse(blob_url="", file_bytes=pdf_bytes, filename=file_name)
            full_text = "\n\n".join(raw_paragraphs)
        except Exception as e:
            logger.error("Parsing failed", error=str(e))
            return False
        
    # 3. Chunking estrutural (F1): headings = fronteiras + section_title;
    #    pares regra↔exceção nunca se separam (teto duro de 1.5×).
    if not raw_paragraphs:
        # Markdown/TXT: pseudo-parágrafos por linha em branco (headings "#"
        # do markdown são detectados pelo assembler).
        raw_paragraphs = [p for p in full_text.split("\n\n") if p.strip()]

    chunks: list[StructuredChunk] = assemble_structural_chunks(
        paragraphs=raw_paragraphs,
        chunk_size=settings.RAG_CHUNK_SIZE,
        chunk_overlap=settings.RAG_CHUNK_OVERLAP,
    )

    # Remove duplicates
    seen = set()
    unique_chunks = []
    for c in chunks:
        key = c.text[:80]
        if key not in seen and len(c.text.strip()) > 30:
            seen.add(key)
            unique_chunks.append(c)
    chunks = unique_chunks

    logger.info(f"Chunked document into {len(chunks)} chunks.")

    # Sem texto extraível: pula sem quebrar (upsert vazio dá erro no Qdrant).
    if not chunks:
        logger.warning(f"No extractable text in {file_name} — skipping (0 chunks).")
        return False

    # 3.5 Contextual Retrieval (F2, flag): o LLM situa cada chunk no documento.
    #     O texto contextualizado alimenta o embedding E o braço esparso
    #     (Contextual BM25) — o payload guarda o `content` CRU (o que o aluno
    #     vê) e o `context_prefix` separado.
    context_prefixes: list[str | None] = [None] * len(chunks)
    if settings.CONTEXTUAL_RETRIEVAL_ENABLED:
        document_excerpt = build_document_excerpt(full_text, raw_paragraphs)
        context_prefixes = await _enrich_chunks_with_context(chunks, document_excerpt, file_name)

    embedding_texts = [
        f"{prefix}\n{chunk.text}" if prefix else chunk.text
        for chunk, prefix in zip(chunks, context_prefixes)
    ]

    # 4. Embeddings — híbrido-aware (F3): provider híbrido (BGE-M3) gera dense
    #    + sparse em 1 passo; senão dense do provider atual + sparse local
    #    (fastembed, Plano B). Sem braço esparso disponível → dense-only.
    await init_qdrant_collection(settings.QDRANT_COLLECTION)
    try:
        collection_mode = await get_collection_mode(settings.QDRANT_COLLECTION)
    except Exception:
        from src.rag.qdrant_client import CollectionMode
        collection_mode = CollectionMode(named_dense=False, has_sparse=False)

    embedder = get_embedder(settings.DEFAULT_EMBEDDER_PROVIDER)
    embeddings: list[list[float]] = []
    sparse_embeddings: list[dict | None] = [None] * len(chunks)
    try:
        if collection_mode.has_sparse and isinstance(embedder, HybridEmbeddingProvider):
            for i, text_to_embed in enumerate(embedding_texts):
                dense_batch, sparse_batch = await embedder.aembed_documents_hybrid([text_to_embed])
                embeddings.append(dense_batch[0])
                sparse_embeddings[i] = sparse_batch[0]
                if (i + 1) % 5 == 0:
                    logger.info(f"Embedded {i + 1}/{len(chunks)} chunks (hybrid)...")
        else:
            for i, text_to_embed in enumerate(embedding_texts):
                result = await embedder.aembed_documents([text_to_embed])
                embeddings.append(result[0])
                if (i + 1) % 5 == 0:
                    logger.info(f"Embedded {i + 1}/{len(chunks)} chunks...")
            if collection_mode.has_sparse:
                from src.llm.bge_m3 import encode_sparse_fallback
                encoded = await encode_sparse_fallback(embedding_texts)
                if encoded:
                    sparse_embeddings = list(encoded)
    except Exception as e:
        logger.error("Embedding generation failed", error=str(e))
        return False

    # 5. Upsert to Qdrant
    qdrant = await get_qdrant()

    # 5.1 Remove vetores antigos para este arquivo (evita lixo se o novo PDF for menor que o antigo)
    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        await qdrant.delete(
            collection_name=settings.QDRANT_COLLECTION,
            points_selector=Filter(
                must=[FieldCondition(key="source", match=MatchValue(value=file_name))]
            )
        )
    except Exception as e:
        logger.warning(f"Could not delete old vectors for {file_name}", error=str(e))

    points = []

    for idx, (chunk, vector) in enumerate(zip(chunks, embeddings)):
        # Usa uuid5 determinístico para garantir idempotência exata
        vector_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"whatsapp-bot://{file_id}/{idx}"))

        payload = {
            "teacher_id": teacher_id,
            "course_id": course_id,
            "subject_name": subject_name,
            "lesson_number": lesson_number,
            "material_type": material_type,
            "content": chunk.text,
            "source": file_name,
            # file_id do Drive (único e estável) — permite ao /delete apagar os
            # chunks deste arquivo com precisão, à prova de homônimos.
            "file_id": file_id,
            "chunk_index": idx,
            "total_chunks": len(chunks),
            # F1: rastreabilidade estrutural — o contexto cita a seção.
            "section_title": chunk.section_title,
            "char_count": len(chunk.text),
        }
        if context_prefixes[idx]:
            payload["context_prefix"] = context_prefixes[idx]

        if collection_mode.named_dense:
            vector_data: dict = {"dense": vector}
            sparse = sparse_embeddings[idx]
            if collection_mode.has_sparse and sparse and sparse.get("indices"):
                vector_data["sparse"] = SparseVector(indices=sparse["indices"], values=sparse["values"])
            points.append(PointStruct(id=vector_id, vector=vector_data, payload=payload))
        else:
            points.append(PointStruct(id=vector_id, vector=vector, payload=payload))

    await qdrant.upsert(
        collection_name=settings.QDRANT_COLLECTION,
        points=points
    )

    # F7: medidor de corpus do escopo (best-effort — decide o bypass long-context).
    try:
        from src.rag.corpus import update_corpus_size
        await update_corpus_size(teacher_id, course_id, subject_name, material_type)
    except Exception as e:
        logger.warning("corpus_meter_update_failed", error=str(e)[:200])

    logger.info(f"✅ Successfully upserted {len(points)} vectors to Qdrant for {file_name}")
    return True


async def invalidate_quiz_cache_for_lesson(course_id: str, subject_name: str | None, lesson_number: int, material_type: str = "lesson"):
    """CICLO DE VIDA DO CACHE (Lazy Invalidation).

    Sempre que um PDF novo/alterado de uma aula é processado com sucesso, o
    material base mudou — as questões antigas do QuizBank podem estar
    desatualizadas. Disparamos:

      1. `DELETE FROM "QuizBank" WHERE course_id = X AND lesson_number = Y`
         (escopado pela Disciplina E pelo material_type, para não atingir
         outra disciplina do mesmo curso nem o material errado — atualizar o
         Cap. 3 do livro não pode apagar os quizzes da Aula 3). O histórico de
         respostas dos alunos sobrevive (PendingQuizzes.quiz_bank_id tem
         ON DELETE SET NULL). As revisões espaçadas agendadas dessas questões
         morrem junto (SpacedRepetitionQueue tem ON DELETE CASCADE) — não
         existe como reentregar uma questão apagada.
      2. Invalidação da lista de tópicos da aula no Redis — o próximo pedido
         de quiz forçará o LLM a reler o novo RAG e recriar tópicos e questões.

    Falhas aqui NUNCA quebram a ingestão (best-effort com log).
    """
    try:
        from src.rag.quiz_bank import delete_lesson_quizzes
        from src.cache.topic_cache import invalidate_cached_topics

        deleted = await delete_lesson_quizzes(
            course_id=course_id,
            lesson_number=lesson_number,
            subject_name=subject_name,
            material_type=material_type,
        )
        await invalidate_cached_topics(course_id, lesson_number, subject_name, material_type)

        logger.info(
            "quiz_cache_invalidated_after_ingestion",
            course_id=course_id,
            subject_name=subject_name,
            lesson_number=lesson_number,
            material_type=material_type,
            quiz_bank_deleted=deleted,
        )
    except Exception as e:
        logger.error("quiz_cache_invalidation_failed", error=str(e)[:300])


async def sync_google_drive():
    """Polls the Google Drive folder for new files to ingest."""
    folder_id = settings.GOOGLE_DRIVE_FOLDER_ID
    if not folder_id:
        logger.warning("GOOGLE_DRIVE_FOLDER_ID is not set in config.")
        return
        
    logger.info(f"Checking Google Drive folder: {folder_id}")
    try:
        files = await get_material_source().list_materials(folder_id)
    except Exception as e:
        logger.error("Failed to list Google Drive files", error=str(e))
        return
    
    if not files:
        logger.info("No files found in the folder.")
        return
    
    logger.info(f"Found {len(files)} files in Google Drive.")
        
    await init_qdrant_collection(settings.QDRANT_COLLECTION)

    async with AsyncSessionLocal() as session:
        docs_before = (await session.execute(text("SELECT count(*) FROM file_sync_state WHERE status='concluído'"))).scalar()

        enqueued_count = 0
        skipped_count = 0
        
        for f in files:
            file_id = f.id
            file_name = f.name
            mime_type = f.mime_type
            course_name = f.course_name
            subject_name = f.subject_name
            lesson_number = f.lesson_number
            material_type = f.material_type

            if not course_name:
                logger.debug(f"Skipping '{file_name}': no course_name")
                continue
            if not subject_name:
                logger.warning(f"File '{file_name}' fora de uma pasta de Disciplina (Curso > Disciplina > Aula). Pulando.")
                continue
                
            result = await session.execute(
                text('SELECT id, teacher_id FROM \"Courses\" WHERE name = :name LIMIT 1'), 
                {"name": course_name}
            )
            course_row = result.fetchone()
            
            if not course_row:
                logger.warning(f"Course '{course_name}' not found in DB. Skipping file '{file_name}'.")
                continue
                
            course_id = course_row[0]
            teacher_id = course_row[1]
            
            # Check if we already processed this file successfully
            result = await session.execute(
                text("SELECT status FROM file_sync_state WHERE file_id = :fid"),
                {"fid": file_id}
            )
            row = result.fetchone()
            
            # 'excluído' = material removido por /delete — a sync NÃO o ressuscita
            # (o professor o apagou de propósito; reaparece só se re-adicionado).
            if row and row[0] in ('concluído', 'dead_letter', 'excluído'):
                skipped_count += 1
                continue
            
            logger.info(f"Enqueuing file '{file_name}' (current status: {row[0] if row else 'new'})")
                
            # Upsert state as 'na_fila'
            await session.execute(
                text("""
                    INSERT INTO file_sync_state (file_id, teacher_id, status, processing_started_at)
                    VALUES (:fid, :tid, 'na_fila', CURRENT_TIMESTAMP)
                    ON CONFLICT(file_id) DO UPDATE SET status='na_fila', processing_started_at=CURRENT_TIMESTAMP
                """),
                {"fid": file_id, "tid": teacher_id}
            )
            await session.commit()
            
            # Enqueue task
            from src.queue.factory import get_queue
            await get_queue().enqueue(
                queue="ingestion_queue",
                payload={
                    "file_id": file_id,
                    "file_name": file_name,
                    "mime_type": mime_type,
                    "teacher_id": teacher_id,
                    "course_id": course_id,
                    "subject_name": subject_name,
                    "lesson_number": lesson_number,
                    "material_type": material_type
                }
            )
            enqueued_count += 1
        
        logger.info(f"Sync complete: {enqueued_count} enqueued, {skipped_count} skipped (already concluído)")
        docs_after = (await session.execute(text("SELECT count(*) FROM file_sync_state WHERE status='concluído'"))).scalar()
        return {"docs_before": docs_before, "docs_after": docs_after}

async def handle_ingestion_message(payload: dict, context):
    """Consumes an ingestion task from the queue."""
    try:
        file_id = payload.get("file_id")
        file_name = payload.get("file_name")
        mime_type = payload.get("mime_type")
        teacher_id = payload.get("teacher_id")
        course_id = payload.get("course_id")
        subject_name = payload.get("subject_name")
        lesson_number = payload.get("lesson_number")
        # Mensagens antigas na fila (pré-Addon) não trazem material_type.
        material_type = payload.get("material_type", "lesson")

        async with AsyncSessionLocal() as session:
            # Mark as processing
            await session.execute(
                text("UPDATE file_sync_state SET status = 'processando', processing_started_at = CURRENT_TIMESTAMP WHERE file_id = :fid"),
                {"fid": file_id}
            )
            await session.commit()
            
            # Campos do payload são garantidos pelo enfileirador (sync do Drive);
            # .get() devolve Any|None — o contrato real é validado upstream.
            success = await process_file(file_id, file_name, mime_type, teacher_id, course_id, subject_name, lesson_number, material_type)  # type: ignore[arg-type]
            
            if success:
                await session.execute(
                    text("UPDATE file_sync_state SET status = 'concluído', attempts = 0, last_synced = CURRENT_TIMESTAMP WHERE file_id = :fid"),
                    {"fid": file_id}
                )
            else:
                # Increment attempts; after 3 failures, mark as dead_letter to stop retries
                await session.execute(
                    text("""
                        UPDATE file_sync_state
                        SET status = CASE WHEN COALESCE(attempts, 0) + 1 >= 3 THEN 'dead_letter' ELSE 'falhou' END,
                            attempts = COALESCE(attempts, 0) + 1,
                            last_synced = CURRENT_TIMESTAMP
                        WHERE file_id = :fid
                    """),
                    {"fid": file_id}
                )
            await session.commit()

        # ── CICLO DE VIDA DO CACHE ──────────────────────────────────────────
        # Material novo/atualizado processado com sucesso → invalida o
        # QuizBank (course_id + disciplina + lesson_number) e a lista de
        # tópicos no Redis. Roda FORA da sessão acima (best-effort).
        if success and course_id and lesson_number is not None:
            await invalidate_quiz_cache_for_lesson(
                course_id=course_id,
                subject_name=subject_name,
                lesson_number=int(lesson_number),
                material_type=material_type,
            )

        await context.complete()
    except Exception as e:
        logger.error("ingestion_worker_failed", error=str(e), exc_info=True)
        await context.dead_letter(reason="IngestionError", description=str(e)[:500])

async def drive_polling_loop():
    logger.info("Starting Google Drive polling loop...")
    while True:
        try:
            await sync_google_drive()
        except Exception as e:
            logger.error("Error in ingestion sync loop", error=str(e))
        await asyncio.sleep(300)

async def worker_loop():
    logger.info("Starting Ingestion Worker loop (Queue Consumer)...")
    
    # Start the background polling task
    asyncio.create_task(drive_polling_loop())
    
    from src.queue.factory import get_queue
    queue = get_queue()
    await queue.consume_loop("ingestion_queue", handle_ingestion_message)

if __name__ == "__main__":
    asyncio.run(worker_loop())
