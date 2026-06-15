"""Testes do registry de parsers (`src/parsing/factory.py`) e do MarkerParser (M1).

PUROS: mecânica de registro/seleção/cache/fallback e conversão Markdown→blocos.
Nenhuma chamada de rede real (o `_call_marker` é mockado). Rodar:
    pytest tests/unit/test_parser_factory.py -v
"""

import pytest

from src.parsing.factory import (
    DEFAULT_PARSER,
    get_parser,
    register_parser,
    reset_parser_cache,
)
from src.parsing.interfaces import IDocumentParser
from src.parsing.marker_parser import (
    MarkerParser,
    extract_markdown,
    markdown_to_blocks,
)


class _FakeParser(IDocumentParser):
    async def parse(self, blob_url: str, file_bytes: bytes, filename: str) -> list[str]:
        return ["## Fake", "bloco de teste"]


class _OtherFakeParser(IDocumentParser):
    async def parse(self, blob_url: str, file_bytes: bytes, filename: str) -> list[str]:
        return ["outro"]


def test_register_and_get_returns_registered_instance():
    register_parser("fake_unit", _FakeParser)
    assert isinstance(get_parser("fake_unit"), _FakeParser)


def test_get_parser_caches_singleton_per_name():
    register_parser("fake_cache", _FakeParser)
    assert get_parser("fake_cache") is get_parser("fake_cache")


def test_explicit_name_selects_provider_independent_of_default():
    # Selecionar pelo nome ignora o PARSER_BACKEND — permite A/B no eval.
    register_parser("fake_sel", _FakeParser)
    register_parser("fake_sel_b", _OtherFakeParser)
    assert isinstance(get_parser("fake_sel"), _FakeParser)
    assert isinstance(get_parser("fake_sel_b"), _OtherFakeParser)


def test_register_accepts_class_or_builder():
    # Classe direta (como `register_parser("mistral", MistralOCRParser)`)...
    register_parser("fake_cls", _FakeParser)
    assert isinstance(get_parser("fake_cls"), _FakeParser)
    # ...e builder com import tardio (função que devolve a instância).
    register_parser("fake_builder", lambda: _OtherFakeParser())
    assert isinstance(get_parser("fake_builder"), _OtherFakeParser)


def test_unknown_provider_falls_back_to_default_without_crashing():
    # Typo de env NÃO derruba a ingestão: degrada para o parser padrão.
    parser = get_parser("definitely_not_registered_xyz")
    assert isinstance(parser, IDocumentParser)
    assert type(parser).__name__ == "AzureDocIntelParser"


def test_builtin_parsers_registered():
    from src.parsing.local_parser import LocalPDFParser
    from src.parsing.azure_parser import AzureDocIntelParser

    assert DEFAULT_PARSER == "azure"
    assert isinstance(get_parser("local"), LocalPDFParser)
    assert isinstance(get_parser("azure"), AzureDocIntelParser)


def test_reset_parser_cache_rebuilds_instance():
    register_parser("fake_reset", _FakeParser)
    first = get_parser("fake_reset")
    reset_parser_cache()
    assert get_parser("fake_reset") is not first


def test_marker_is_registered():
    # M1: o parser de matemática entra no registry como mais um backend.
    assert "marker" in __import__("src.parsing.factory", fromlist=["_PARSER_BUILDERS"])._PARSER_BUILDERS


# ==============================================================================
# M1 — MarkerParser: conversão Markdown→blocos e fallback
# ==============================================================================

def test_extract_markdown_from_keys_and_raw():
    assert extract_markdown("# cru") == "# cru"
    assert extract_markdown({"markdown": "# md"}) == "# md"
    assert extract_markdown({"output": "# out"}) == "# out"
    # markdown tem prioridade sobre os alias.
    assert extract_markdown({"text": "t", "markdown": "m"}) == "m"


def test_extract_markdown_raises_without_content():
    import pytest as _pytest
    with _pytest.raises(ValueError):
        extract_markdown({"foo": "bar"})
    with _pytest.raises(ValueError):
        extract_markdown({"markdown": "   "})  # vazio não conta


def test_markdown_to_blocks_headings_are_own_blocks():
    md = "# Cálculo I\n\nA derivada mede a inclinação.\n\n## Definição\n\nVeja abaixo."
    blocks = markdown_to_blocks(md)
    assert blocks == [
        "# Cálculo I",
        "A derivada mede a inclinação.",
        "## Definição",
        "Veja abaixo.",
    ]


def test_markdown_to_blocks_keeps_multiline_formula_intact():
    md = "Considere:\n\n$$\n\\int_0^1 x^2\\,dx\n= \\frac{1}{3}\n$$\n\nlogo."
    blocks = markdown_to_blocks(md)
    # A fórmula em várias linhas vira UM bloco íntegro (sem linha em branco interna).
    formula_blocks = [b for b in blocks if "\\int_0^1" in b]
    assert len(formula_blocks) == 1
    assert formula_blocks[0].startswith("$$")
    assert "\\frac{1}{3}" in formula_blocks[0]


def test_markdown_to_blocks_keeps_table_intact():
    md = "Tabela:\n\n| x | f(x) |\n|---|------|\n| 1 | 1 |\n| 2 | 4 |\n\nfim."
    blocks = markdown_to_blocks(md)
    table_blocks = [b for b in blocks if "f(x)" in b]
    assert len(table_blocks) == 1
    assert table_blocks[0].count("\n") == 3  # 4 linhas da tabela num só bloco


@pytest.mark.asyncio
async def test_marker_parse_success(monkeypatch):
    parser = MarkerParser(fallback_backend="local")
    monkeypatch.setattr(parser, "endpoint", "http://fake-marker.local/parse")

    async def _fake_call(file_bytes, filename):
        return "## Seção\n\n$$x^2$$\n\nfim."

    monkeypatch.setattr(parser, "_call_marker", _fake_call)
    blocks = await parser.parse("", b"%PDF-fake", "aula.pdf")
    assert blocks == ["## Seção", "$$x^2$$", "fim."]


@pytest.mark.asyncio
async def test_marker_parse_falls_back_on_error(monkeypatch):
    register_parser("fake_fb", _OtherFakeParser)
    parser = MarkerParser(fallback_backend="fake_fb")
    monkeypatch.setattr(parser, "endpoint", "http://fake-marker.local/parse")

    async def _boom(file_bytes, filename):
        raise RuntimeError("marker down")

    monkeypatch.setattr(parser, "_call_marker", _boom)
    blocks = await parser.parse("", b"%PDF-fake", "aula.pdf")
    # Degradou para o fallback registrado (_OtherFakeParser → ["outro"]).
    assert blocks == ["outro"]


@pytest.mark.asyncio
async def test_marker_parse_falls_back_when_no_endpoint(monkeypatch):
    register_parser("fake_fb2", _OtherFakeParser)
    parser = MarkerParser(fallback_backend="fake_fb2")
    monkeypatch.setattr(parser, "endpoint", "")  # sem endpoint configurado
    blocks = await parser.parse("", b"%PDF-fake", "aula.pdf")
    assert blocks == ["outro"]


def test_marker_fallback_never_loops_to_itself():
    # Anti-loop: fallback "marker" é normalizado para "azure".
    assert MarkerParser(fallback_backend="marker")._fallback_backend == "azure"
