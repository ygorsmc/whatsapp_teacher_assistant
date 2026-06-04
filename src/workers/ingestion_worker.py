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
from src.parsing.factory import get_parser
import src.llm # Init providers

logger = structlog.get_logger(__name__)

async def process_file(file_id: str, file_name: str, mime_type: str, teacher_id: str, course_id: str, lesson_number: int):
    """
    Downloads a file from Google Drive, parses it (via factory parsers), 
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
        parser = get_parser()
        
        try:
            raw_paragraphs = await parser.parse(blob_url="", file_bytes=pdf_bytes, filename=file_name)
            full_text = "\n\n".join(raw_paragraphs)
        except Exception as e:
            logger.error("Parsing failed", error=str(e))
            return False
        
    # 3. Semantic chunking
    if not raw_paragraphs:
        chunks = split_document_text(
            text=full_text,
            chunk_size=settings.RAG_CHUNK_SIZE,
            chunk_overlap=settings.RAG_CHUNK_OVERLAP,
            separators=["\n\n", "\n", ". ", "! ", "? ", " ", ""]
        )

    if raw_paragraphs:
        chunks = []
        buffer = ""
        for para in raw_paragraphs:
            if len(buffer) + len(para) + 2 <= settings.RAG_CHUNK_SIZE:
                buffer = (buffer + "\n\n" + para).strip()
            else:
                if buffer:
                    chunks.extend(split_document_text(buffer, chunk_size=settings.RAG_CHUNK_SIZE, chunk_overlap=settings.RAG_CHUNK_OVERLAP))
                buffer = para
        if buffer:
            chunks.extend(split_document_text(buffer, chunk_size=settings.RAG_CHUNK_SIZE, chunk_overlap=settings.RAG_CHUNK_OVERLAP))
    else:
        logger.warning("No structured paragraphs found. Falling back to plain text splitting.")
        chunks = split_document_text(
            text=full_text,
            chunk_size=settings.RAG_CHUNK_SIZE,
            chunk_overlap=settings.RAG_CHUNK_OVERLAP,
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
