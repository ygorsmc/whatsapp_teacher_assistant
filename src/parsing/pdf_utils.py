"""Utilitários de PDF compartilhados entre parsers (PyMuPDF/fitz).

Por ora: fatiamento para respeitar os limites do OCR da Mistral. Diferente do
Azure DI (que fatia 1 página/chamada por CUSTO — cobrança por página + throttle
do free tier), aqui o split é só para caber no TETO RÍGIDO da API (≤1000 páginas
e ~50 MB por documento). Logo, fatiamos no MAIOR pedaço possível sob o teto —
mandar o documento inteiro numa chamada só é o caminho preferido (mais barato em
overhead e preserva o contexto entre páginas).
"""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)

# Tetos da API de OCR da Mistral (com margem sob os 50 MB para o overhead do
# data-url base64 e variação de compressão).
MISTRAL_OCR_MAX_PAGES = 1000
MISTRAL_OCR_MAX_BYTES = 45 * 1024 * 1024


def split_pdf_for_ocr(
    pdf_bytes: bytes,
    max_pages: int = MISTRAL_OCR_MAX_PAGES,
    max_bytes: int = MISTRAL_OCR_MAX_BYTES,
) -> list[bytes]:
    """Fatia um PDF em pedaços que respeitem os limites do OCR da Mistral.

    Caso comum (documento dentro do teto): devolve `[pdf_bytes]` — UM elemento,
    sem recompressão, ou seja, comportamento byte-a-byte idêntico ao de não
    fatiar. Só PDFs grandes (> max_pages OU estimados > max_bytes) são quebrados,
    em janelas do MAIOR tamanho possível sob os dois tetos (não 1 página/pedaço).

    O orçamento de bytes usa o tamanho médio por página (`len/total`) — barato e
    suficiente; não serializa repetidamente. Se o fitz não abrir o arquivo,
    devolve `[pdf_bytes]` intacto (deixa um eventual erro surgir na própria API,
    com mensagem clara, em vez de mascarar)."""
    import fitz  # import tardio: só quem fatia paga o custo do SDK

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        logger.warning("split_pdf_open_failed", error=str(e)[:150])
        return [pdf_bytes]

    try:
        total = doc.page_count
        if total == 0:
            return [pdf_bytes]

        avg_page_bytes = max(1, len(pdf_bytes) // total)
        pages_by_size = max(1, max_bytes // avg_page_bytes)
        step = max(1, min(max_pages, pages_by_size))

        if total <= step:
            return [pdf_bytes]

        logger.info(
            "split_pdf_for_ocr",
            total_pages=total,
            pages_per_chunk=step,
            chunks=(total + step - 1) // step,
            reason="max_pages" if step == max_pages else "max_bytes",
        )

        chunks: list[bytes] = []
        for start in range(0, total, step):
            end = min(start + step, total)
            part = fitz.open()
            part.insert_pdf(doc, from_page=start, to_page=end - 1)
            # garbage=4, deflate=True: limpa lixo da cópia e reduz o MB do pedaço.
            chunks.append(part.tobytes(garbage=4, deflate=True))
            part.close()
        return chunks
    finally:
        doc.close()
