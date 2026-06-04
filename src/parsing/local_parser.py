import fitz  # PyMuPDF
import structlog
from typing import List
from src.parsing.interfaces import IDocumentParser

logger = structlog.get_logger(__name__)

class LocalPDFParser(IDocumentParser):
    """
    Extracts text from PDF locally using PyMuPDF.
    Agnostic alternative to Azure Document Intelligence.
    """
    
    async def parse(self, blob_url: str, file_bytes: bytes, filename: str) -> List[str]:
        raw_paragraphs = []
        
        if filename.lower().endswith('.pdf'):
            try:
                doc = fitz.open(stream=file_bytes, filetype="pdf")
                total_pages = len(doc)
                logger.info(f"LocalPDFParser: processing {total_pages} pages.")

                for page_num in range(total_pages):
                    page = doc.load_page(page_num)
                    # Extract text blocks
                    blocks = page.get_text("blocks")
                    for block in blocks:
                        # block is typically (x0, y0, x1, y1, "text", block_no, block_type)
                        # block_type 0 is text
                        if len(block) >= 7 and block[6] == 0:
                            text = block[4].strip()
                            if len(text) > 30:
                                raw_paragraphs.append(text.replace("\n", " "))
                                
                doc.close()
            except Exception as e:
                logger.error("LocalPDFParser failed", error=str(e))
                raise
        else:
            # Fallback for TXT/MD
            full_text = file_bytes.decode('utf-8', errors='ignore')
            raw_paragraphs = [full_text]
            
        return raw_paragraphs
