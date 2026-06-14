"""
Testes unitários do RAG de Alta Precisão (F0-F8).

São testes PUROS (helpers de chunking, regexes do roteador, limiares do
CRAG-lite, métricas do eval e fallbacks mockados): não tocam banco, Redis,
Qdrant nem rede. Rodar com:  pytest tests/unit/test_rag_precision.py -v
"""

import pytest

from src.rag.text_splitter import (
    GLUE_HARD_CAP_FACTOR,
    StructuredChunk,
    assemble_structural_chunks,
    extract_heading_outline,
    heading_text,
    is_heading,
    should_glue_paragraph,
)
from src.rag.query_router import route_query, sparse_query_text
from src.rag.crag import (
    EVIDENCE_GREEN,
    EVIDENCE_RED,
    EVIDENCE_YELLOW,
    build_abstention_message,
    classify_evidence,
)
from src.rag.corpus import corpus_scope_key, should_bypass_rag
from src.llm.reranker import RerankResult, get_reranker, rerank_with_fallback


# ==============================================================================
# F1 — Chunking estrutural: should_glue_paragraph (regra ↔ exceção)
# ==============================================================================

PREV = "A regra geral determina o uso do fator 1.4 em todas as combinações."


@pytest.mark.parametrize("paragraph", [
    "Exceção: estruturas pré-moldadas usam fator 1.3.",
    "exceções: ver tabela 4.",
    "Salvo disposição em contrário, aplica-se o caput.",
    "Porém, em lajes em balanço o valor muda.",
    "Entretanto, há um caso especial.",
    "No entanto, o coeficiente é reduzido.",
    "Observação: válido apenas para concreto C30.",
    "Obs.: o ensaio é dispensado em lotes pequenos.",
    "Nota: ver errata da 2ª edição.",
    "Parágrafo único. O disposto não se aplica a microempresas.",
    "§ 2º A exigência será dispensada.",
    "a) primeira alínea da enumeração;",
    "iv) quarto item;",
    "1) item numerado;",
    "- item de lista com hífen",
    "• item de lista com bullet",
    "Importante: nunca misturar os aditivos.",
    "Atenção: risco de fissuração.",
])
def test_should_glue_paragraph_continuations(paragraph):
    assert should_glue_paragraph(PREV, paragraph) is True


@pytest.mark.parametrize("paragraph", [
    "O concreto é um material compósito utilizado em larga escala.",
    "Capítulo novo sobre fundações.",
    "Entretanto é uma palavra que aqui inicia outra frase? Não: este caso COLA.",
])
def test_should_glue_paragraph_normal_paragraphs(paragraph):
    expected = paragraph.startswith("Entretanto")
    assert should_glue_paragraph(PREV, paragraph) is expected


def test_should_glue_paragraph_requires_previous_buffer():
    assert should_glue_paragraph("", "Exceção: caso especial.") is False
    assert should_glue_paragraph("   ", "Exceção: caso especial.") is False
    assert should_glue_paragraph(PREV, "") is False


# ==============================================================================
# F1 — Headings e montagem estrutural
# ==============================================================================

def test_is_heading_markdown_and_numbered():
    assert is_heading("## Capítulo 3 — Termodinâmica") is True
    assert is_heading("# Título") is True
    assert is_heading("3.2 Equações do segundo grau") is True
    assert is_heading("Parágrafo comum que fala de 3.2 coisas e segue por aí com mais de oitenta caracteres no total.") is False
    assert is_heading("3.2 Esta linha termina como frase.") is False
    assert is_heading("") is False


def test_heading_text_strips_markdown():
    assert heading_text("## Capítulo 3 — Termodinâmica") == "Capítulo 3 — Termodinâmica"
    assert heading_text("3.2 Equações") == "3.2 Equações"


def test_extract_heading_outline():
    paragraphs = ["## Introdução", "texto", "## Capítulo 1", "mais texto", "## Capítulo 2"]
    assert extract_heading_outline(paragraphs) == ["Introdução", "Capítulo 1", "Capítulo 2"]
    assert extract_heading_outline(paragraphs, max_items=2) == ["Introdução", "Capítulo 1"]


def test_assemble_chunks_heading_becomes_section_title():
    paragraphs = [
        "## Regras de Segurança",
        "Use sempre o EPI adequado durante a prática.",
        "## Procedimentos",
        "Inicie pelo desligamento da máquina.",
    ]
    chunks = assemble_structural_chunks(paragraphs, chunk_size=200, chunk_overlap=20)
    assert [c.section_title for c in chunks] == ["Regras de Segurança", "Procedimentos"]
    # O texto do heading fica no corpo (ajuda o braço lexical).
    assert chunks[0].text.startswith("Regras de Segurança")


def test_assemble_chunks_glues_exception_beyond_chunk_size():
    rule = "A regra geral é aplicar o coeficiente de segurança 1.4 em todos os casos de carga. " * 3
    exception = "Exceção: em estruturas provisórias o coeficiente cai para 1.2."
    chunk_size = len(rule) + 10  # a exceção NÃO caberia no limite normal
    chunks = assemble_structural_chunks([rule, exception], chunk_size=chunk_size, chunk_overlap=20)
    # Regra e exceção permanecem no MESMO chunk (estourando até 1.5×).
    assert len(chunks) == 1
    assert "Exceção" in chunks[0].text
    assert len(chunks[0].text) <= int(chunk_size * GLUE_HARD_CAP_FACTOR)


def test_assemble_chunks_hard_cap_still_respected():
    rule = "x" * 100
    huge_exception = "Exceção: " + "y" * 200  # estoura até o teto de 1.5×150=225
    chunks = assemble_structural_chunks([rule, huge_exception], chunk_size=150, chunk_overlap=10)
    # Teto duro: não cola — fecha o buffer e a exceção vira chunk próprio.
    assert len(chunks) >= 2
    assert chunks[0].text == rule


def test_assemble_chunks_normal_paragraph_split():
    paragraphs = [f"Parágrafo {i} com algum conteúdo didático razoável." for i in range(10)]
    chunks = assemble_structural_chunks(paragraphs, chunk_size=120, chunk_overlap=10)
    assert all(isinstance(c, StructuredChunk) for c in chunks)
    assert all(c.section_title is None for c in chunks)
    assert len(chunks) > 1
    joined = " ".join(c.text for c in chunks)
    assert "Parágrafo 0" in joined and "Parágrafo 9" in joined


def test_assemble_chunks_oversized_buffer_recursive_split_keeps_title():
    paragraphs = ["## Seção Única", "palavra " * 800]  # >> hard cap
    chunks = assemble_structural_chunks(paragraphs, chunk_size=300, chunk_overlap=50)
    assert len(chunks) > 1
    assert all(c.section_title == "Seção Única" for c in chunks)


# ==============================================================================
# F6 — Roteador de referências exatas
# ==============================================================================

def test_route_exercise_dotted():
    route = route_query("como resolver o exercício 3.2 do livro?")
    assert route.chapter_ref == 3
    assert route.exercise_ref == "cap3_ex2"
    assert route.has_exact_reference
    assert any("exercício 3.2" in t.lower() for t in route.exact_terms)


def test_route_exercise_with_chapter():
    route = route_query("me ajuda no exercício 2 do capítulo 3")
    assert route.chapter_ref == 3
    assert route.question_number == 2
    assert route.exercise_ref == "cap3_ex2"


def test_route_question_and_lesson():
    route = route_query("não entendi a questão 5 da aula 12")
    assert route.lesson_ref == 12
    assert route.question_number == 5


def test_route_chapter_only():
    route = route_query("o que cai do capítulo 7?")
    assert route.chapter_ref == 7
    assert route.exercise_ref is None


def test_route_acronyms_and_stoplist():
    route = route_query("o que significa PIB? E o que é CIDE?")
    assert "PIB" in route.exact_terms
    assert "CIDE" in route.exact_terms
    route2 = route_query("OK ME EXPLICA ISSO")
    assert "OK" not in route2.exact_terms
    assert "ME" not in route2.exact_terms


@pytest.mark.parametrize("query,expected_term", [
    ("o que diz a NBR 6118 sobre flexão?", "NBR 6118"),
    ("a ABNT NBR 14724 trata de formatação", "ABNT NBR 14724"),
    ("requisitos da ISO 9001", "ISO 9001"),
    ("a Lei 8.666/93 foi revogada?", "Lei 8.666/93"),
    ("o que muda com o Decreto 10.024?", "Decreto 10.024"),
    ("resumo da Resolução 466/12", "Resolução 466/12"),
])
def test_route_norms_and_laws(query, expected_term):
    route = route_query(query)
    assert any(expected_term.casefold() == t.casefold() for t in route.exact_terms), route.exact_terms


def test_route_plain_conceptual_query_has_no_reference():
    route = route_query("me explica fotossíntese de um jeito simples")
    assert route.has_exact_reference is False
    assert route.lesson_ref is None and route.chapter_ref is None


def test_sparse_query_text_reinforces_exact_terms():
    route = route_query("o que diz a NBR 6118?")
    reinforced = sparse_query_text("o que diz a NBR 6118?", route)
    # O termo literal aparece pelo menos 2x (original + reforço).
    assert reinforced.upper().count("NBR 6118") >= 2

    plain_route = route_query("me explica fotossíntese")
    assert sparse_query_text("me explica fotossíntese", plain_route) == "me explica fotossíntese"


# ==============================================================================
# F5 — Limiares do CRAG-lite e Recusa Honesta
# ==============================================================================

@pytest.mark.parametrize("score,expected", [
    (0.95, EVIDENCE_GREEN),
    (0.50, EVIDENCE_GREEN),   # borda: >= GREEN é verde
    (0.49, EVIDENCE_YELLOW),
    (0.20, EVIDENCE_YELLOW),  # borda: >= RED é amarelo
    (0.19, EVIDENCE_RED),
    (0.0, EVIDENCE_RED),
    (None, EVIDENCE_RED),     # nenhum candidato = vermelho
])
def test_classify_evidence_thresholds(score, expected):
    assert classify_evidence(score, green=0.5, red=0.2) == expected


def test_build_abstention_message_enriches_teacher_message():
    message = build_abstention_message("Foque no material da disciplina!")
    assert message.startswith("Foque no material da disciplina!")
    assert "professor" in message.lower()
    assert "reformular" in message.lower()


def test_build_abstention_message_handles_empty_out_of_scope():
    message = build_abstention_message("")
    assert "Não encontrei" in message


# ==============================================================================
# F4 — Reranker: factory e fallback silencioso
# ==============================================================================

class _FakeReranker:
    name = "fake"

    def __init__(self, results=None, raises=False):
        self._results = results or []
        self._raises = raises

    async def rerank(self, query, documents, top_n, instruction=None):
        if self._raises:
            raise TimeoutError("reranker fora do ar")
        return self._results[:top_n]


def test_get_reranker_none_is_passthrough():
    assert get_reranker("none") is None
    assert get_reranker("") is None


def test_get_reranker_unknown_provider_returns_none():
    assert get_reranker("provedor-inexistente") is None


@pytest.mark.asyncio
async def test_rerank_with_fallback_returns_ordered_results():
    fake = _FakeReranker(results=[
        RerankResult(index=2, score=0.9),
        RerankResult(index=0, score=0.4),
    ])
    results = await rerank_with_fallback("q", ["a", "b", "c"], top_n=2, provider=fake)
    assert [r.index for r in results] == [2, 0]
    assert results[0].score == 0.9


@pytest.mark.asyncio
async def test_rerank_with_fallback_silent_on_failure():
    fake = _FakeReranker(raises=True)
    results = await rerank_with_fallback("q", ["a", "b"], top_n=2, provider=fake)
    assert results is None  # chamador mantém a ordem da fusão


@pytest.mark.asyncio
async def test_rerank_with_fallback_empty_documents():
    fake = _FakeReranker(results=[RerankResult(index=0, score=1.0)])
    assert await rerank_with_fallback("q", [], top_n=2, provider=fake) is None


# ==============================================================================
# F3 — Fallback híbrido → dense no estágio de busca (qdrant mockado)
# ==============================================================================

class _FakeQueryResponse:
    def __init__(self, points):
        self.points = points


class _FakePoint:
    def __init__(self, pid, score=0.5, payload=None):
        self.id = pid
        self.score = score
        self.payload = payload or {"content": f"chunk {pid}"}


class _FakeQdrant:
    """Falha quando recebe prefetch (fusão) e funciona no caminho dense."""

    def __init__(self):
        self.calls = []

    async def query_points(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("prefetch"):
            raise RuntimeError("fusão indisponível")
        return _FakeQueryResponse([_FakePoint("p1"), _FakePoint("p2")])


@pytest.mark.asyncio
async def test_search_stage_hybrid_falls_back_to_dense():
    from src.rag.retriever import _search_stage
    from qdrant_client.models import Filter

    fake = _FakeQdrant()
    results = await _search_stage(
        fake,
        named_dense=True,
        dense_vector=[0.1, 0.2],
        sparse_vector={"indices": [1], "values": [0.5]},
        query_filter=Filter(must=[]),
        limit=10,
        score_threshold=0.2,
    )
    # 1ª chamada (híbrida) falhou → 2ª chamada (dense) respondeu.
    assert len(fake.calls) == 2
    assert fake.calls[0].get("prefetch") is not None
    assert fake.calls[1].get("prefetch") is None
    assert [r.id for r in results] == ["p1", "p2"]


@pytest.mark.asyncio
async def test_search_stage_dense_only_without_sparse():
    from src.rag.retriever import _search_stage
    from qdrant_client.models import Filter

    fake = _FakeQdrant()
    results = await _search_stage(
        fake, named_dense=False, dense_vector=[0.1], sparse_vector=None,
        query_filter=Filter(must=[]), limit=5, score_threshold=0.3,
    )
    assert len(fake.calls) == 1
    assert fake.calls[0].get("using") is None  # collection legada: vetor sem nome
    assert len(results) == 2


# ==============================================================================
# Retriever — helpers puros
# ==============================================================================

def test_build_context_includes_section_title():
    from src.rag.retriever import RetrievedHit, _build_context

    hits = [
        RetrievedHit(id="1", score=0.9, payload={
            "content": "A regra e sua exceção.", "source": "Aula3.pdf",
            "lesson_number": 3, "section_title": "Termodinâmica",
        }),
        RetrievedHit(id="2", score=0.8, payload={
            "content": "Sem seção.", "source": "Aula1.pdf", "lesson_number": 1,
        }),
    ]
    context = _build_context(hits)
    assert "[Fonte: Aula3.pdf | Material da Aula: 3 | Seção: Termodinâmica]" in context
    assert "[Fonte: Aula1.pdf | Material da Aula: 1]" in context


def test_judge_text_uses_context_prefix():
    from src.rag.retriever import _judge_text

    assert _judge_text({"content": "corpo"}) == "corpo"
    assert _judge_text({"content": "corpo", "context_prefix": "Capítulo 3"}) == "Capítulo 3\ncorpo"


def test_extract_lesson_hint_via_router():
    from src.rag.retriever import _extract_lesson_hint

    assert _extract_lesson_hint("tópicos da aula 3") == 3
    assert _extract_lesson_hint("o que foi visto na lição 2?") == 2
    assert _extract_lesson_hint("resumo do capítulo 5") == 5
    assert _extract_lesson_hint("explique XYZ") is None


# ==============================================================================
# F7 — Long-context: decisão de bypass (helpers puros)
# ==============================================================================

def test_corpus_scope_key():
    assert corpus_scope_key("c1", "Física", "book") == "c1:Física:book"
    assert corpus_scope_key("c1", None, None) == "c1:-:-"


@pytest.mark.parametrize("chars,enabled,max_chars,expected", [
    (100_000, True, 150_000, True),
    (150_000, True, 150_000, True),    # borda: igual ao limite entra
    (150_001, True, 150_000, False),
    (100_000, False, 150_000, False),  # flag off = nunca
    (None, True, 150_000, False),      # tamanho desconhecido = RAG normal
    (0, True, 150_000, False),
])
def test_should_bypass_rag(chars, enabled, max_chars, expected):
    assert should_bypass_rag(chars, enabled=enabled, max_chars=max_chars) is expected


# ==============================================================================
# F2 — Prompt de enriquecimento (str.format, prefixo fixo p/ cache)
# ==============================================================================

def test_contextual_enrichment_prompt_prefix_order():
    from src.shared.constants.prompts import CONTEXTUAL_ENRICHMENT_PROMPT

    rendered = CONTEXTUAL_ENRICHMENT_PROMPT.format(
        document_excerpt="EXCERTO-DO-DOC", chunk="CHUNK-VARIÁVEL",
    )
    # O excerto (fixo entre chamadas) PRECEDE o chunk — é o que o prompt
    # caching do provider reaproveita.
    assert rendered.index("EXCERTO-DO-DOC") < rendered.index("CHUNK-VARIÁVEL")


def test_query_rewrite_prompt_formats():
    from src.shared.constants.prompts import QUERY_REWRITE_PROMPT

    rendered = QUERY_REWRITE_PROMPT.format(
        query="o que é X?", subject_name="Física", lesson_number=4,
    )
    assert "o que é X?" in rendered and "Física" in rendered


def test_build_document_excerpt_caps_and_includes_outline():
    from src.workers.ingestion_worker import build_document_excerpt

    full_text = "começo do documento " * 50
    paragraphs = ["## Capítulo 1", "texto", "## Capítulo 2"]
    excerpt = build_document_excerpt(full_text, paragraphs, max_chars=400)
    assert len(excerpt) <= 400
    assert "SUMÁRIO DE SEÇÕES" in excerpt
    assert "Capítulo 1" in excerpt


# ==============================================================================
# F0 — Métricas do eval (núcleo determinístico)
# ==============================================================================

def test_eval_metrics_helpers():
    import importlib.util
    import os

    spec = importlib.util.spec_from_file_location(
        "eval_rag", os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "eval_rag.py"),
    )
    eval_rag = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(eval_rag)

    # substring_hit (case-insensitive)
    assert eval_rag.substring_hit(["A NBR 6118 define..."], ["nbr 6118"]) is True
    assert eval_rag.substring_hit(["outro texto"], ["nbr 6118"]) is False
    assert eval_rag.substring_hit([], ["x"]) is False
    assert eval_rag.substring_hit(["texto"], []) is False

    # source_hit (match por substring)
    assert eval_rag.source_hit(["Aula3_v2.pdf"], ["aula3"]) is True
    assert eval_rag.source_hit(["Outro.pdf"], ["aula3"]) is False

    # first_hit_rank / MRR
    assert eval_rag.first_hit_rank(["nada", "tem NBR 6118 aqui"], ["NBR 6118"]) == 2
    assert eval_rag.first_hit_rank(["nada"], ["NBR 6118"]) is None
    assert eval_rag.mean_reciprocal_rank([1, 2, None]) == pytest.approx((1 + 0.5 + 0) / 3)
    assert eval_rag.mean_reciprocal_rank([]) == 0.0

    # summarize: recall, abstenção correta e falsos-recusados
    records = [
        {"category": "termos_exatos", "recall_hit": True, "source_hit": True, "rank": 1, "abstained": False, "latency_ms": 100.0},
        {"category": "conceitual", "recall_hit": False, "source_hit": False, "rank": None, "abstained": True, "latency_ms": 200.0},
        {"category": "fora_do_escopo", "recall_hit": False, "source_hit": False, "rank": None, "abstained": True, "latency_ms": 150.0},
        {"category": "fora_do_escopo", "recall_hit": False, "source_hit": False, "rank": None, "abstained": False, "latency_ms": 120.0},
    ]
    summary = eval_rag.summarize(records)
    assert summary["recall_at_k"] == pytest.approx(0.5)
    assert summary["correct_abstention_rate"] == pytest.approx(0.5)
    assert summary["false_refusal_rate"] == pytest.approx(0.5)
    assert summary["mrr"] == pytest.approx((1.0 + 0.0) / 2)
    assert summary["by_category"]["termos_exatos"]["recall"] == 1.0
