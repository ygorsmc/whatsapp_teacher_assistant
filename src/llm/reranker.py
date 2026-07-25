"""
Reranking cross-encoder (F4) — o "juiz" do funil de recuperação.

A fusão (RRF) entrega ~50 candidatos; o cross-encoder (atenção cruzada
pergunta×documento) reordena e só os RERANK_TOP_N melhores entram no prompt.
O score devolvido aqui também é o AVALIADOR do CRAG-lite (F5) — determinístico
e já pago nesta chamada.

Providers (httpx + Tenacity, sem SDK novo):
  - `cohere`  → Rerank v4.0 (variantes Fast/Pro via COHERE_RERANK_MODEL)
  - `voyage`  → rerank-2
  - `open`    → endpoint de inferência hospedada (formato TEI `/rerank`)
                servindo BGE-Reranker-v2-m3 ou Qwen3-Reranker
  - `none`    → passthrough (default; caminho atual intocado)

Regras invioláveis: timeout de RERANK_TIMEOUT_SECONDS por chamada e FALLBACK
SILENCIOSO — qualquer falha devolve a ordem da fusão, nunca derruba a resposta.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_fixed

from src.shared.config import settings

logger = structlog.get_logger(__name__)


@dataclass
class RerankResult:
    """Posição original do documento e o score de relevância do juiz."""
    index: int
    score: float


class IRerankerProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    async def rerank(
        self,
        query: str,
        documents: List[str],
        top_n: int,
        instruction: Optional[str] = None,
    ) -> List[RerankResult]:
        """Reordena `documents` por relevância à `query` (maior score primeiro).

        `instruction` é contexto de negócio para rerankers instruction-following
        (ex.: "material da disciplina X, nível introdutório") — providers que
        não suportam, ignoram.
        """
        pass


def _http_timeout() -> httpx.Timeout:
    return httpx.Timeout(settings.RERANK_TIMEOUT_SECONDS)


class CohereReranker(IRerankerProvider):
    """Cohere Rerank v4.0 (Fast/Pro) — padrão corporativo, 100+ idiomas."""

    name = "cohere"

    def __init__(self):
        if not settings.COHERE_API_KEY:
            raise ValueError("COHERE_API_KEY is not set in environment.")

    @retry(wait=wait_fixed(0.2), stop=stop_after_attempt(2), reraise=True)
    async def rerank(self, query, documents, top_n, instruction=None):
        async with httpx.AsyncClient(timeout=_http_timeout()) as client:
            resp = await client.post(
                "https://api.cohere.com/v2/rerank",
                headers={"Authorization": f"Bearer {settings.COHERE_API_KEY}"},
                json={
                    "model": settings.COHERE_RERANK_MODEL,
                    "query": query,
                    "documents": documents,
                    "top_n": min(top_n, len(documents)),
                },
            )
            resp.raise_for_status()
            data = resp.json()
        return [RerankResult(index=r["index"], score=float(r["relevance_score"])) for r in data["results"]]


class VoyageReranker(IRerankerProvider):
    """Voyage rerank-2."""

    name = "voyage"

    def __init__(self):
        if not settings.VOYAGE_API_KEY:
            raise ValueError("VOYAGE_API_KEY is not set in environment.")

    @retry(wait=wait_fixed(0.2), stop=stop_after_attempt(2), reraise=True)
    async def rerank(self, query, documents, top_n, instruction=None):
        async with httpx.AsyncClient(timeout=_http_timeout()) as client:
            resp = await client.post(
                "https://api.voyageai.com/v1/rerank",
                headers={"Authorization": f"Bearer {settings.VOYAGE_API_KEY}"},
                json={
                    "model": settings.VOYAGE_RERANK_MODEL,
                    "query": query,
                    "documents": documents,
                    "top_k": min(top_n, len(documents)),
                },
            )
            resp.raise_for_status()
            data = resp.json()
        return [RerankResult(index=r["index"], score=float(r["relevance_score"])) for r in data["data"]]


class OpenReranker(IRerankerProvider):
    """Endpoint de inferência hospedada no formato TEI `/rerank`
    (BGE-Reranker-v2-m3 — baseline leve — ou Qwen3-Reranker, melhor open-source
    para QA multilíngue). Contrato:

        POST {OPEN_RERANKER_ENDPOINT}
        {"query": "...", "texts": ["...", ...]}
        → [{"index": 0, "score": 0.97}, ...]
    """

    name = "open"

    def __init__(self):
        if not settings.OPEN_RERANKER_ENDPOINT:
            raise ValueError("OPEN_RERANKER_ENDPOINT is not set in environment.")

    @retry(wait=wait_fixed(0.2), stop=stop_after_attempt(2), reraise=True)
    async def rerank(self, query, documents, top_n, instruction=None):
        headers = {"Content-Type": "application/json"}
        if settings.OPEN_RERANKER_API_KEY:
            headers["Authorization"] = f"Bearer {settings.OPEN_RERANKER_API_KEY}"
        payload: dict = {"query": query, "texts": documents}
        if settings.OPEN_RERANKER_MODEL:
            payload["model"] = settings.OPEN_RERANKER_MODEL
        if instruction:
            payload["instruction"] = instruction
        async with httpx.AsyncClient(timeout=_http_timeout()) as client:
            resp = await client.post(settings.OPEN_RERANKER_ENDPOINT, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
        results = [RerankResult(index=r["index"], score=float(r["score"])) for r in data]
        results.sort(key=lambda r: r.score, reverse=True)
        return results[: min(top_n, len(results))]


_PROVIDERS: dict[str, type[IRerankerProvider]] = {
    "cohere": CohereReranker,
    "voyage": VoyageReranker,
    "open": OpenReranker,
}

_instance: Optional[IRerankerProvider] = None
_instance_name: Optional[str] = None


def get_reranker(name: str | None = None) -> Optional[IRerankerProvider]:
    """Factory do reranker. `none` (default) → None = estágio desligado.

    `name is None` = usa o provider configurado (`settings.RERANKER_PROVIDER`);
    um `name` explícito porém vazio (`""`)/`"none"` é OFF determinístico — não
    consulta a config global (mantém a factory hermética e previsível).
    """
    global _instance, _instance_name
    if name is None:
        name = settings.RERANKER_PROVIDER
    provider_name = (name or "none").lower()
    if provider_name in ("none", ""):
        return None
    if _instance is not None and _instance_name == provider_name:
        return _instance
    cls = _PROVIDERS.get(provider_name)
    if cls is None:
        logger.warning("unknown_reranker_provider", provider=provider_name)
        return None
    _instance = cls()
    _instance_name = provider_name
    return _instance


async def rerank_with_fallback(
    query: str,
    documents: List[str],
    top_n: int,
    instruction: Optional[str] = None,
    provider: IRerankerProvider | None = None,
) -> Optional[List[RerankResult]]:
    """Aplica o reranker com FALLBACK SILENCIOSO.

    None = estágio indisponível (provider 'none', erro ou timeout) — o chamador
    mantém a ordem da fusão. Nunca levanta exceção (a resposta ao aluno não
    pode cair porque o juiz saiu do ar).
    """
    reranker = provider or get_reranker()
    if reranker is None or not documents:
        return None
    try:
        results = await reranker.rerank(query, documents, top_n=top_n, instruction=instruction)
        return results
    except Exception as e:
        logger.warning(
            "rerank_failed_falling_back_to_fusion_order",
            provider=getattr(reranker, "name", "?"),
            error=str(e)[:200],
        )
        return None
