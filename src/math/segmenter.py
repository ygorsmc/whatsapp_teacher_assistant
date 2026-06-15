"""M6 — Segmentação da resposta para entrega no WhatsApp.

Uma resposta do LLM pode misturar prosa, math inline (``$...$``) e fórmulas em
display (``$$...$$``, ``\\[...\\]``, ``\\begin..\\end``). O WhatsApp NÃO renderiza
LaTeX (cf. "Pipeline RAG para SaaS Educacional" §5) — mandar ``\\frac{a}{b}`` cru
polui a tela e desengaja o aluno. Aqui, PURO e testável, decidimos o destino de
cada pedaço:

  - math SIMPLES / linear  → transpila para Unicode e fica no texto (α, x², ≤);
  - math COMPLEXA (fração, integral, matriz, sistema...) → vira ``ImageSegment``,
    que a camada de canal renderiza como imagem e envia ENTRE dois blocos de
    texto. É o "texto → fórmula (imagem) → texto" pedido: uma resposta lógica
    pode virar várias mensagens.

A renderização (LaTeX→imagem) e o despacho intercalado moram na fase seguinte
(``IMathRenderer`` + ``send_image``); este módulo só classifica O QUE é texto e
O QUE é imagem, e deixa o texto legível. Helpers puros — sem I/O, sem rede.
"""

import re
from dataclasses import dataclass

__all__ = [
    "TextSegment",
    "ImageSegment",
    "ChartSegment",
    "Segment",
    "segment_answer",
    "latex_to_unicode",
    "is_complex_math",
]


@dataclass
class TextSegment:
    """Trecho de prosa pronto para ``send_text`` (math inline já em Unicode)."""
    text: str


@dataclass
class ImageSegment:
    """Fórmula a renderizar como imagem.

    `latex` é o conteúdo SEM os delimitadores ``$$``/``\\[`` (para ambientes
    ``\\begin..\\end`` vem o bloco inteiro, que o renderer precisa intacto).
    `display` distingue a origem (display vs inline complexo) — útil só para o
    renderer escolher o enquadramento.
    """
    latex: str
    display: bool = True


@dataclass
class ChartSegment:
    """Gráfico (M7) a renderizar como imagem.

    `raw` é o JSON cru do bloco ```grafico``` — o parse/validação de segurança
    e o plot moram na camada de render (`src/math/charts.py`). Aqui só marcamos
    a POSIÇÃO no fluxo (intercalado entre prosa), igual ao `ImageSegment`. Se o
    spec for inválido ou o render falhar, a entrega DESCARTA o gráfico e mantém
    o texto (feature ilustrativa, não essencial).
    """
    raw: str


Segment = TextSegment | ImageSegment | ChartSegment


# ── Classificador: o que colapsa em Unicode (→ imagem) ───────────────────────
# Comandos cuja estrutura é 2D ou não-linear e perde sentido em texto corrido.
_COMPLEX_MARKERS = re.compile(
    r"\\(?:d?frac|cfrac|[dt]?binom|sqrt|i{1,3}nt|oint|sum|prod|lim|begin"
    r"|matrix|cases|aligned?|substack|overbrace|underbrace|overline|underline"
    r"|vec|hat|bar|tilde|widehat|widetilde)(?![a-zA-Z])"  # não é prefixo de \integer etc.
    r"|\\\\"                       # quebra de linha (matriz / sistema de equações)
    r"|[_^]\s*\{[^}]{2,}\}"        # sub/sobrescrito composto (x^{n+1}, a_{ij})
)


def is_complex_math(latex: str) -> bool:
    """True se a fórmula colapsa em Unicode e deve virar imagem.

    Fração, raiz, integral/somatório com limites, matriz, sistema e
    sub/sobrescritos compostos → imagem. Coisas lineares (``x^2``, ``\\alpha``,
    ``a \\leq b``, ``x_0``) → False (transpilam bem para Unicode).
    """
    return bool(_COMPLEX_MARKERS.search(latex or ""))


# ── Varredura de spans de math (display tem prioridade sobre inline) ─────────
_MATH_SPAN = re.compile(
    r"\$\$(?P<dd>.+?)\$\$"                                   # $$ ... $$
    r"|\\\[(?P<br>.+?)\\\]"                                  # \[ ... \]
    r"|(?P<env>\\begin\{(?P<envname>\w+\*?)\}.*?\\end\{(?P=envname)\})"
    r"|\$(?P<inl>(?!\$).+?)\$",                              # $ ... $ (inline)
    re.DOTALL,
)

# ── M7: bloco de gráfico ```grafico ... ``` (info-string controlada) ─────────
# Só casa fences cuja linguagem é grafico/chart — ```python``` etc. passam
# intactos como texto. NÃO casa nada → segment_answer fica byte-a-byte o M6.
_CHART_FENCE = re.compile(
    r"```[ \t]*(?:grafico|gráfico|chart)[^\n]*\n(?P<chart>.*?)```",
    re.DOTALL | re.IGNORECASE,
)


def segment_answer(markdown: str) -> list[Segment]:
    """Quebra a resposta em segmentos ordenados de texto, imagem e gráfico.

    Primeiro descasca os blocos ```grafico``` (M7); o restante segue o núcleo M6
    (prosa + fórmulas). Texto entre elementos é preservado verbatim; math inline
    simples vira Unicode no lugar; math complexa vira ``ImageSegment``; o bloco de
    gráfico vira ``ChartSegment``. Textos adjacentes são fundidos no fim.

    SEM bloco de gráfico, o resultado é idêntico ao M6 (byte-a-byte).
    """
    if not markdown:
        return []

    segments: list[Segment] = []
    last = 0
    for m in _CHART_FENCE.finditer(markdown):
        if m.start() > last:
            segments.extend(_segment_math(markdown[last:m.start()]))
        segments.append(ChartSegment(raw=m.group("chart").strip()))
        last = m.end()
    if last < len(markdown):
        segments.extend(_segment_math(markdown[last:]))

    return _merge_text_segments(segments)


def _segment_math(markdown: str) -> list[Segment]:
    """Núcleo M6: prosa + fórmulas (inline→Unicode, complexa→imagem). Cru (sem
    merge): chamado por `segment_answer` para cada trecho fora dos gráficos."""
    segments: list[Segment] = []
    last = 0
    for m in _MATH_SPAN.finditer(markdown):
        if m.start() > last:
            segments.append(TextSegment(markdown[last:m.start()]))

        if m.group("dd") is not None:
            latex, display = m.group("dd").strip(), True
        elif m.group("br") is not None:
            latex, display = m.group("br").strip(), True
        elif m.group("env") is not None:
            latex, display = m.group("env").strip(), True
        else:
            latex, display = m.group("inl").strip(), False

        if is_complex_math(latex):
            segments.append(ImageSegment(latex=latex, display=display))
        else:
            unicode_text = latex_to_unicode(latex)
            # Display simples ganha quebras de linha (fica numa linha própria);
            # inline simples flui no meio da frase.
            segments.append(TextSegment(f"\n{unicode_text}\n" if display else unicode_text))
        last = m.end()

    if last < len(markdown):
        segments.append(TextSegment(markdown[last:]))

    return segments


def _merge_text_segments(segments: list[Segment]) -> list[Segment]:
    """Funde textos adjacentes e descarta textos vazios (sem perder espaços
    internos da prosa)."""
    merged: list[Segment] = []
    for seg in segments:
        if isinstance(seg, TextSegment) and merged and isinstance(merged[-1], TextSegment):
            merged[-1] = TextSegment(merged[-1].text + seg.text)
        else:
            merged.append(seg)
    return [s for s in merged if not (isinstance(s, TextSegment) and not s.text.strip())]


# ── Transpilação LaTeX → Unicode ─────────────────────────────────────────────
# Plano A: pylatexenc (recomendado pelo relatório §5.2) — cobre muito mais.
# Plano B (sem o pacote): mapa embutido para os casos lineares mais comuns.
# Import-guarded, mesmo idioma do braço esparso local (fastembed).

_l2t = None
_l2t_resolved = False


def _get_latex2text():
    global _l2t, _l2t_resolved
    if not _l2t_resolved:
        _l2t_resolved = True
        try:
            from pylatexenc.latex2text import LatexNodes2Text
            _l2t = LatexNodes2Text()
        except Exception:
            _l2t = None
    return _l2t


def latex_to_unicode(latex: str) -> str:
    """Converte LaTeX linear em texto Unicode legível (α, x², ≤, →).

    Usa pylatexenc quando disponível; senão, o mapa embutido. Nunca levanta —
    no pior caso devolve o melhor esforço do fallback.
    """
    source = (latex or "").strip()
    if not source:
        return ""
    engine = _get_latex2text()
    if engine is not None:
        try:
            converted = engine.latex_to_text(source).strip()
            if converted:
                return converted
        except Exception:
            pass
    return _unicode_fallback(source)


_SYMBOLS = {
    r"\alpha": "α", r"\beta": "β", r"\gamma": "γ", r"\delta": "δ",
    r"\epsilon": "ε", r"\varepsilon": "ε", r"\zeta": "ζ", r"\eta": "η",
    r"\theta": "θ", r"\iota": "ι", r"\kappa": "κ", r"\lambda": "λ",
    r"\mu": "μ", r"\nu": "ν", r"\xi": "ξ", r"\pi": "π", r"\rho": "ρ",
    r"\sigma": "σ", r"\tau": "τ", r"\upsilon": "υ", r"\phi": "φ",
    r"\varphi": "φ", r"\chi": "χ", r"\psi": "ψ", r"\omega": "ω",
    r"\Gamma": "Γ", r"\Delta": "Δ", r"\Theta": "Θ", r"\Lambda": "Λ",
    r"\Pi": "Π", r"\Sigma": "Σ", r"\Phi": "Φ", r"\Psi": "Ψ", r"\Omega": "Ω",
    r"\infty": "∞", r"\partial": "∂", r"\nabla": "∇",
    r"\pm": "±", r"\mp": "∓", r"\times": "×", r"\cdot": "⋅", r"\div": "÷",
    r"\ast": "∗", r"\star": "⋆", r"\circ": "∘", r"\bullet": "•",
    r"\leq": "≤", r"\le": "≤", r"\geq": "≥", r"\ge": "≥",
    r"\neq": "≠", r"\ne": "≠", r"\approx": "≈", r"\equiv": "≡",
    r"\sim": "∼", r"\propto": "∝", r"\cong": "≅",
    r"\Rightarrow": "⇒", r"\Leftarrow": "⇐", r"\Leftrightarrow": "⇔",
    r"\rightarrow": "→", r"\leftarrow": "←", r"\leftrightarrow": "↔",
    r"\to": "→", r"\mapsto": "↦",
    r"\in": "∈", r"\notin": "∉", r"\subset": "⊂", r"\subseteq": "⊆",
    r"\supset": "⊃", r"\cup": "∪", r"\cap": "∩", r"\emptyset": "∅",
    r"\forall": "∀", r"\exists": "∃", r"\neg": "¬",
    r"\land": "∧", r"\lor": "∨", r"\wedge": "∧", r"\vee": "∨",
    r"\angle": "∠", r"\perp": "⊥", r"\parallel": "∥",
    r"\degree": "°", r"\prime": "′",
    r"\ldots": "…", r"\cdots": "⋯", r"\dots": "…",
    r"\mathbb{R}": "ℝ", r"\mathbb{N}": "ℕ", r"\mathbb{Z}": "ℤ",
    r"\mathbb{Q}": "ℚ", r"\mathbb{C}": "ℂ",
    r"\%": "%", r"\&": "&", r"\$": "$", r"\#": "#",
}

_SUPERSCRIPT = str.maketrans(
    "0123456789n+-=()i", "⁰¹²³⁴⁵⁶⁷⁸⁹ⁿ⁺⁻⁼⁽⁾ⁱ"
)
_SUBSCRIPT = str.maketrans(
    "0123456789+-=()", "₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎"
)


def _unicode_fallback(s: str) -> str:
    """Mapa embutido (Plano B): símbolos comuns + sub/sobrescritos de 1 char."""
    # Chaves longas primeiro (\\infty antes de \\in) para não casar prefixo.
    for key in sorted(_SYMBOLS, key=len, reverse=True):
        s = s.replace(key, _SYMBOLS[key])

    # Sub/sobrescritos: ^{x}, _{x} de 1 char e ^x, _x diretos.
    s = re.sub(r"\^\{([0-9n+\-=()i])\}", lambda m: m.group(1).translate(_SUPERSCRIPT), s)
    s = re.sub(r"_\{([0-9+\-=()])\}", lambda m: m.group(1).translate(_SUBSCRIPT), s)
    s = re.sub(r"\^([0-9n+\-=()i])", lambda m: m.group(1).translate(_SUPERSCRIPT), s)
    s = re.sub(r"_([0-9+\-=()])", lambda m: m.group(1).translate(_SUBSCRIPT), s)

    # Espaçamentos LaTeX (\, \; \! \: \quad) e \left \right viram nada/espaço.
    s = re.sub(r"\\(?:quad|qquad)", " ", s)
    s = re.sub(r"\\[,;:!]", "", s)
    s = s.replace(r"\left", "").replace(r"\right", "")
    s = re.sub(r"[{}]", "", s)
    s = re.sub(r"[ \t]+", " ", s).strip()
    return s
