import asyncio
import base64
import re
import structlog
from typing import List

from mistralai.client import Mistral
from google import genai
from tenacity import AsyncRetrying, wait_exponential, stop_after_attempt

from src.parsing.interfaces import IDocumentParser
from src.parsing.pdf_utils import split_pdf_for_ocr
from src.shared.config import settings

logger = structlog.get_logger(__name__)

class MistralOCRParser(IDocumentParser):
    """
    Parser oficial baseado no Mistral OCR (multimodal) combinado com Gemini Vision.
    Arquitetura Cloud-Native (Stateless): Processa tudo em memória, ideal para o
    Azure Container Apps onde o disco é efêmero e o Worker processa filas concorrentes.
    """

    def __init__(self):
        self.mistral_key = settings.MISTRAL_API_KEY
        self.google_key = settings.GOOGLE_API_KEY

        if not self.mistral_key:
            raise ValueError("MISTRAL_API_KEY is not configured.")
        if not self.google_key:
            raise ValueError("GOOGLE_API_KEY is not configured.")

        self.mistral_client = Mistral(api_key=self.mistral_key)
        self.gemini_client = genai.Client(api_key=self.google_key)

    async def _get_document_context(self, initial_markdown: str) -> str:
        """Gera um pequeno resumo sobre o documento lendo as primeiras páginas."""
        prompt = (
            "You are a helpful assistant. Read the following initial pages of a document "
            "extracted via OCR and write a brief 2-3 sentence summary of what this document is about "
            "(e.g., 'This is a PMP Exam Prep study guide covering project management frameworks'). "
            "This summary will be used as context to describe images later.\n\n"
            f"Document snippet:\n{initial_markdown[:10000]}"
        )
        try:
            logger.info("Generating document context with Gemini...")
            response = await self.gemini_client.aio.models.generate_content(
                model="gemini-3.1-flash-lite",
                contents=[prompt],
            )
            context = response.text.strip()
            logger.info(f"Context generated: {context}")
            return context
        except Exception as e:
            logger.warning(f"Failed to generate context, proceeding without it: {e}")
            return "A technical or educational document."

    async def _describe_image(self, img_name: str, img_b64: str, doc_context: str, surrounding_text: str) -> str:
        """Envia imagem para o Gemini descrever (com tenacity retry para Rate Limit)."""
        prompt = (
            f"Context about the document: {doc_context}\n\n"
            "You are an expert at describing educational diagrams, charts, and figures. "
            "Describe this image in detail so a text-search system can understand its content. "
            "Include all labels, data, relationships, and structure. Be concise but thorough.\n\n"
            f"Surrounding text for context:\n{surrounding_text}"
        )

        if "," in img_b64:
            img_b64 = img_b64.split(",", 1)[1]

        async for attempt in AsyncRetrying(
            wait=wait_exponential(multiplier=2, min=4, max=60),
            stop=stop_after_attempt(5),
            reraise=True
        ):
            with attempt:
                response = await self.gemini_client.aio.models.generate_content(
                    model="gemini-3.5-flash",
                    contents=[
                        {"role": "user", "parts": [
                            {"text": prompt},
                            {"inline_data": {"mime_type": "image/jpeg", "data": img_b64}}
                        ]}
                    ],
                )
                if not response.text:
                    raise ValueError("Empty response from Gemini Vision")
                return f"[Imagem: {img_name}] {response.text.strip()}"
        return f"[Imagem: {img_name} - descrição falhou]"

    async def enrich_pages_to_paragraphs(self, pages: List[dict], start_page: int = 1) -> List[str]:
        """Enriquecimento visual a partir de páginas JÁ extraídas por OCR.

        Recebe a forma NORMALIZADA das páginas do Mistral OCR
            [{"markdown": str, "images": [{"id": str, "image_base64": str}, ...]}]
        gera um resumo de contexto (Gemini), descreve cada imagem com esse
        contexto e substitui a referência `![id](id)` pela descrição, devolvendo
        os blocos markdown `## Página N` — o MESMO formato de saída do `parse()`.

        `start_page` desloca a numeração das páginas — usado quando um PDF grande
        é fatiado (>1000 págs / 50 MB) e cada pedaço é enriquecido em separado,
        para que `## Página N` continue contínuo entre os pedaços.

        Isolado de propósito: o OCR online (`parse`, que faz a chamada ao Mistral)
        e o OCR em LOTE (Mistral Batch, −50%, feito offline pelo
        `scripts/bulk_ingest_mistral.py`) compartilham ESTA implementação — só
        muda quem produz as `pages`. Sem duplicar a lógica de enriquecimento.
        """
        full_markdown = "\n\n".join(p.get("markdown", "") for p in pages)

        # 1. Resumo de Contexto (Gemini 3.1 Flash Lite)
        doc_context = await self._get_document_context(full_markdown)

        # 2. Mapear imagens em memória
        image_map = {}
        for page in pages:
            for img in (page.get("images") or []):
                if img.get("image_base64"):
                    image_map[img["id"]] = img["image_base64"]

        logger.info(f"Found {len(image_map)} images. Starting descriptions with Gemini...")

        # 3. Descrever imagens iterativamente (Gemini 3.5 Flash)
        image_descriptions = {}
        for img_id, img_b64 in image_map.items():
            pattern = re.escape(f"![{img_id}]({img_id})")
            match = re.search(pattern, full_markdown)
            if match:
                start_ctx = max(0, match.start() - 300)
                end_ctx = min(len(full_markdown), match.end() + 300)
                surrounding = full_markdown[start_ctx:end_ctx]
            else:
                surrounding = ""

            try:
                desc = await self._describe_image(img_id, img_b64, doc_context, surrounding)
                image_descriptions[img_id] = desc
            except Exception as e:
                logger.error(f"Failed to describe image {img_id}", error=str(e))
                image_descriptions[img_id] = f"[Imagem: {img_id} - erro na extração]"

            await asyncio.sleep(1.0)

        # 4. Substituição e blocos por página
        raw_paragraphs = []
        for i, page in enumerate(pages):
            page_md = page.get("markdown", "")

            for img_id, desc in image_descriptions.items():
                old_ref = f"![{img_id}]({img_id})"
                new_ref = f"\n> 📊 **{desc}**\n"
                page_md = page_md.replace(old_ref, new_ref)

            raw_paragraphs.append(f"## Página {start_page + i}\n\n{page_md}")

        return raw_paragraphs

    @staticmethod
    def _normalize_sdk_pages(sdk_pages) -> List[dict]:
        """Converte as páginas do SDK do Mistral OCR para a forma normalizada
        (dicts) que `enrich_pages_to_paragraphs` consome — a MESMA forma que o
        JSONL de resultado do Batch já entrega."""
        return [
            {
                "markdown": p.markdown,
                "images": [
                    {"id": img.id, "image_base64": img.image_base64}
                    for img in (getattr(p, "images", None) or [])
                    if img.image_base64
                ],
            }
            for p in sdk_pages
        ]

    async def _ocr_chunk(self, chunk_bytes: bytes, filename: str) -> List[dict]:
        """OCR de UM pedaço de PDF (já dentro dos tetos da API) → páginas
        normalizadas. Levanta a exceção da Mistral (a ingestão decide o fallback)."""
        base64_pdf = base64.b64encode(chunk_bytes).decode("utf-8")
        try:
            ocr_response = await self.mistral_client.ocr.process_async(
                model="mistral-ocr-latest",
                document={
                    "type": "document_url",
                    "document_url": f"data:application/pdf;base64,{base64_pdf}",
                },
                include_image_base64=True,
                # Isola cabeçalho/rodapé corridos em page.header/footer → fora do
                # markdown que vira chunk (boilerplate não polui o RAG).
                extract_header=settings.MISTRAL_OCR_EXTRACT_HEADER_FOOTER,
                extract_footer=settings.MISTRAL_OCR_EXTRACT_HEADER_FOOTER,
            )
        except Exception as e:
            logger.error(f"Mistral OCR failed for {filename}", error=str(e))
            raise
        return self._normalize_sdk_pages(ocr_response.pages)

    async def parse(self, blob_url: str, file_bytes: bytes, filename: str) -> List[str]:
        if not filename.lower().endswith('.pdf'):
            return [file_bytes.decode('utf-8', errors='ignore')]

        logger.info(f"Starting Mistral OCR parse for {filename} ({len(file_bytes)/1024/1024:.2f} MB)")

        # Respeita os tetos do OCR da Mistral (≤1000 págs / ~50 MB). Documento
        # dentro do limite → 1 pedaço (intacto): comportamento idêntico ao de
        # antes. Só PDFs grandes são fatiados; cada pedaço é OCRizado e enriquecido
        # em separado, com a numeração de páginas contínua entre eles.
        chunks = split_pdf_for_ocr(file_bytes)
        if len(chunks) > 1:
            logger.info(f"{filename}: PDF grande → {len(chunks)} pedaços para o OCR.")

        raw_paragraphs: List[str] = []
        page_offset = 0
        for chunk_bytes in chunks:
            pages = await self._ocr_chunk(chunk_bytes, filename)
            raw_paragraphs += await self.enrich_pages_to_paragraphs(pages, start_page=page_offset + 1)
            page_offset += len(pages)

        logger.info(f"Mistral OCR finished: {page_offset} pages extracted from {filename}.")
        return raw_paragraphs
