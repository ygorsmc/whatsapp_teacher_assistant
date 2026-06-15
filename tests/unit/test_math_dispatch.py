"""Testes do dispatch intercalado (M6 lado-canal) e da factory de renderer.

PUROS: usam um canal e um renderer FAKE (duck-typed) — sem rede, sem matplotlib.
Verificam a sequência texto→imagem→texto e a degradação para Unicode. Rodar:
    pytest tests/unit/test_math_dispatch.py -v
"""

import pytest

from src.math.delivery import deliver_answer
from src.math.renderer import get_math_renderer, reset_renderer_cache


class _FakeChannel:
    """Canal duck-typed: registra (tipo, conteúdo, extra) de cada envio."""

    def __init__(self):
        self.calls: list[tuple] = []

    async def send_text(self, user_id, text, reply_to=None):
        self.calls.append(("text", text, reply_to))
        return {}

    async def send_image(self, user_id, image_bytes, caption=""):
        self.calls.append(("image", image_bytes, caption))
        return {}


class _FakeRenderer:
    def __init__(self, output: bytes = b"PNGDATA"):
        self.output = output

    async def render(self, latex, display=True):
        return self.output


class _NoneRenderer:
    async def render(self, latex, display=True):
        return None


class _BoomRenderer:
    async def render(self, latex, display=True):
        raise RuntimeError("render down")


# ==============================================================================
# deliver_answer — sequência de envios
# ==============================================================================

@pytest.mark.asyncio
async def test_plain_text_is_single_message():
    ch = _FakeChannel()
    sent = await deliver_answer(ch, "u1", "A derivada mede a inclinação.", reply_to="m1", renderer=_NoneRenderer())
    assert sent == 1
    assert ch.calls == [("text", "A derivada mede a inclinação.", "m1")]


@pytest.mark.asyncio
async def test_interleaves_text_image_text():
    ch = _FakeChannel()
    md = "Olhe $$\\frac{a}{b}$$ pronto."
    sent = await deliver_answer(ch, "u1", md, reply_to="m1", renderer=_FakeRenderer(b"PNG"))
    kinds = [c[0] for c in ch.calls]
    assert kinds == ["text", "image", "text"]
    assert ch.calls[0] == ("text", "Olhe", "m1")     # texto antes, com reply_to
    assert ch.calls[1] == ("image", b"PNG", "")       # a fórmula como imagem
    assert ch.calls[2][1] == "pronto."                # texto depois
    assert sent == 3


@pytest.mark.asyncio
async def test_reply_to_only_on_first_message():
    ch = _FakeChannel()
    md = "Antes $$\\frac{x}{y}$$ depois $$\\sqrt{z}$$ fim."
    await deliver_answer(ch, "u1", md, reply_to="m1", renderer=_FakeRenderer())
    text_calls = [c for c in ch.calls if c[0] == "text"]
    assert text_calls[0][2] == "m1"                   # 1ª mensagem encadeia
    assert all(c[2] is None for c in text_calls[1:])  # as demais, não


@pytest.mark.asyncio
async def test_degrades_to_unicode_when_renderer_returns_none():
    ch = _FakeChannel()
    md = "Seja $$\\frac{a}{b}$$ aqui."
    sent = await deliver_answer(ch, "u1", md, renderer=_NoneRenderer())
    assert all(c[0] == "text" for c in ch.calls)      # nenhuma imagem
    assert sent == 1                                  # tudo num texto só


@pytest.mark.asyncio
async def test_degrades_when_renderer_raises():
    ch = _FakeChannel()
    md = "Seja $$\\frac{a}{b}$$ aqui."
    await deliver_answer(ch, "u1", md, renderer=_BoomRenderer())
    assert not any(c[0] == "image" for c in ch.calls)  # exceção → fallback texto


@pytest.mark.asyncio
async def test_inline_simple_math_stays_in_text_no_image():
    ch = _FakeChannel()
    sent = await deliver_answer(ch, "u1", "Seja $x^2$ positivo.", renderer=_FakeRenderer())
    assert sent == 1
    assert ch.calls[0][0] == "text"
    assert "x²" in ch.calls[0][1]


# ==============================================================================
# Factory do renderer
# ==============================================================================

def test_get_math_renderer_none_and_unknown():
    reset_renderer_cache()
    assert get_math_renderer("none") is None
    assert get_math_renderer("") is None
    assert get_math_renderer("inexistente_xyz") is None
