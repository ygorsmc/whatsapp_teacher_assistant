"""
M6 — Entrega intercalada da resposta no WhatsApp (texto → fórmula(imagem) → texto).

Uma resposta lógica do LLM pode virar VÁRIAS mensagens: a prosa segue como texto
(com math inline já em Unicode pelo segmentador), e cada fórmula complexa vira uma
imagem renderizada, despachada ENTRE os blocos de texto.

Degradação sempre (regra da casa): sem renderer, ou se o render falhar, a fórmula
cai para Unicode dentro do próprio texto — nenhuma fórmula é perdida, nenhuma
resposta é derrubada. Usado só quando `MATH_RENDERING_ENABLED` (flag OFF = envio
de texto único, como hoje).
"""

import structlog

from src.math.segmenter import (
    ChartSegment,
    ImageSegment,
    TextSegment,
    latex_to_unicode,
    segment_answer,
)

logger = structlog.get_logger(__name__)


async def deliver_answer(
    channel,
    user_id: str,
    markdown: str,
    reply_to: str | None = None,
    renderer=None,
    chart_renderer=None,
) -> int:
    """Entrega `markdown` intercalando texto, imagens de fórmula e gráficos.

    Retorna o nº de mensagens enviadas. `renderer=None` → resolve pela factory
    (`get_math_renderer`); se ele devolver None (motor 'none'/indisponível), as
    fórmulas degradam para Unicode no texto. `chart_renderer=None` → resolve por
    `get_chart_renderer` SÓ se houver gráfico na resposta; se ele devolver None,
    o gráfico (ilustrativo) é DESCARTADO e o texto explicativo segue intacto.
    """
    if renderer is None:
        from src.math.renderer import get_math_renderer
        renderer = get_math_renderer()

    segments = segment_answer(markdown)

    # Renderer de gráfico resolvido só quando a resposta de fato traz um gráfico
    # (evita construir matplotlib em respostas sem plot).
    if chart_renderer is None and any(isinstance(s, ChartSegment) for s in segments):
        from src.math.charts import get_chart_renderer
        chart_renderer = get_chart_renderer()

    sent = 0
    pending = ""

    async def flush_text() -> None:
        nonlocal pending, sent
        if pending.strip():
            # reply_to só na PRIMEIRA mensagem (encadeia a resposta à pergunta).
            await channel.send_text(user_id, pending.strip(), reply_to=(reply_to if sent == 0 else None))
            sent += 1
        pending = ""

    for seg in segments:
        if isinstance(seg, TextSegment):
            pending += seg.text
            continue

        if isinstance(seg, ChartSegment):
            # M7: gráfico ilustrativo. Render falha/sem motor → descarta (o texto
            # explica sozinho); nunca degrada para LaTeX nem derruba a resposta.
            chart_img = None
            if chart_renderer is not None:
                try:
                    chart_img = await chart_renderer.render(seg.raw)
                except Exception as e:
                    logger.warning("chart_render_error", error=str(e)[:200])
                    chart_img = None
            if chart_img:
                await flush_text()  # o texto que vem ANTES do gráfico
                caption = _chart_caption(seg.raw)
                await channel.send_image(user_id, chart_img, caption=caption)
                sent += 1
            continue

        # ImageSegment: tenta renderizar; falha/sem renderer → Unicode no texto.
        assert isinstance(seg, ImageSegment)
        image = None
        if renderer is not None:
            try:
                image = await renderer.render(seg.latex, display=seg.display)
            except Exception as e:
                logger.warning("math_render_error", error=str(e)[:200])
                image = None

        if image:
            await flush_text()  # o texto que vem ANTES da fórmula
            await channel.send_image(user_id, image)
            sent += 1
        else:
            pending += " " + latex_to_unicode(seg.latex) + " "

    await flush_text()

    # Resposta só de espaços (não deveria acontecer): garante ao menos 1 envio.
    if sent == 0 and markdown.strip():
        await channel.send_text(user_id, markdown.strip(), reply_to=reply_to)
        sent = 1

    if sent > 1:
        logger.info("math_answer_delivered_multipart", messages=sent)
    return sent


def _chart_caption(raw: str) -> str:
    """Título do gráfico (se válido) para legenda da imagem; "" em qualquer falha.
    Parse PURO e barato (sem sympy/matplotlib) — nunca levanta."""
    try:
        from src.math.charts import parse_chart_spec
        spec = parse_chart_spec(raw)
        return (spec.title if spec else "") or ""
    except Exception:
        return ""
