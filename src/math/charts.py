"""
M7 — Gráficos para EXPLICAÇÕES (plot determinístico de função), agnóstico de provider.

Quando uma função/relação ajuda a visualizar a explicação (cf. "Pipeline RAG para
SaaS Educacional" §5.4), o LLM PODE emitir um bloco ```grafico``` com um spec de
schema CONTROLADO. O gráfico é supplementar ao texto: nunca nasce de um pedido do
aluno, só quando casa com uma explicação — e o texto já explica sozinho.

REGRA DE OURO (sem RCE): o LLM entrega apenas a EXPRESSÃO matemática e o domínio,
NUNCA código. A defesa é em duas camadas:
  1. `parse_chart_spec` — gate PURO (regex/whitelist): a expressão só pode conter
     dígitos, ``x``, operadores ``+ - * / ** ( ) ,``, constantes ``pi``/``e`` e
     funções de uma whitelist (sin, cos, exp, log, sqrt, abs...). Qualquer outra
     coisa (``__import__``, ``os.``, ``[`` , ``lambda``, atributo) → rejeitado.
  2. `compute_series` — parse com `sympy` restrito (global_dict só com as funções
     liberadas) e `lambdify(modules=['numpy'])`. Os NÚMEROS plotados vêm de
     `numpy`, determinísticos — o LLM não inventa pontos.

CONTRATO `IChartRenderer.render(raw_spec) -> bytes | None`:
  - bytes = PNG do gráfico; None = não consegui (spec inválido, dep ausente, erro).
    NUNCA levanta. O chamador (delivery) DESCARTA o gráfico e mantém o texto —
    nenhuma resposta é derrubada por causa de um gráfico ilustrativo.

`numpy` já é dependência; `sympy`/`matplotlib`/`seaborn` são import-guarded (mesmo
idioma do M6): sem eles, o motor fica indisponível e o gráfico degrada para nada.
"""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional

import structlog

from src.shared.config import settings

logger = structlog.get_logger(__name__)

__all__ = [
    "ChartSeries",
    "ChartSpec",
    "parse_chart_spec",
    "compute_series",
    "IChartRenderer",
    "MatplotlibChartRenderer",
    "get_chart_renderer",
    "reset_chart_renderer_cache",
]


# ── Whitelist de funções/constantes permitidas na expressão ──────────────────
# O gate aceita SÓ estes nomes (além de 'x'); qualquer outro identificador
# (import, eval, os, __class__, lambda...) reprova a expressão inteira.
_ALLOWED_FUNCS = frozenset({
    "sin", "cos", "tan", "asin", "acos", "atan", "atan2",
    "sinh", "cosh", "tanh", "exp", "log", "ln", "log10",
    "sqrt", "abs", "sign", "floor", "ceil", "factorial",
    "min", "max", "pow",
})
_ALLOWED_CONSTS = frozenset({"pi", "e", "x"})
_ALLOWED_NAMES = _ALLOWED_FUNCS | _ALLOWED_CONSTS

# Tudo que NÃO é identificador deve ser um destes símbolos (após normalizar ^→**).
_ALLOWED_SYMBOLS_RE = re.compile(r"^[0-9\.\+\-\*/\(\),\s]*$")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z_0-9]*")

# Limites do spec (anti-DoS / FinOps; o teto de pontos vem do settings).
_MAX_SERIES = 4
_MAX_EXPR_LEN = 200
_MAX_DOMAIN_SPAN = 1e6


@dataclass
class ChartSeries:
    """Uma curva: expressão f(x) já validada (sem delimitadores) + rótulo."""
    expr: str
    label: str = ""


@dataclass
class ChartSpec:
    """Spec validado de um gráfico de função. Imutável após `parse_chart_spec`."""
    series: list[ChartSeries]
    domain: tuple[float, float]
    title: str = ""
    xlabel: str = "x"
    ylabel: str = "y"
    points: int = 400
    kind: str = "funcao"
    extras: dict = field(default_factory=dict)


def _normalize_expr(raw: str) -> str:
    """Normaliza a expressão para a sintaxe do parser: ``^`` → ``**``."""
    return (raw or "").replace("^", "**").strip()


def _expression_is_safe(expr: str) -> bool:
    """Gate PURO: a expressão só usa x, números, operadores e funções da whitelist.

    Não avalia nada — só inspeciona tokens. Bloqueia identificadores fora da
    whitelist (``__import__``, ``os``, ``eval``, ``lambda``, atributos como
    ``x.__class__``) e símbolos perigosos (``[`` ``]`` ``{`` ``}`` ``:`` ``=``
    ``;`` ``%`` ``@`` etc.), que nunca aparecem em ``_ALLOWED_SYMBOLS_RE``.
    """
    if not expr or len(expr) > _MAX_EXPR_LEN:
        return False
    # 1) Todo identificador tem de estar na whitelist.
    for name in _IDENT_RE.findall(expr):
        if name not in _ALLOWED_NAMES:
            return False
    # 2) Removidos os identificadores, só podem sobrar números/operadores/parens.
    residue = _IDENT_RE.sub("", expr)
    if not _ALLOWED_SYMBOLS_RE.match(residue):
        return False
    # 3) Defesa extra: nada de '**' encadeado abusivo nem atributo ('.' colado a nome).
    if re.search(r"\*\*\s*\*\*", expr) or re.search(r"[A-Za-z_0-9]\s*\.\s*[A-Za-z_]", expr):
        return False
    return True


def _coerce_domain(value) -> Optional[tuple[float, float]]:
    """Valida o domínio: par [a, b] numérico, finito, a<b e span limitado."""
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        a, b = float(value[0]), float(value[1])
    except (TypeError, ValueError):
        return None
    import math
    if not (math.isfinite(a) and math.isfinite(b)):
        return None
    if a >= b or (b - a) > _MAX_DOMAIN_SPAN:
        return None
    return (a, b)


def parse_chart_spec(raw: str) -> Optional[ChartSpec]:
    """Faz o parse + a VALIDAÇÃO de segurança do bloco ```grafico```.

    `raw` é o JSON interno do bloco. Devolve `ChartSpec` se tudo for seguro e
    bem-formado; senão `None` (o gráfico é descartado — feature ilustrativa).
    Esta é a camada de segurança PURA (sem sympy/matplotlib): testável sem deps.
    """
    if not raw or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None

    kind = str(data.get("tipo") or data.get("kind") or "funcao").lower()
    if kind not in ("funcao", "function"):
        return None  # por ora só plot de função (caso de uso pedido)

    domain = _coerce_domain(data.get("dominio") or data.get("domain"))
    if domain is None:
        return None

    raw_series = data.get("funcoes") or data.get("series") or []
    if not isinstance(raw_series, list) or not raw_series:
        return None
    if len(raw_series) > _MAX_SERIES:
        raw_series = raw_series[:_MAX_SERIES]

    series: list[ChartSeries] = []
    for item in raw_series:
        if isinstance(item, str):
            expr_raw, label = item, ""
        elif isinstance(item, dict):
            expr_raw = item.get("expr") or item.get("y") or ""
            label = str(item.get("rotulo") or item.get("label") or "")
        else:
            continue
        expr = _normalize_expr(str(expr_raw))
        if not _expression_is_safe(expr):
            return None  # uma expressão suspeita reprova o gráfico inteiro
        series.append(ChartSeries(expr=expr, label=label[:80]))

    if not series:
        return None

    points = data.get("pontos") or data.get("points") or 400
    try:
        points = int(points)
    except (TypeError, ValueError):
        points = 400
    points = max(10, min(points, settings.CHART_MAX_POINTS))

    return ChartSpec(
        series=series,
        domain=domain,
        title=str(data.get("titulo") or data.get("title") or "")[:140],
        xlabel=str(data.get("eixo_x") or data.get("xlabel") or "x")[:60],
        ylabel=str(data.get("eixo_y") or data.get("ylabel") or "y")[:60],
        points=points,
        kind="funcao",
    )


def compute_series(expr: str, xs):
    """Avalia `expr` sobre o array `xs` via sympy restrito → numpy. None em falha.

    Pressupõe que `expr` JÁ passou por `_expression_is_safe` (defesa em camadas).
    `sympy` é import-guarded: sem o pacote, devolve None (motor indisponível).
    """
    try:
        import numpy as np
        from sympy import lambdify, symbols
        from sympy.parsing.sympy_parser import (
            implicit_multiplication_application,
            parse_expr,
            standard_transformations,
        )
        from sympy.functions import (  # noqa: F401 — povoam o global_dict restrito
            Abs, acos, asin, atan, ceiling, cos, cosh, exp, factorial,
            floor, log, sign, sin, sinh, sqrt, tan, tanh,
        )
        from sympy import E, pi

        x = symbols("x")
        # global_dict RESTRITO: o parser do sympy só enxerga estes nomes.
        safe_globals = {
            "x": x, "pi": pi, "e": E,
            "sin": sin, "cos": cos, "tan": tan,
            "asin": asin, "acos": acos, "atan": atan,
            "sinh": sinh, "cosh": cosh, "tanh": tanh,
            "exp": exp, "log": log, "ln": log, "sqrt": sqrt,
            "abs": Abs, "Abs": Abs, "sign": sign,
            "floor": floor, "ceil": ceiling, "factorial": factorial,
        }
        transformations = standard_transformations + (implicit_multiplication_application,)
        # Usamos local_dict p/ sobrescrever os nomes liberados com as versões do sympy;
        # NÃO passamos global_dict={} porque o parser precisa dos tipos internos do
        # sympy (Integer, Float, Symbol, ...). A defesa é feita ANTES pelo gate puro
        # _expression_is_safe (whitelist de identificadores sem eval), completada aqui
        # pelo check de free_symbols (só x permitido).
        sym_expr = parse_expr(
            expr,
            local_dict=safe_globals,
            transformations=transformations,
            evaluate=True,
        )
        # Só pode depender de x (rejeita variáveis livres surpresa).
        free = {str(s) for s in sym_expr.free_symbols}
        if not free.issubset({"x"}):
            return None
        f = lambdify(x, sym_expr, modules=["numpy"])
        with np.errstate(all="ignore"):
            ys = f(xs)
            ys = np.asarray(ys, dtype="float64") * np.ones_like(xs)  # difunde constantes
            ys[~np.isfinite(ys)] = np.nan  # descontinuidades viram buracos no plot
        return ys
    except Exception as e:
        logger.warning("chart_compute_failed", error=str(e)[:200])
        return None


# ── Renderer agnóstico (mesmo padrão de IMathRenderer) ───────────────────────


class IChartRenderer(ABC):
    name: str = "abstract"

    @abstractmethod
    async def render(self, raw_spec: str) -> bytes | None:
        """Parseia+valida+plota o bloco. None = não consegui (chamador descarta)."""
        ...


class MatplotlibChartRenderer(IChartRenderer):
    """Plot in-process via matplotlib (+ seaborn se disponível), backend Agg."""

    name = "matplotlib"

    def __init__(self):
        import matplotlib  # import tardio (opcional, pesado)
        matplotlib.use("Agg")  # backend headless, seguro no worker

    def _render_sync(self, spec: ChartSpec) -> bytes | None:
        import io
        import numpy as np
        import matplotlib.pyplot as plt

        try:
            import seaborn as sns
            sns.set_theme(style="whitegrid")
        except Exception:
            pass  # seaborn é só estética; matplotlib puro serve

        a, b = spec.domain
        xs = np.linspace(a, b, spec.points)
        fig, ax = plt.subplots(figsize=(6, 4))
        plotted = 0
        for s in spec.series:
            ys = compute_series(s.expr, xs)
            if ys is None:
                continue
            ax.plot(xs, ys, label=s.label or s.expr)
            plotted += 1
        if plotted == 0:
            plt.close(fig)
            return None
        if spec.title:
            ax.set_title(spec.title)
        ax.set_xlabel(spec.xlabel)
        ax.set_ylabel(spec.ylabel)
        ax.axhline(0, color="#888", linewidth=0.8)
        ax.axvline(0, color="#888", linewidth=0.8)
        if plotted > 1 or any(s.label for s in spec.series):
            ax.legend(loc="best", fontsize=9)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=160, bbox_inches="tight")
        plt.close(fig)
        return buf.getvalue()

    async def render(self, raw_spec: str) -> bytes | None:
        import asyncio
        spec = parse_chart_spec(raw_spec)
        if spec is None:
            return None
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._render_sync, spec)
        except Exception as e:
            logger.warning("chart_render_failed", renderer=self.name, error=str(e)[:200])
            return None


_RENDERERS: dict[str, Callable[[], IChartRenderer]] = {
    "matplotlib": MatplotlibChartRenderer,
}

_cache: dict[str, IChartRenderer] = {}


def get_chart_renderer(name: str | None = None) -> Optional[IChartRenderer]:
    """Factory do renderer de gráfico. 'none'/desconhecido/indisponível → None
    (o gráfico é descartado; o texto explicativo segue intacto)."""
    resolved = (name or settings.CHART_RENDERER or "none").lower()
    if resolved in ("none", ""):
        return None
    if resolved in _cache:
        return _cache[resolved]
    builder = _RENDERERS.get(resolved)
    if builder is None:
        logger.warning("unknown_chart_renderer", renderer=resolved)
        return None
    try:
        instance = builder()
    except Exception as e:  # matplotlib ausente etc.
        logger.warning("chart_renderer_unavailable", renderer=resolved, error=str(e)[:200])
        return None
    _cache[resolved] = instance
    return instance


def reset_chart_renderer_cache() -> None:
    """Limpa o cache (testes / troca de motor)."""
    _cache.clear()
