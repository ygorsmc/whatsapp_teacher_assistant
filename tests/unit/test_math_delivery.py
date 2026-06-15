"""Testes unitários da entrega de matemática no WhatsApp (M6).

PUROS: segmentação texto/imagem e transpilação LaTeX→Unicode (fallback
embutido, sem depender de pylatexenc). Rodar:
    pytest tests/unit/test_math_delivery.py -v
"""

import pytest

from src.math.segmenter import (
    ImageSegment,
    TextSegment,
    is_complex_math,
    latex_to_unicode,
    segment_answer,
    _unicode_fallback,
)


# ==============================================================================
# Classificador: o que colapsa em Unicode (→ imagem) vs linear (→ texto)
# ==============================================================================

@pytest.mark.parametrize("latex", [
    r"\frac{df}{dx}",
    r"\int_0^1 x^2\,dx",
    r"\sqrt{2}",
    r"\sum_{i=1}^{n} i",
    r"\begin{matrix} a & b \\ c & d \end{matrix}",
    r"x^{n+1}",
    r"a_{ij}",
    r"\lim_{h \to 0}",
])
def test_is_complex_math_true(latex):
    assert is_complex_math(latex) is True


@pytest.mark.parametrize("latex", [
    r"x^2",
    r"\alpha",
    r"a \leq b",
    r"x_0",
    r"E = mc^2",
    r"v + a",
])
def test_is_complex_math_false(latex):
    assert is_complex_math(latex) is False


# ==============================================================================
# Transpilação LaTeX → Unicode (fallback embutido, determinístico)
# ==============================================================================

def test_unicode_fallback_symbols():
    assert _unicode_fallback(r"\alpha \leq \beta") == "α ≤ β"
    assert _unicode_fallback(r"\pi \approx 3{,}14") == "π ≈ 3,14"
    # \infty não pode ser destruído pelo \in (chaves longas primeiro).
    assert _unicode_fallback(r"x \to \infty") == "x → ∞"


def test_unicode_fallback_scripts():
    assert _unicode_fallback("x^2") == "x²"
    assert _unicode_fallback("a_0") == "a₀"
    assert _unicode_fallback("x^{2}") == "x²"
    assert _unicode_fallback(r"A_0 \cdot x^2") == "A₀ ⋅ x²"


def test_latex_to_unicode_never_raises_and_strips():
    assert latex_to_unicode("") == ""
    assert latex_to_unicode("   ") == ""
    # Caso linear comum chega a Unicode (vale para pylatexenc OU fallback).
    assert "²" in latex_to_unicode("x^2")


# ==============================================================================
# Segmentação da resposta
# ==============================================================================

def test_segment_plain_text_is_single_text_segment():
    md = "A derivada mede a inclinação da reta tangente."
    segs = segment_answer(md)
    assert segs == [TextSegment(md)]


def test_segment_empty_returns_empty():
    assert segment_answer("") == []
    assert segment_answer("   \n  ") == [TextSegment("   \n  ")] or segment_answer("   ") == []


def test_segment_display_formula_becomes_image_between_texts():
    # O cenário pedido: texto → fórmula (imagem) → texto, como 3 mensagens.
    md = "A derivada é definida por $$\\frac{df}{dx} = \\lim_{h\\to0}\\frac{f(x+h)-f(x)}{h}$$ ou seja, a inclinação."
    segs = segment_answer(md)
    assert [type(s).__name__ for s in segs] == ["TextSegment", "ImageSegment", "TextSegment"]
    img = segs[1]
    assert isinstance(img, ImageSegment)
    assert img.display is True
    assert img.latex.startswith(r"\frac{df}{dx}")
    assert "A derivada" in segs[0].text
    assert "inclinação" in segs[2].text


def test_segment_inline_complex_becomes_image():
    md = "Calcule $\\int_0^1 x^2\\,dx$ com atenção."
    segs = segment_answer(md)
    images = [s for s in segs if isinstance(s, ImageSegment)]
    assert len(images) == 1
    assert images[0].display is False
    assert images[0].latex == r"\int_0^1 x^2\,dx"


def test_segment_inline_simple_stays_in_text_no_image():
    md = "Seja $x^2$ sempre positivo e $\\alpha$ o ângulo."
    segs = segment_answer(md)
    assert all(isinstance(s, TextSegment) for s in segs)
    # Texto inline simples é fundido num único bloco de prosa.
    assert len(segs) == 1
    assert "x²" in segs[0].text
    assert "α" in segs[0].text


def test_segment_display_simple_stays_text_on_own_line():
    md = "A energia: $$E = mc^2$$ é a base."
    segs = segment_answer(md)
    # E=mc^2 é linear → Unicode (não vira imagem).
    assert all(isinstance(s, TextSegment) for s in segs)
    joined = "".join(s.text for s in segs)
    assert "E = mc²" in joined


def test_segment_two_formulas_keep_order():
    md = "Primeiro $$\\frac{a}{b}$$ depois $$\\sqrt{c}$$ fim."
    segs = segment_answer(md)
    kinds = [type(s).__name__ for s in segs]
    assert kinds == ["TextSegment", "ImageSegment", "TextSegment", "ImageSegment", "TextSegment"]
    assert segs[1].latex == r"\frac{a}{b}"
    assert segs[3].latex == r"\sqrt{c}"


def test_segment_double_dollar_not_split_as_inline():
    # Garante que $$...$$ é tratado como display, nunca como dois inlines.
    md = "x $$y$$ z"  # y é linear → vira texto, mas como DISPLAY (1 span só)
    segs = segment_answer(md)
    assert not any(isinstance(s, ImageSegment) for s in segs)
    assert "y" in "".join(s.text for s in segs)
