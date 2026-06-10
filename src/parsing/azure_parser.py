import fitz  # PyMuPDF
import structlog
from typing import List, Any
from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
from src.parsing.interfaces import IDocumentParser

logger = structlog.get_logger(__name__)

class AzureDocIntelParser(IDocumentParser):
    
    def _optimize_pdf(self, p_bytes: bytes) -> bytes:
        """
        1. COMPRESSÃO FINOPS: Agrupa páginas 2 a 2 na vertical para reduzir o custo do DI.
        2. LIMITES DA AZURE: Redimensiona páginas se excederem 17 polegadas (1224 pontos) para evitar o erro InvalidContentLength.
        """
        try:
            doc = fitz.open(stream=p_bytes, filetype="pdf")
            if len(doc) == 0:
                doc.close()
                return p_bytes
                
            first_page = doc[0]
            w = first_page.rect.width
            h = first_page.rect.height
            
            # Verifica se é formato paisagem (Slide) e tem mais de 1 página
            is_presentation = (w > h * 1.1) and (len(doc) > 1)
            
            # Precisamos redimensionar? (Qualquer dimensão > 1200 para dar folga ao limite da Microsoft de 1224)
            needs_resize = w > 1200 or h > 1200
            
            if is_presentation:
                logger.info("Formato de Apresentação detectado: Agrupando páginas 2 a 2 na vertical (FinOps).")
                new_doc = fitz.open()
                
                for i in range(0, len(doc), 2):
                    p1 = doc[i]
                    p2 = doc[i+1] if i+1 < len(doc) else None
                    
                    pw = p1.rect.width
                    ph1 = p1.rect.height
                    ph2 = p2.rect.height if p2 else 0
                    ptotal_h = ph1 + ph2
                    
                    # Fator de escala para caber em 1200
                    scale = 1.0
                    if pw > 1200 or ptotal_h > 1200:
                        scale = min(1200 / pw, 1200 / ptotal_h)
                        
                    final_w = pw * scale
                    final_h = ptotal_h * scale
                    final_h1 = ph1 * scale
                    
                    new_page = new_doc.new_page(width=final_w, height=final_h)
                    new_page.show_pdf_page(fitz.Rect(0, 0, final_w, final_h1), doc, p1.number)
                    
                    if p2:
                        new_page.show_pdf_page(fitz.Rect(0, final_h1, final_w, final_h), doc, p2.number)
                        
                # garbage=4, deflate=True limpa lixo da cópia de páginas e reduz MBs drasticamente
                optimized_bytes = new_doc.tobytes(garbage=4, deflate=True)
                doc.close()
                new_doc.close()
                return optimized_bytes
                
            elif needs_resize:
                logger.info(f"PDF excede limite da Azure ({w}x{h}). Redimensionando para caber em 1200x1200.")
                new_doc = fitz.open()
                for i in range(len(doc)):
                    p = doc[i]
                    pw = p.rect.width
                    ph = p.rect.height
                    scale = min(1200 / pw, 1200 / ph)
                    
                    final_w = pw * scale
                    final_h = ph * scale
                    
                    new_page = new_doc.new_page(width=final_w, height=final_h)
                    new_page.show_pdf_page(fitz.Rect(0, 0, final_w, final_h), doc, p.number)
                    
                optimized_bytes = new_doc.tobytes(garbage=4, deflate=True)
                doc.close()
                new_doc.close()
                return optimized_bytes
                
            else:
                doc.close()
                return p_bytes
        except Exception as ex:
            logger.error("Compression failed, using original PDF", error=str(ex))
            return p_bytes

    async def parse(self, blob_url: str, file_bytes: bytes, filename: str) -> List[str]:
        from src.shared.config import settings
        import asyncio
        raw_paragraphs = []
        
        endpoint = settings.DOCUMENT_INTELLIGENCE_ENDPOINT
        key = settings.DOCUMENT_INTELLIGENCE_KEY
        
        if not endpoint:
            raise ValueError("Missing Azure Document Intelligence endpoint in configuration.")

        if filename.lower().endswith('.pdf'):
            pdf_bytes = self._optimize_pdf(file_bytes)
            
            try:
                doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                total_pages = len(doc)
                logger.info(f"PDF has {total_pages} pages. Processing with Azure DI (1 page/call, throttled to 20 calls/min).")

                cred: Any
                if key:
                    from azure.core.credentials import AzureKeyCredential
                    cred = AzureKeyCredential(key)
                else:
                    from azure.identity.aio import DefaultAzureCredential
                    cred = DefaultAzureCredential()
                
                from tenacity import AsyncRetrying, wait_exponential, stop_after_attempt, retry_if_exception_type
                from azure.core.exceptions import HttpResponseError
                
                # Reutiliza um único cliente Azure DI para todas as páginas (evita overhead de handshake TLS)
                async with DocumentIntelligenceClient(endpoint=endpoint, credential=cred) as di_client:
                    for page_idx in range(total_pages):
                        # Fatia 1 página por vez com garbage collection
                        batch_doc = fitz.open()
                        batch_doc.insert_pdf(doc, from_page=page_idx, to_page=page_idx)
                        batch_bytes = batch_doc.tobytes(garbage=4, deflate=True)
                        batch_doc.close()
                        
                        chunk_mb = len(batch_bytes) / 1024 / 1024
                        logger.info(f"Sending page {page_idx + 1}/{total_pages} to Azure DI ({chunk_mb:.2f} MB)")
                        
                        # Retry com backoff exponencial (protege contra 429 Rate Limit)
                        async for attempt in AsyncRetrying(
                            wait=wait_exponential(multiplier=2, min=4, max=60),
                            stop=stop_after_attempt(5),
                            retry=retry_if_exception_type(HttpResponseError),
                            reraise=True
                        ):
                            with attempt:
                                poller = await di_client.begin_analyze_document(
                                    "prebuilt-layout",
                                    body=batch_bytes,
                                    content_type="application/octet-stream"
                                )
                                result = await poller.result()
                        
                        if result.paragraphs:
                            raw_paragraphs.extend([p.content.strip() for p in result.paragraphs if p.content and len(p.content.strip()) > 30])
                        
                        # THROTTLE: 3.5s entre chamadas = ~17 calls/min (margem de segurança para o limite de 20/min do F0)
                        if page_idx < total_pages - 1:
                            await asyncio.sleep(3.5)
                            
                doc.close()
            except Exception as e:
                logger.error("Azure Document Intelligence processing failed", error=str(e))
                raise
        else:
            # TXT / MD or Fallback
            full_text = file_bytes.decode('utf-8', errors='ignore')
            raw_paragraphs = [full_text]
            
        return raw_paragraphs

