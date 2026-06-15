"""Testes do roteador adaptável de ingestão de matemática (M2).

PUROS: detecção por nome, sonda de densidade e composição (com `settings`
monkeypatchado). Rodar:
    pytest tests/unit/test_math_routing.py -v
"""

import pytest

from src.shared.config import settings
from src.math.routing import (
    choose_parser_backend,
    detect_exatas_course,
    is_math_dense,
    math_density,
    needs_density_probe,
)


# ==============================================================================
# Detecção por nome (convenção sobre configuração)
# ==============================================================================

@pytest.mark.parametrize("name", [
    "Cálculo I",
    "Física Quântica",
    "Estatística Aplicada",
    "Química Orgânica",
    "Matemática Discreta",
    "Álgebra Linear",
    "Geometria Analítica",
    "Probabilidade e Estatística",
    "[exatas] Tópicos Especiais",  # tag autoritativa, sem palavra-chave
])
def test_detect_exatas_true(name):
    assert detect_exatas_course(name) is True


@pytest.mark.parametrize("name", [
    "História da Arte",
    "Literatura Brasileira",
    "Educação Física",                 # tem "fisica", mas é stoplist
    "Filosofia da Matemática",         # tem "matematica", mas é stoplist
    "Sociologia",
    "",
])
def test_detect_exatas_false(name):
    assert detect_exatas_course(name) is False


def test_detect_exatas_tag_overrides_stoplist():
    # Professor força exatas mesmo num nome da stoplist.
    assert detect_exatas_course("[exatas] Educação Física Aplicada") is True


def test_detect_exatas_handles_none():
    assert detect_exatas_course(None) is False  # type: ignore[arg-type]


# ==============================================================================
# Sonda de densidade matemática
# ==============================================================================

def test_math_density_high_for_math_text():
    math_text = r"A derivada $\frac{df}{dx}$ e a integral $\int_0^1 x^2\,dx$ da função quadrática."
    prose_text = "O documento descreve a história da arte renascentista e seus principais autores europeus."
    assert math_density(math_text) > math_density(prose_text)
    assert math_density(prose_text) < 3.0


def test_math_density_survives_scrambled_text_via_vocabulary():
    # PDF nato-digital embaralha o LaTeX: sem '$', mas o vocabulário sobrevive.
    scrambled = "A equacao da funcao quadratica tem uma derivada e a integral do polinomio resulta..."
    assert math_density(scrambled) > 3.0


def test_is_math_dense_threshold():
    assert is_math_dense("equação derivada integral função teorema vetor", threshold=3.0) is True
    assert is_math_dense("texto comum sobre cultura e arte", threshold=3.0) is False


def test_math_density_empty():
    assert math_density("") == 0.0


# ==============================================================================
# Composição (lê settings) — flag OFF preserva comportamento atual
# ==============================================================================

def test_choose_backend_none_when_flag_off(monkeypatch):
    monkeypatch.setattr(settings, "MATH_INGESTION_ENABLED", False)
    assert choose_parser_backend("Cálculo I") is None
    assert choose_parser_backend("Física", probe_text="integral derivada equação função") is None


def test_choose_backend_marker_for_exatas_by_name(monkeypatch):
    monkeypatch.setattr(settings, "MATH_INGESTION_ENABLED", True)
    monkeypatch.setattr(settings, "MATH_PARSER_BACKEND", "marker")
    assert choose_parser_backend("Cálculo I") == "marker"


def test_choose_backend_marker_for_dense_probe(monkeypatch):
    monkeypatch.setattr(settings, "MATH_INGESTION_ENABLED", True)
    monkeypatch.setattr(settings, "MATH_PARSER_BACKEND", "marker")
    monkeypatch.setattr(settings, "MATH_DENSITY_THRESHOLD", 3.0)
    dense = "equacao funcao derivada integral teorema polinomio vetor matriz"
    assert choose_parser_backend("Tópicos Gerais", probe_text=dense) == "marker"


def test_choose_backend_none_for_neutral_without_dense_probe(monkeypatch):
    monkeypatch.setattr(settings, "MATH_INGESTION_ENABLED", True)
    assert choose_parser_backend("História Geral", probe_text="texto comum sobre cultura") is None
    assert choose_parser_backend("História Geral", probe_text=None) is None


def test_needs_density_probe(monkeypatch):
    monkeypatch.setattr(settings, "MATH_INGESTION_ENABLED", False)
    assert needs_density_probe("História") is False  # flag off

    monkeypatch.setattr(settings, "MATH_INGESTION_ENABLED", True)
    assert needs_density_probe("Cálculo I") is False  # nome já decidiu → sem sonda
    assert needs_density_probe("História Geral") is True  # neutro → vale sondar
