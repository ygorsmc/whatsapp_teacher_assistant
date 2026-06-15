"""
M6 — Renderização de fórmula LaTeX → imagem (PNG), agnóstica de provider.

Fórmulas complexas (frações, integrais com limites, matrizes, sistemas) colapsam
em Unicode (cf. "Pipeline RAG para SaaS Educacional" §5.3) — vão como IMAGEM no
WhatsApp. Aqui, o motor de render é selecionável por env (`MATH_RENDERER`), mesmo
padrão registry de `speech/factory.py`:

  - 'matplotlib' → in-process (mathtext puro, sem instalar TeX). Import-guarded:
    sem o pacote, indisponível → None.
  - 'endpoint'   → serviço HTTP self-hosted (contrato documentado abaixo).
  - 'none'       → default. Sem render → a entrega degrada a fórmula para Unicode.

CONTRATO `IMathRenderer.render(latex, display) -> bytes | None`:
  - bytes = PNG da fórmula; None = não consegui renderizar (o chamador degrada
    para Unicode — nenhuma fórmula é perdida). NUNCA levanta.

Contrato do endpoint:
    POST {MATH_RENDER_ENDPOINT}
    Authorization: Bearer {MATH_RENDER_API_KEY}   (se definido)
    {"latex": "\\frac{a}{b}", "display": true}
    → corpo binário PNG (image/png)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable, Optional

import structlog

from src.shared.config import settings

logger = structlog.get_logger(__name__)


class IMathRenderer(ABC):
    name: str = "abstract"

    @abstractmethod
    async def render(self, latex: str, display: bool = True) -> bytes | None:
        """Renderiza `latex` em PNG. None = indisponível (chamador degrada)."""
        ...


class MatplotlibMathRenderer(IMathRenderer):
    """Render in-process via matplotlib mathtext (sem TeX instalado).

    Cobre o grosso (frações, integrais, somatórios, raízes, gregas, índices);
    ambientes não suportados (ex.: `\\begin{cases}`) levantam → None → Unicode.
    """

    name = "matplotlib"

    def __init__(self):
        import matplotlib  # import tardio (opcional, pesado)
        matplotlib.use("Agg")  # backend sem display, seguro no worker

    def _render_sync(self, latex: str, display: bool) -> bytes:
        import io
        import matplotlib.pyplot as plt

        body = latex.strip().strip("$")
        fig = plt.figure(figsize=(0.01, 0.01))
        fig.text(0, 0, f"${body}$", fontsize=22 if display else 16)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=200, bbox_inches="tight", pad_inches=0.25)
        plt.close(fig)
        return buf.getvalue()

    async def render(self, latex: str, display: bool = True) -> bytes | None:
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, self._render_sync, latex, display)
        except Exception as e:
            logger.warning("math_render_failed", renderer=self.name, error=str(e)[:200])
            return None


class EndpointMathRenderer(IMathRenderer):
    """Render via serviço HTTP self-hosted (TeX Live / MathJax → PNG)."""

    name = "endpoint"

    def __init__(self):
        if not settings.MATH_RENDER_ENDPOINT:
            raise ValueError("MATH_RENDER_ENDPOINT is not set in environment.")
        self.endpoint = settings.MATH_RENDER_ENDPOINT
        self.api_key = settings.MATH_RENDER_API_KEY
        self.timeout = settings.MATH_RENDER_TIMEOUT_SECONDS

    async def render(self, latex: str, display: bool = True) -> bytes | None:
        import httpx
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(
                    self.endpoint,
                    headers=headers,
                    json={"latex": latex, "display": display},
                )
                resp.raise_for_status()
                content = resp.content
            return content or None
        except Exception as e:
            logger.warning("math_render_failed", renderer=self.name, error=str(e)[:200])
            return None


_RENDERERS: dict[str, Callable[[], IMathRenderer]] = {
    "matplotlib": MatplotlibMathRenderer,
    "endpoint": EndpointMathRenderer,
}

_cache: dict[str, IMathRenderer] = {}


def get_math_renderer(name: str | None = None) -> Optional[IMathRenderer]:
    """Factory do renderer. 'none'/desconhecido/indisponível → None (a entrega
    degrada a fórmula para Unicode; nenhuma camada derruba a resposta)."""
    resolved = (name or settings.MATH_RENDERER or "none").lower()
    if resolved in ("none", ""):
        return None
    if resolved in _cache:
        return _cache[resolved]
    builder = _RENDERERS.get(resolved)
    if builder is None:
        logger.warning("unknown_math_renderer", renderer=resolved)
        return None
    try:
        instance = builder()
    except Exception as e:  # pacote ausente / endpoint não configurado
        logger.warning("math_renderer_unavailable", renderer=resolved, error=str(e)[:200])
        return None
    _cache[resolved] = instance
    return instance


def reset_renderer_cache() -> None:
    """Limpa o cache (testes / troca de motor)."""
    _cache.clear()
