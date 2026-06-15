"""
M1 — Parser ciente de matemática via endpoint self-hosted (Marker/Docling/MinerU).

Modo HÍBRIDO (cf. "Pipeline RAG para SaaS Educacional" §3): a ferramenta extrai a
prosa direto da camada digital do PDF (rápido/barato) e delega APENAS os crops de
fórmula/tabela a um VLM, que reconstrói o LaTeX. A saída é um Markdown unificado,
limpo, com a matemática em `$...$`/`$$...$$` — exatamente o que o guarda de chunk
(M3) protege e o braço esparso (BM25) casa por símbolo.

Contrato da API (documentado — adapte seu deploy a ele, como no BGE-M3/reranker):

    POST {MARKER_ENDPOINT}
    Authorization: Bearer {MARKER_API_KEY}            (se definido)
    multipart/form-data: file=<pdf bytes>, output_format="markdown"

    → {"success": true, "markdown": "# Título\\n\\n$$\\int...$$\\n\\n..."}

Aceita "output"/"text"/"result"/"content" como alias de "markdown", e também uma
resposta que seja a string Markdown crua.

FALLBACK SEMPRE (regra da casa): sem endpoint configurado, ou em erro/timeout/
resposta vazia, degrada para `MARKER_FALLBACK_PARSER` (Azure DI por padrão) — a
ingestão NUNCA para por causa do parser de matemática.
"""

import re
from typing import List

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from src.parsing.interfaces import IDocumentParser
from src.shared.config import settings

logger = structlog.get_logger(__name__)

# Linha de heading markdown (#..###### + texto) — vira bloco próprio para o
# chunker estrutural (`is_heading`/`section_title`).
_HEADING_LINE = re.compile(r"^#{1,6}\s+\S")

# Chaves onde o Markdown pode vir na resposta JSON, em ordem de preferência.
_MARKDOWN_KEYS = ("markdown", "output", "text", "result", "content")


def extract_markdown(payload) -> str:
    """Extrai o Markdown da resposta (string crua ou JSON com uma das chaves)."""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in _MARKDOWN_KEYS:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
    raise ValueError("Marker response has no markdown content")


def markdown_to_blocks(markdown: str) -> List[str]:
    """Quebra o Markdown em blocos para o chunker estrutural.

    Regras: linha em branco é fronteira de bloco; um heading (`## ...`) é sempre
    bloco próprio; linhas consecutivas não-vazias formam um parágrafo. Um bloco
    `$$...$$` em várias linhas (sem linha em branco no meio) permanece ÍNTEGRO —
    o guarda de chunk depois garante que ele não seja partido.
    """
    blocks: List[str] = []
    current: List[str] = []

    def flush() -> None:
        if current:
            text = "\n".join(current).strip()
            if text:
                blocks.append(text)
            current.clear()

    for line in (markdown or "").splitlines():
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        if _HEADING_LINE.match(stripped):
            flush()
            blocks.append(stripped)
            continue
        current.append(line)
    flush()
    return blocks


class MarkerParser(IDocumentParser):
    """Parser de matemática self-hosted, com fallback silencioso para o padrão."""

    def __init__(self, fallback_backend: str | None = None):
        self.endpoint = settings.MARKER_ENDPOINT
        self.api_key = settings.MARKER_API_KEY
        self.timeout = settings.MARKER_TIMEOUT_SECONDS
        # Cuidado anti-loop: o fallback nunca pode ser o próprio 'marker'.
        fb = (fallback_backend or settings.MARKER_FALLBACK_PARSER or "azure").lower()
        self._fallback_backend = "azure" if fb == "marker" else fb

    @retry(wait=wait_exponential(multiplier=1, min=1, max=10), stop=stop_after_attempt(3), reraise=True)
    async def _call_marker(self, file_bytes: bytes, filename: str) -> str:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                self.endpoint,
                headers=headers,
                files={"file": (filename, file_bytes, "application/pdf")},
                data={"output_format": "markdown"},
            )
            resp.raise_for_status()
            payload = resp.json()
        return extract_markdown(payload)

    async def _fallback(self, blob_url: str, file_bytes: bytes, filename: str) -> List[str]:
        from src.parsing.factory import get_parser
        return await get_parser(self._fallback_backend).parse(blob_url, file_bytes, filename)

    async def parse(self, blob_url: str, file_bytes: bytes, filename: str) -> List[str]:
        # Não-PDF (txt/md) não passa pelo VLM — o worker já trata texto direto,
        # mas se chegar aqui, manda pro fallback que sabe lidar com isso.
        if not self.endpoint:
            logger.warning("marker_endpoint_missing_falling_back", fallback=self._fallback_backend)
            return await self._fallback(blob_url, file_bytes, filename)

        try:
            markdown = await self._call_marker(file_bytes, filename)
        except Exception as e:
            logger.warning("marker_parse_failed_falling_back", error=str(e)[:200], fallback=self._fallback_backend)
            return await self._fallback(blob_url, file_bytes, filename)

        blocks = markdown_to_blocks(markdown)
        if not blocks:
            logger.warning("marker_returned_empty_falling_back", fallback=self._fallback_backend)
            return await self._fallback(blob_url, file_bytes, filename)

        logger.info("marker_parsed_ok", filename=filename, blocks=len(blocks), chars=len(markdown))
        return blocks
