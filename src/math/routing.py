"""
M2 — Roteador adaptável: decide QUANDO usar o parser ciente de matemática.

Pagar VLM em todo PDF (inclusive de História/Literatura) é desperdício. Mesmo
espírito do `detect_language_course` (convenção sobre configuração), em camadas:

  1. Tag autoritativa `[exatas]` no nome do curso/disciplina → força matemática.
  2. Detecção por NOME (Cálculo/Física/Estatística/Química/...) — barata, roda
     ANTES de qualquer parsing. É o sinal forte.
  3. Sonda de densidade matemática sobre uma amostra de texto — rede de
     segurança para uma disciplina de nome "neutro" que na prática é cheia de
     fórmula. BEST-EFFORT: num PDF nato-digital o LaTeX vem embaralhado na
     camada de texto, então a sonda olha vocabulário + operadores + símbolos,
     não só marcadores LaTeX.

Tudo atrás de `MATH_INGESTION_ENABLED` (default OFF): flag desligada =
comportamento atual byte-a-byte (parser padrão sempre). Helpers PUROS.
"""

from __future__ import annotations

import re
import unicodedata

from src.shared.config import settings


def _normalize(text: str) -> str:
    """Minúsculo + sem acentos (NFKD) — casamento robusto. Puro."""
    nfkd = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


# Tag explícita do professor (vence stoplist) — ex.: "[exatas] Tópicos Especiais".
_EXATAS_TAG_RE = re.compile(r"\[\s*exatas\s*\]")

# Disciplinas de exatas por palavra-chave (nome normalizado, sem acento).
_EXATAS_KEYWORDS = re.compile(
    r"\b(?:"
    r"calculo|fisica|estatistica|quimica|matematica|algebra|geometria"
    r"|trigonometria|mecanica|termodinamica|eletromagnetismo|probabilidade"
    r"|precalculo|biofisica"
    r")\b"
)

# Falsos-positivos que carregam uma palavra-chave mas NÃO são exatas — exigem a
# tag explícita (ex.: "Educação Física" tem "fisica"; "Filosofia da Matemática").
_EXATAS_STOPLIST = (
    "educacao fisica",
    "historia da",
    "filosofia",
    "sociologia",
)

# Vocabulário matemático em PT (radicais) — sobrevive à extração embaralhada do
# PDF nato-digital, onde os marcadores LaTeX se perdem. Comparado no normalizado.
_MATH_VOCAB = re.compile(
    r"\b(?:"
    r"equac|formula|integral|derivad|teorema|funcao|funcoes|vetor|matriz"
    r"|logaritm|seno|cosseno|tangente|polinomi|hipotenusa|trigonometr"
    r"|algebr|geometr|probabilidade|variancia|derivada|equacoes"
    r")\w*"
)

_LATEX_CMD = re.compile(r"\\[a-zA-Z]+")
# Operadores/símbolos matemáticos que aparecem mesmo em texto cru (não-LaTeX).
_MATH_SYMBOLS = frozenset("=±×÷≤≥≠≈∫∑∏√πθλσμΔΩ∞∂∇²³^_")


def detect_exatas_course(name: str) -> bool:
    """True se o nome do curso/disciplina indica exatas. Puro.

    Precedência: tag `[exatas]` (autoritativa) → stoplist (exige tag) →
    palavra-chave de disciplina.
    """
    if not name:
        return False
    norm = _normalize(name)
    if _EXATAS_TAG_RE.search(norm):
        return True
    if any(stop in norm for stop in _EXATAS_STOPLIST):
        return False
    return bool(_EXATAS_KEYWORDS.search(norm))


def math_density(text: str) -> float:
    """Indicadores de matemática por 1000 caracteres (vocabulário + operadores +
    marcadores LaTeX). Puro. Best-effort: alta em material de exatas, ~0 em prosa."""
    if not text:
        return 0.0
    norm = _normalize(text)
    indicators = (
        len(_MATH_VOCAB.findall(norm))
        + text.count("$")
        + len(_LATEX_CMD.findall(text))
        + sum(1 for ch in text if ch in _MATH_SYMBOLS)
    )
    return indicators / len(text) * 1000.0


def is_math_dense(text: str, threshold: float) -> bool:
    """True se a densidade matemática do texto atinge o limiar. Puro."""
    return math_density(text) >= threshold


def needs_density_probe(subject_name: str | None) -> bool:
    """True se vale rodar a sonda (flag ligada e o nome NÃO decidiu) — evita a
    extração local à toa quando a disciplina já é exatas pelo nome."""
    return settings.MATH_INGESTION_ENABLED and not detect_exatas_course(subject_name or "")


def choose_parser_backend(subject_name: str | None, probe_text: str | None = None) -> str | None:
    """Nome do parser a usar, ou None (= parser padrão).

    None sempre que `MATH_INGESTION_ENABLED` está OFF — preserva o comportamento
    atual byte-a-byte. Ligada: disciplina de exatas pelo nome OU documento
    math-denso na sonda → `MATH_PARSER_BACKEND`.
    """
    if not settings.MATH_INGESTION_ENABLED:
        return None
    if detect_exatas_course(subject_name or ""):
        return settings.MATH_PARSER_BACKEND
    if probe_text and is_math_dense(probe_text, settings.MATH_DENSITY_THRESHOLD):
        return settings.MATH_PARSER_BACKEND
    return None
