"""
Factory da camada de parsing — seleção de `IDocumentParser` por env var, no
mesmo padrão registry de `speech/factory.py` e `llm/factory.py`.

Registrar um parser novo (Mistral OCR, Marker, Docling, Nougat...) é UMA linha:

    register_parser("mistral", MistralOCRParser)

e o serviço passa a ser selecionável por `PARSER_BACKEND=mistral` — ou
`get_parser("mistral")` para rodar dois parsers lado a lado no eval, sem
reiniciar o processo. Provider desconhecido NÃO derruba a ingestão: degrada
para o parser padrão (Azure DI) com aviso — parser, diferente de speech, não é
opcional (o aluno precisa do material indexado).

Os builders dos parsers nativos fazem import TARDIO do SDK pesado (Azure DI puxa
azure-ai + fitz; o local puxa fitz) — selecionar 'local' não importa o SDK da
Azure, e vice-versa.

Contrato que todo parser registrado honra (ver `IDocumentParser`):
  - `parse(...) -> list[str]` de blocos markdown;
  - headings prefixados com `## ` (o chunker estrutural usa como fronteira de
    seção → `section_title`);
  - matemática em LaTeX com `$...$` / `$$...$$` (o guarda de chunk protege os
    blocos em display).
"""

from __future__ import annotations

from typing import Callable

import structlog

from src.parsing.interfaces import IDocumentParser
from src.shared.config import settings

logger = structlog.get_logger(__name__)

DEFAULT_PARSER = "azure"

# Registry nome → builder: um callable que devolve uma instância de
# IDocumentParser. Uma CLASSE serve como builder (chamá-la instancia); um
# builder pode ser uma função com import tardio, como os nativos abaixo.
_PARSER_BUILDERS: dict[str, Callable[[], IDocumentParser]] = {}
_instances: dict[str, IDocumentParser] = {}


def register_parser(name: str, builder: Callable[[], IDocumentParser]) -> None:
    """Registra um parser sob `name` (idempotente — sobrescreve)."""
    _PARSER_BUILDERS[name.lower()] = builder


def _build_azure() -> IDocumentParser:
    from src.parsing.azure_parser import AzureDocIntelParser  # import tardio (SDK Azure)
    return AzureDocIntelParser()


def _build_local() -> IDocumentParser:
    from src.parsing.local_parser import LocalPDFParser  # import tardio (fitz)
    return LocalPDFParser()


def _build_marker() -> IDocumentParser:
    from src.parsing.marker_parser import MarkerParser  # import tardio (httpx)
    return MarkerParser()


register_parser(DEFAULT_PARSER, _build_azure)
register_parser("local", _build_local)
register_parser("marker", _build_marker)


def get_parser(name: str | None = None) -> IDocumentParser:
    """Devolve o parser selecionado, com cache singleton por nome.

    Precedência: `name` explícito → `PARSER_BACKEND` → `DEFAULT_PARSER`. Nome
    não registrado degrada para o parser padrão (avisa e segue — nunca levanta
    nem devolve None; a ingestão não pode parar por um typo de env).
    """
    resolved = (name or settings.PARSER_BACKEND or DEFAULT_PARSER).lower()

    if resolved not in _PARSER_BUILDERS:
        logger.warning("unknown_parser_provider", provider=resolved, fallback=DEFAULT_PARSER)
        resolved = DEFAULT_PARSER

    cached = _instances.get(resolved)
    if cached is not None:
        return cached

    instance = _PARSER_BUILDERS[resolved]()
    _instances[resolved] = instance
    return instance


def reset_parser_cache() -> None:
    """Limpa o cache de instâncias (útil em testes e após trocar o backend)."""
    _instances.clear()
