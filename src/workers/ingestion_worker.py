import asyncio
import io
import uuid
import structlog
from datetime import datetime
from sqlalchemy import text
from src.shared.config import settings
from src.db.database import AsyncSessionLocal
from src.drive.client import drive_client
from src.rag.qdrant_client import get_qdrant, init_qdrant_collection
from src.llm.factory import get_embedder
from qdrant_client.models import PointStruct
from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
from src.rag.text_splitter import split_document_text
import src.llm # Init providers

logger = structlog.get_logger(__name__)

async def process_file(file_id: str, file_name: str, mime_type: str, teacher_id: str, course_id: str, lesson_number: int):
    """
    Downloads a file from Google Drive, parses it (PDF via Azure DI, others via native parsers), 
    embeds chunks with Gemini, and upserts to Qdrant.
    """
    logger.info(f"Starting ingestion for file: {file_name} ({mime_type})")
    
    # 1. Download from Google Drive
    try:
        file_bytes = await drive_client.download_file(file_id)
        logger.info(f"Downloaded {len(file_bytes)} bytes from Drive.")
    except Exception as e:
        logger.error(f"Failed to download from Drive", error=str(e))
        return False

    full_text = ""
    raw_paragraphs = []
    
    # --- 2. ROTEADOR DE EXTRATORES ---
    is_office = "wordprocessingml" in mime_type.lower() or "presentationml" in mime_type.lower() or file_name.endswith(('.docx', '.pptx'))
    is_pdf = "pdf" in mime_type.lower() or file_name.endswith('.pdf')

    if is_pdf or is_office:
        import fitz
        endpoint = settings.DOCUMENT_INTELLIGENCE_ENDPOINT
        key = settings.DOCUMENT_INTELLIGENCE_KEY
        if not endpoint or not key:
            logger.error("Azure Document Intelligence credentials missing.")
            return False

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

        # --- COMPRESSÃO FINOPS: 2-UP EMPILHADO PARA SLIDES ---
        def _compress_presentation_pdf(p_bytes: bytes) -> bytes:
            try:
                import fitz
                doc = fitz.open(stream=p_bytes, filetype="pdf")
                if len(doc) <= 1:
                    doc.close()
                    return p_bytes
                    
                first_page = doc[0]
                # Verifica se é formato paisagem (Slide)
                if first_page.rect.width > first_page.rect.height * 1.1:
                    logger.info("Formato de Apresentação detectado: Agrupando páginas 2 a 2 na vertical (FinOps).")
                    new_doc = fitz.open()
                    
                    for i in range(0, len(doc), 2):
                        p1 = doc[i]
                        p2 = doc[i+1] if i+1 < len(doc) else None
                        
                        w = p1.rect.width
                        h1 = p1.rect.height
                        h2 = p2.rect.height if p2 else 0
                        total_h = h1 + h2
                        
                        new_page = new_doc.new_page(width=w, height=total_h)
                        new_page.show_pdf_page(fitz.Rect(0, 0, w, h1), doc, p1.number)
                        
                        if p2:
                            new_page.show_pdf_page(fitz.Rect(0, h1, w, total_h), doc, p2.number)
                            
                    compressed_bytes = new_doc.tobytes()
                    doc.close()
                    new_doc.close()
                    return compressed_bytes
                else:
                    doc.close()
                    return p_bytes
            except Exception as ex:
                logger.error("Compression failed, using original PDF", error=str(ex))
                return p_bytes
                
        # Aplica a compressão (se aplicável)
        pdf_bytes = _compress_presentation_pdf(pdf_bytes)

        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            total_pages = len(doc)
            logger.info(f"PDF has {total_pages} pages. Processing with Azure DI.")

            for start in range(0, total_pages, 2):
                end = min(start + 2, total_pages)
                batch_doc = fitz.open()
                batch_doc.insert_pdf(doc, from_page=start, to_page=end - 1)
                batch_bytes = batch_doc.tobytes()
                batch_doc.close()

                async with DocumentIntelligenceClient(endpoint=endpoint, credential=AzureKeyCredential(key)) as di_client:
                    poller = await di_client.begin_analyze_document("prebuilt-layout", body=batch_bytes, content_type="application/octet-stream")
                    result = await poller.result()
                    
                full_text += (result.content or "") + "\n\n"
                if result.paragraphs:
                    raw_paragraphs.extend([p.content.strip() for p in result.paragraphs if p.content and len(p.content.strip()) > 30])
            doc.close()
        except Exception as e:
            logger.error("Azure Document Intelligence processing failed", error=str(e))
            return False

    else:
        # TXT / MD or Fallback
        full_text = file_bytes.decode('utf-8', errors='ignore')
        logger.info(f"Raw Text extracted {len(full_text)} chars.")
        
    # 3. Semantic chunking
    if not raw_paragraphs:
        chunks = split_document_text(
            text=full_text,
            chunk_size=800,
            chunk_overlap=150,
            separators=["\n\n", "\n", ". ", "! ", "? ", " ", ""]
        )

    if raw_paragraphs:
        chunks = []
        buffer = ""
        for para in raw_paragraphs:
            if len(buffer) + len(para) + 2 <= 800:
                buffer = (buffer + "\n\n" + para).strip()
            else:
                if buffer:
                    chunks.extend(split_document_text(buffer, chunk_size=800, chunk_overlap=150))
                buffer = para
        if buffer:
            chunks.extend(split_document_text(buffer, chunk_size=800, chunk_overlap=150))
    else:
        logger.warning("No structured paragraphs found. Falling back to plain text splitting.")
        chunks = split_document_text(
            text=full_text,
            chunk_size=800,
            chunk_overlap=150,
            separators=["\n\n", "\n", ". ", "! ", "? ", " ", ""]
        )

    # Remove duplicates
    seen = set()
    unique_chunks = []
    for c in chunks:
        key = c[:80]
        if key not in seen and len(c.strip()) > 30:
            seen.add(key)
            unique_chunks.append(c)
    chunks = unique_chunks

    logger.info(f"Chunked document into {len(chunks)} chunks.")
        
    # 4. Generate embeddings with Gemini
    embedder = get_embedder(settings.DEFAULT_EMBEDDER_PROVIDER)
    embeddings = []
    try:
        for i, chunk in enumerate(chunks):
            result = await embedder.aembed_documents([chunk])
            embeddings.append(result[0])
            if (i + 1) % 5 == 0:
                logger.info(f"Embedded {i + 1}/{len(chunks)} chunks...")
    except Exception as e:
        logger.error("Embedding generation failed", error=str(e))
        return False

    # 5. Upsert to Qdrant
    qdrant = await get_qdrant()
    points = []

    for idx, (chunk, vector) in enumerate(zip(chunks, embeddings)):
        points.append(
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "teacher_id": teacher_id,
                    "course_id": course_id,
                    "lesson_number": lesson_number,
                    "content": chunk,
                    "source": file_name,
                    "chunk_index": idx,
                    "total_chunks": len(chunks)
                }
            )
        )

    await qdrant.upsert(
        collection_name="documents",
        points=points
    )

    logger.info(f"✅ Successfully upserted {len(points)} vectors to Qdrant for {file_name}")
    return True

async def sync_google_drive():
    """Polls the Google Drive folder for new files to ingest."""
    folder_id = settings.GOOGLE_DRIVE_FOLDER_ID
    if not folder_id:
        logger.warning("GOOGLE_DRIVE_FOLDER_ID is not set in config.")
        return
        
    logger.info(f"Checking Google Drive folder: {folder_id}")
    files = await drive_client.list_course_files(folder_id)
    
    if not files:
        logger.info("No files found in the folder.")
        return
        
    await init_qdrant_collection("documents")
    
    async with AsyncSessionLocal() as session:
        docs_before = (await session.execute(text("SELECT count(*) FROM file_sync_state WHERE status='concluído'"))).scalar()

        for f in files:
            file_id = f['id']
            file_name = f['name']
            mime_type = f.get('mimeType', '')
            course_name = f.get('course_name')
            lesson_number = f.get('lesson_number', 1)
            
            if not course_name:
                continue
                
            result = await session.execute(
                text("SELECT id, teacher_id FROM Courses WHERE name = :name LIMIT 1"), 
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
            
            if row and row[0] == 'concluído':
                continue
                
            # Upsert state as 'processando'
            await session.execute(
                text("""
                    INSERT INTO file_sync_state (file_id, teacher_id, status, processing_started_at)
                    VALUES (:fid, :tid, 'processando', CURRENT_TIMESTAMP)
                    ON CONFLICT(file_id) DO UPDATE SET status='processando', processing_started_at=CURRENT_TIMESTAMP
                """),
                {"fid": file_id, "tid": teacher_id}
            )
            await session.commit()
            
            success = await process_file(file_id, file_name, mime_type, teacher_id, course_id, lesson_number)
            
            new_status = 'concluído' if success else 'falhou'
            await session.execute(
                text("UPDATE file_sync_state SET status = :status, last_synced = CURRENT_TIMESTAMP WHERE file_id = :fid"),
                {"status": new_status, "fid": file_id}
            )
            await session.commit()
            
        docs_after = (await session.execute(text("SELECT count(*) FROM file_sync_state WHERE status='concluído'"))).scalar()
        return {"docs_before": docs_before, "docs_after": docs_after}

async def worker_loop():
    logger.info("Starting Ingestion Worker loop...")
    while True:
        try:
            await sync_google_drive()
        except Exception as e:
            logger.error("Error in ingestion sync loop", error=str(e))
            
        # Poll every 5 minutes (300 seconds)
        await asyncio.sleep(300)

if __name__ == "__main__":
    asyncio.run(worker_loop())
