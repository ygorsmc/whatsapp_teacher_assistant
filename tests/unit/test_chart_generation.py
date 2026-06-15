"""Testes do M7 — geração de gráficos para explicações.

Camadas:
  - parse_chart_spec  → segurança PURA (whitelist) + validação do schema;
  - segment_answer    → reconhece o bloco ```grafico``` (e mantém o M6 byte-a-byte);
  - deliver_answer    → despacha o gráfico ou DESCARTA (texto segue);
  - prompt_builder    → injeta a diretiva só com flag + exatas;
  - compute_series / render → núcleo determinístico (sympy/matplotlib), guardados
    por importorskip (rodam onde as libs existirem; pulam no venv enxuto).

Rodar: pytest tests/unit/test_chart_generation.py -v
"""

import json
from types import SimpleNamespace

import pytest

from src.math.charts import (
    ChartSpec,
    get_chart_renderer,
    parse_chart_spec,
    reset_chart_renderer_cache,
)
from src.math.delivery import deliver_answer
from src.math.segmenter import ChartSegment, TextSegment, segment_answer
from src.shared.config import settings


def _spec_json(**over) -> str:
    base = {
        "tipo": "funcao",
        "titulo": "Parábola",
        "eixo_x": "x",
        "eixo_y": "f(x)",
        "dominio": [-2, 5],
        "funcoes": [{"expr": "x**2 - 3*x + 2", "rotulo": "f(x)"}],
    }
    base.update(over)
    return json.dumps(base)


# ==============================================================================
# parse_chart_spec — schema válido
# ==============================================================================

def test_valid_spec_parses():
    spec = parse_chart_spec(_spec_json())
    assert isinstance(spec, ChartSpec)
    assert spec.domain == (-2.0, 5.0)
    assert spec.title == "Parábola"
    assert len(spec.series) == 1
    assert spec.series[0].expr == "x**2 - 3*x + 2"


def test_caret_is_normalized_to_double_star():
    spec = parse_chart_spec(_spec_json(funcoes=[{"expr": "x^2 + 1"}]))
    assert spec is not None
    assert spec.series[0].expr == "x**2 + 1"  # ^ → **


def test_multiple_functions_capped():
    funcs = [{"expr": f"x + {i}"} for i in range(10)]
    spec = parse_chart_spec(_spec_json(funcoes=funcs))
    assert spec is not None
    assert len(spec.series) == 4  # _MAX_SERIES


def test_points_clamped():
    assert parse_chart_spec(_spec_json(pontos=999999)).points == settings.CHART_MAX_POINTS
    assert parse_chart_spec(_spec_json(pontos=2)).points == 10  # piso


def test_string_function_shorthand():
    spec = parse_chart_spec(_spec_json(funcoes=["sin(x)"]))
    assert spec is not None
    assert spec.series[0].expr == "sin(x)"


# ==============================================================================
# parse_chart_spec — rejeições estruturais
# ==============================================================================

@pytest.mark.parametrize("raw", [
    "",
    "   ",
    "{nao eh json}",
    "[1, 2, 3]",                                   # não-dict
    json.dumps({"tipo": "funcao", "funcoes": [{"expr": "x"}]}),       # sem domínio
    json.dumps({"tipo": "funcao", "dominio": [5, 5], "funcoes": [{"expr": "x"}]}),  # a>=b
    json.dumps({"tipo": "funcao", "dominio": ["a", "b"], "funcoes": [{"expr": "x"}]}),  # não-num
    json.dumps({"tipo": "funcao", "dominio": [-1, 1], "funcoes": []}),  # sem séries
    json.dumps({"tipo": "barra", "dominio": [-1, 1], "funcoes": [{"expr": "x"}]}),  # tipo n/suportado
])
def test_rejects_malformed(raw):
    assert parse_chart_spec(raw) is None


# ==============================================================================
# parse_chart_spec — SEGURANÇA (sem RCE): expressões proibidas reprovam o gráfico
# ==============================================================================

@pytest.mark.parametrize("expr", [
    "__import__('os').system('rm -rf /')",
    "os.system('x')",
    "eval('2+2')",
    "exec('x=1')",
    "x.__class__.__bases__",
    "open('/etc/passwd')",
    "lambda x: x",
    "import os",
    "[1, 2, 3]",
    "{1: 2}",
    "foo(x)",          # função fora da whitelist
    "2*y + 1",         # variável que não é x
    "x; print(1)",
    "x if True else 0",
])
def test_rejects_unsafe_expression(expr):
    raw = _spec_json(funcoes=[{"expr": expr}])
    assert parse_chart_spec(raw) is None, f"deveria reprovar: {expr!r}"


def test_allowed_functions_pass():
    for expr in ["sin(x)", "cos(x)*x", "exp(-x**2)", "sqrt(abs(x))", "log(x) + pi", "tan(x)/2"]:
        assert parse_chart_spec(_spec_json(funcoes=[{"expr": expr}])) is not None, expr


# ==============================================================================
# segment_answer — reconhece o bloco e mantém o M6 intacto
# ==============================================================================

def test_segment_extracts_chart_block():
    md = f"Veja o gráfico:\n```grafico\n{_spec_json()}\n```\nEntendeu?"
    segs = segment_answer(md)
    kinds = [type(s).__name__ for s in segs]
    assert "ChartSegment" in kinds
    chart = next(s for s in segs if isinstance(s, ChartSegment))
    assert "funcoes" in chart.raw                       # JSON cru preservado
    assert isinstance(segs[0], TextSegment) and "Veja" in segs[0].text
    assert isinstance(segs[-1], TextSegment) and "Entendeu" in segs[-1].text


def test_chart_alias_chart_keyword():
    md = f"```chart\n{_spec_json()}\n```"
    segs = segment_answer(md)
    assert any(isinstance(s, ChartSegment) for s in segs)


def test_no_chart_block_is_unchanged_m6():
    # Sem bloco de gráfico: nada de ChartSegment; a fórmula vira ImageSegment como no M6.
    segs = segment_answer("Seja $$\\frac{a}{b}$$ aqui.")
    assert not any(isinstance(s, ChartSegment) for s in segs)
    assert [type(s).__name__ for s in segs] == ["TextSegment", "ImageSegment", "TextSegment"]


def test_python_fence_is_not_a_chart():
    md = "```python\nprint('oi')\n```"
    segs = segment_answer(md)
    assert not any(isinstance(s, ChartSegment) for s in segs)  # só grafico/chart contam


# ==============================================================================
# deliver_answer — despacho do gráfico (canal + renderer FAKE, duck-typed)
# ==============================================================================

class _FakeChannel:
    def __init__(self):
        self.calls = []

    async def send_text(self, user_id, text, reply_to=None):
        self.calls.append(("text", text, reply_to))
        return {}

    async def send_image(self, user_id, image_bytes, caption=""):
        self.calls.append(("image", image_bytes, caption))
        return {}


class _FakeChartRenderer:
    def __init__(self, out=b"PNGCHART"):
        self.out = out

    async def render(self, raw):
        return self.out


class _NoneChartRenderer:
    async def render(self, raw):
        return None


class _BoomChartRenderer:
    async def render(self, raw):
        raise RuntimeError("matplotlib down")


@pytest.mark.asyncio
async def test_chart_interleaves_text_image_text():
    ch = _FakeChannel()
    md = f"Antes.\n```grafico\n{_spec_json()}\n```\nDepois."
    sent = await deliver_answer(
        ch, "u1", md, reply_to="m1",
        renderer=_NoneChartRenderer(), chart_renderer=_FakeChartRenderer(b"PNG"),
    )
    kinds = [c[0] for c in ch.calls]
    assert kinds == ["text", "image", "text"]
    assert ch.calls[0] == ("text", "Antes.", "m1")
    assert ch.calls[1] == ("image", b"PNG", "Parábola")   # legenda = título do spec
    assert ch.calls[2][1] == "Depois."
    assert sent == 3


@pytest.mark.asyncio
async def test_chart_dropped_when_renderer_none():
    ch = _FakeChannel()
    md = f"Antes.\n```grafico\n{_spec_json()}\n```\nDepois."
    sent = await deliver_answer(ch, "u1", md, renderer=_NoneChartRenderer(), chart_renderer=_NoneChartRenderer())
    assert not any(c[0] == "image" for c in ch.calls)     # gráfico descartado
    assert sent == 1                                      # texto unido num envio
    assert "Antes." in ch.calls[0][1] and "Depois." in ch.calls[0][1]


@pytest.mark.asyncio
async def test_chart_dropped_when_renderer_raises():
    ch = _FakeChannel()
    md = f"Antes.\n```grafico\n{_spec_json()}\n```\nDepois."
    await deliver_answer(ch, "u1", md, renderer=_NoneChartRenderer(), chart_renderer=_BoomChartRenderer())
    assert not any(c[0] == "image" for c in ch.calls)     # exceção → descarta, não derruba


# ==============================================================================
# Factory do renderer de gráfico
# ==============================================================================

def test_get_chart_renderer_none_and_unknown():
    reset_chart_renderer_cache()
    assert get_chart_renderer("none") is None
    assert get_chart_renderer("") is None
    assert get_chart_renderer("inexistente_xyz") is None


# ==============================================================================
# Diretiva no System Prompt (M7) — só com flag + exatas; invariante flag-off
# ==============================================================================

_CHART_MARKER = "GRÁFICO DE APOIO"


def _fake_actors():
    teacher = SimpleNamespace(
        name="Ana", subject="Física", assistant_persona="amigável",
        out_of_scope_message="Fora do escopo.",
    )
    student = SimpleNamespace(name="João Silva")
    return teacher, student


async def _build(course_name, subject_name=None):
    from src.rag.prompt_builder import build_system_prompt
    teacher, student = _fake_actors()
    return await build_system_prompt(teacher, student, "crs_x", course_name, "les_01", subject_name)


@pytest.mark.asyncio
async def test_chart_directive_off_by_default(mocker):
    mocker.patch.object(settings, "CHART_GENERATION_ENABLED", False)
    prompt = await _build("Cálculo I", subject_name="Cálculo")
    assert _CHART_MARKER not in prompt          # exatas, mas flag off ⇒ intocado


@pytest.mark.asyncio
async def test_chart_directive_on_appends_for_exatas(mocker):
    mocker.patch.object(settings, "CHART_GENERATION_ENABLED", True)
    prompt = await _build("Turma 2026", subject_name="Física")
    assert _CHART_MARKER in prompt
    assert "```grafico" in prompt               # mostra o formato exato do bloco


@pytest.mark.asyncio
async def test_chart_directive_not_for_non_exatas(mocker):
    mocker.patch.object(settings, "CHART_GENERATION_ENABLED", True)
    assert _CHART_MARKER not in await _build("História da Arte", subject_name="História")


@pytest.mark.asyncio
async def test_chart_directive_flag_off_is_prefix_of_on(mocker):
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", False)
    mocker.patch.object(settings, "MATH_MODE_ENABLED", False)
    mocker.patch.object(settings, "CHART_GENERATION_ENABLED", False)
    off = await _build("Cálculo I", subject_name="Cálculo")
    mocker.patch.object(settings, "CHART_GENERATION_ENABLED", True)
    on = await _build("Cálculo I", subject_name="Cálculo")
    assert on.startswith(off) and on != off     # OFF = prefixo exato


# ==============================================================================
# Núcleo determinístico (sympy/matplotlib) — guardado por importorskip
# ==============================================================================

def test_compute_series_values():
    pytest.importorskip("sympy")
    import numpy as np
    from src.math.charts import compute_series

    xs = np.array([0.0, 1.0, 2.0, 3.0])
    ys = compute_series("x**2", xs)
    assert ys is not None
    np.testing.assert_allclose(ys, [0.0, 1.0, 4.0, 9.0])


def test_compute_series_constant_broadcasts():
    pytest.importorskip("sympy")
    import numpy as np
    from src.math.charts import compute_series

    xs = np.linspace(-1, 1, 5)
    ys = compute_series("3", xs)
    assert ys is not None and ys.shape == xs.shape
    np.testing.assert_allclose(ys, np.full(5, 3.0))


def test_compute_series_rejects_foreign_variable():
    pytest.importorskip("sympy")
    import numpy as np
    from src.math.charts import compute_series

    # Defesa em profundidade: 'y' passaria do gate? Não — mas se chegasse aqui,
    # o guard de free_symbols (só x) devolve None.
    assert compute_series("y", np.linspace(0, 1, 3)) is None


def test_compute_series_handles_discontinuity():
    pytest.importorskip("sympy")
    import numpy as np
    from src.math.charts import compute_series

    xs = np.array([-1.0, 0.0, 1.0])
    ys = compute_series("1/x", xs)
    assert ys is not None
    assert np.isnan(ys[1])             # 1/0 → buraco, sem crash
    np.testing.assert_allclose(ys[[0, 2]], [-1.0, 1.0])


@pytest.mark.asyncio
async def test_matplotlib_chart_renderer_produces_png():
    pytest.importorskip("sympy")
    pytest.importorskip("matplotlib")
    reset_chart_renderer_cache()
    renderer = get_chart_renderer("matplotlib")
    assert renderer is not None
    png = await renderer.render(_spec_json())
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"   # assinatura PNG


@pytest.mark.asyncio
async def test_matplotlib_renderer_returns_none_on_invalid_spec():
    pytest.importorskip("matplotlib")
    reset_chart_renderer_cache()
    renderer = get_chart_renderer("matplotlib")
    assert renderer is not None
    assert await renderer.render("{lixo}") is None        # spec inválido → None


# ==============================================================================
# Config — flags do M7 existem com defaults seguros
# ==============================================================================

def test_chart_flags_default_off():
    assert settings.CHART_GENERATION_ENABLED is False
    assert settings.CHART_RENDERER == "none"
    assert settings.CHART_MAX_POINTS == 1000
