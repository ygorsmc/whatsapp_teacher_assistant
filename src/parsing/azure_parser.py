import fitz  # PyMuPDF
import structlog
from typing import List, Any
from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
from src.parsing.interfaces import IDocumentParser

logger = structlog.get_logger(__name__)

class AzureDocIntelParser(IDocumentParser):
    
    def _compress_presentation_pdf(self, p_bytes: bytes) -> bytes:
        """
        --- COMPRESSÃO FINOPS: 2-UP EMPILHADO PARA SLIDES ---
        Agrupa páginas 2 a 2 na vertical para reduzir o custo do Document Intelligence pela metade.
        """
        try:
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

    async def parse(self, blob_url: str, file_bytes: bytes, filename: str) -> List[str]:
        from src.shared.config import settings
        raw_paragraphs = []
        
        endpoint = settings.DOCUMENT_INTELLIGENCE_ENDPOINT
        key = settings.DOCUMENT_INTELLIGENCE_KEY
        
        if not endpoint:
            raise ValueError("Missing Azure Document Intelligence endpoint in configuration.")

        if filename.lower().endswith('.pdf'):
            pdf_bytes = self._compress_presentation_pdf(file_bytes)
            
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

                    cred: Any
                    if key:
                        from azure.core.credentials import AzureKeyCredential
                        cred = AzureKeyCredential(key)
                    else:
                        from azure.identity.aio import DefaultAzureCredential
                        cred = DefaultAzureCredential()
                        
                    async with DocumentIntelligenceClient(endpoint=endpoint, credential=cred) as di_client:
                        poller = await di_client.begin_analyze_document("prebuilt-layout", body=batch_bytes, content_type="application/octet-stream")
                        result = await poller.result()
                        
                    if result.paragraphs:
                        raw_paragraphs.extend([p.content.strip() for p in result.paragraphs if p.content and len(p.content.strip()) > 30])
                doc.close()
            except Exception as e:
                logger.error("Azure Document Intelligence processing failed", error=str(e))
                raise
        else:
            # TXT / MD or Fallback
            full_text = file_bytes.decode('utf-8', errors='ignore')
            raw_paragraphs = [full_text]
            
        return raw_paragraphs
