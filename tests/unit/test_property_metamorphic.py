"""Testes baseados em propriedades (§13.5 Hypothesis) e metamórficos (§13.3).

Endurecem os helpers PUROS de segurança/RAG: o framework gera centenas de
entradas e busca o menor contraexemplo que quebra uma propriedade invariante.

Eixos cobertos:
- Não-vazamento de PII na máscara de contato (a saída revela ≤ 4 chars).
- Minimização do nome para o LLM (nunca além do 1º token).
- Robustez e contratos do query_router (não quebra com entrada hostil).
- CRAG `classify_evidence`: partição completa + MONOTONICIDADE (baixar CRAG_RED
  só pode manter ou AUMENTAR as respostas — nunca recusar mais; §13.3).
"""
from hypothesis import given, strategies as st

from src.shared.privacy import first_name, llm_safe_name, mask_contact
from src.shared.telemetry import _mask_value
from src.rag.query_router import route_query, RouteResult, _ACRONYM_STOPLIST
from src.rag.crag import classify_evidence, EVIDENCE_GREEN, EVIDENCE_YELLOW, EVIDENCE_RED

_EVIDENCE_RANK = {EVIDENCE_RED: 0, EVIDENCE_YELLOW: 1, EVIDENCE_GREEN: 2}


# ───────────────────────── PII: não-vazamento da máscara ──────────────────────
@given(st.one_of(st.text(min_size=1), st.integers()))
def test_mask_contact_reveals_at_most_4_chars(value):
    masked = mask_contact(value)
    s = str(value)
    # Propriedade de não-vazamento: a máscara revela no máximo 4 chars
    # (vazio/falsy → sentinela "?" que não revela nada).
    assert len(masked) <= 5
    if len(s) > 4:
        assert s not in masked          # original longo nunca cabe → não vaza
        assert masked == "…" + s[-4:]   # exatamente os 4 últimos


@given(st.one_of(st.text(min_size=5), st.integers(min_value=10000)))
def test_telemetry_mask_value_matches_contract(value):
    masked = _mask_value(value)
    assert masked.startswith("…")
    assert len(masked) <= 5
    assert masked[1:] == str(value)[-4:]


# ───────────────────────── minimização do nome p/ LLM ────────────────────────
@given(st.text())
def test_first_name_is_single_token_never_empty(full):
    fn = first_name(full)
    assert fn  # nunca vazio
    assert len(fn.split()) == 1  # sempre um único token (sem sobrenome)


@given(st.text())
def test_llm_safe_name_never_exceeds_full_name(full):
    out = llm_safe_name(full)
    assert out
    # Com a flag ON (default) → 1º nome; com OFF → nome completo. Em ambos os
    # casos a saída pertence ao conjunto seguro previsto (nunca inventa dado).
    assert out in (first_name(full), (full or "").strip() or "aluno(a)")


# ───────────────────────── query_router: robustez/contrato ───────────────────
@given(st.text())
def test_route_query_never_raises_and_typed(query):
    r = route_query(query)
    assert isinstance(r, RouteResult)
    assert isinstance(r.has_exact_reference, bool)
    for v in (r.lesson_ref, r.chapter_ref, r.question_number):
        assert v is None or (isinstance(v, int) and v >= 0)
    # Siglas detectadas: 2-6 chars, maiúsculas, fora da stoplist.
    for term in r.exact_terms:
        assert isinstance(term, str) and term


@given(st.sampled_from(sorted(_ACRONYM_STOPLIST)))
def test_router_stoplist_words_are_not_emitted_as_acronyms(word):
    # Uma palavra da stoplist, sozinha, nunca vira sigla/termo exato.
    r = route_query(word)
    assert word not in r.exact_terms


# ───────────────────────── CRAG: partição + monotonicidade ────────────────────
_score = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)
_thr = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


@given(_score, _thr, _thr)
def test_classify_evidence_total_partition(score, a, b):
    green, red = max(a, b), min(a, b)
    assert classify_evidence(score, green=green, red=red) in _EVIDENCE_RANK


def test_classify_evidence_none_is_red():
    assert classify_evidence(None, green=0.5, red=0.2) == EVIDENCE_RED


@given(_score, _score, _thr, _thr)
def test_classify_monotonic_in_score(s1, s2, a, b):
    """Score maior NUNCA produz evidência pior (red<yellow<green)."""
    green, red = max(a, b), min(a, b)
    hi, lo = max(s1, s2), min(s1, s2)
    assert _EVIDENCE_RANK[classify_evidence(hi, green=green, red=red)] >= \
        _EVIDENCE_RANK[classify_evidence(lo, green=green, red=red)]


@given(_score, _thr, _thr, _thr)
def test_mr_lowering_crag_red_never_refuses_more(score, green_raw, r1, r2):
    """MR-monotonicidade do CRAG (§13.3): baixar CRAG_RED só pode MANTER ou
    AUMENTAR as respostas — jamais recusar (🔴) algo que antes era respondido."""
    green = max(green_raw, r1, r2)  # garante green >= ambos os reds
    red_low, red_high = min(r1, r2), max(r1, r2)
    ev_low = classify_evidence(score, green=green, red=red_low)
    ev_high = classify_evidence(score, green=green, red=red_high)
    # Com o red MENOR, a evidência é no mínimo tão permissiva quanto com o maior.
    assert _EVIDENCE_RANK[ev_low] >= _EVIDENCE_RANK[ev_high]
    # Em particular: o que não era recusa com red_high segue não sendo com red_low.
    if ev_high != EVIDENCE_RED:
        assert ev_low != EVIDENCE_RED
