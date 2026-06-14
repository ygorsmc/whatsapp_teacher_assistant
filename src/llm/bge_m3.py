"""
BGE-M3 — Embedder híbrido via API de inferência hospedada (F3a).

O BGE-M3 (BAAI, 568M, open-source) gera num ÚNICO forward pass o vetor denso
(1024 dims) e os pesos esparsos lexicais — substituindo o pipeline paralelo de
BM25. Multilíngue forte em PT-BR. Decisão final de adoção é POR DADOS, no
eval da Fase 0 (`scripts/eval_rag.py --embedder bge-m3`).

Contrato da API (documentado — escolher endpoint compatível ao configurar):
    POST {BGE_M3_ENDPOINT}
    Authorization: Bearer {BGE_M3_API_KEY}        (se definido)
    {"inputs": ["texto 1", ...], "return_dense": true, "return_sparse": true}

    → {"dense": [[...1024 floats...], ...],
       "sparse": [{"indices": [int,...], "values": [float,...]}, ...]}

Também aceita o formato do FlagEmbedding server (`lexical_weights`:
{token_id: peso}), convertido para indices/values.

Plano B (se o eval reprovar o BGE-M3): manter o embedder denso atual e gerar
o braço esparso com BM25 local (`fastembed`, modelo "Qdrant/bm25") — ver
`FastembedSparseEncoder` (import-guarded; sem custo de API).
"""

from typing import List, Optional

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from src.llm.interfaces import HybridEmbeddingProvider
from src.shared.config import settings

logger = structlog.get_logger(__name__)


def _parse_sparse_item(item) -> dict:
    """Normaliza o vetor esparso para {"indices": [...], "values": [...]}.

    Aceita o formato nativo (indices/values) e o do FlagEmbedding
    (lexical_weights: {"1042": 0.31, ...}).
    """
    if isinstance(item, dict) and "indices" in item and "values" in item:
        return {"indices": list(item["indices"]), "values": list(item["values"])}
    # lexical_weights: dict token_id -> peso
    weights = item.get("lexical_weights", item) if isinstance(item, dict) else {}
    indices, values = [], []
    for token_id, weight in weights.items():
        try:
            indices.append(int(token_id))
            values.append(float(weight))
        except (TypeError, ValueError):
            continue
    return {"indices": indices, "values": values}


class BGEM3EmbeddingProvider(HybridEmbeddingProvider):
    """Provider 'bge-m3' — denso 1024 dims + esparso lexical em 1 chamada."""

    DIMENSIONS = 1024

    def __init__(self):
        if not settings.BGE_M3_ENDPOINT:
            raise ValueError("BGE_M3_ENDPOINT is not set in environment.")
        self.endpoint = settings.BGE_M3_ENDPOINT
        self.api_key = settings.BGE_M3_API_KEY
        self.timeout = settings.BGE_M3_TIMEOUT_SECONDS

    def get_dimensions(self) -> int:
        return self.DIMENSIONS

    @retry(wait=wait_exponential(multiplier=1, min=1, max=10), stop=stop_after_attempt(3), reraise=True)
    async def _call(self, texts: List[str], return_sparse: bool) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                self.endpoint,
                json={"inputs": texts, "return_dense": True, "return_sparse": return_sparse},
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        data = await self._call(texts, return_sparse=False)
        return [list(map(float, vec)) for vec in data["dense"]]

    async def aembed_query(self, text: str) -> List[float]:
        return (await self.aembed_documents([text]))[0]

    async def aembed_documents_hybrid(self, texts: List[str]) -> tuple[List[List[float]], List[dict]]:
        data = await self._call(texts, return_sparse=True)
        dense = [list(map(float, vec)) for vec in data["dense"]]
        sparse = [_parse_sparse_item(item) for item in data.get("sparse", [])]
        if len(sparse) != len(dense):
            # API sem braço esparso: degrada para denso (fallback sempre).
            logger.warning("bge_m3_sparse_missing", dense=len(dense), sparse=len(sparse))
            sparse = [{"indices": [], "values": []} for _ in dense]
        return dense, sparse

    async def aembed_query_hybrid(self, text: str) -> tuple[List[float], dict]:
        dense, sparse = await self.aembed_documents_hybrid([text])
        return dense[0], sparse[0]


class FastembedSparseEncoder:
    """Plano B do braço esparso: BM25 local via `fastembed` (Qdrant/bm25).

    Usado quando o embedder denso continua o atual (não-híbrido) mas a busca
    híbrida está ligada. Import-guarded: sem `fastembed` instalado, o chamador
    degrada para dense-only (fallback sempre — nunca derruba a resposta).
    """

    def __init__(self, model_name: str = "Qdrant/bm25"):
        from fastembed import SparseTextEmbedding  # import tardio (opcional)
        self._model = SparseTextEmbedding(model_name=model_name)

    async def encode(self, texts: List[str]) -> List[dict]:
        import asyncio
        def _run():
            return [
                {"indices": emb.indices.tolist(), "values": emb.values.tolist()}
                for emb in self._model.embed(texts)
            ]
        return await asyncio.get_event_loop().run_in_executor(None, _run)


_sparse_encoder: Optional[FastembedSparseEncoder] = None
_sparse_encoder_failed = False


async def encode_sparse_fallback(texts: List[str]) -> Optional[List[dict]]:
    """Braço esparso para embedders não-híbridos (Plano B). None = indisponível."""
    global _sparse_encoder, _sparse_encoder_failed
    if _sparse_encoder_failed:
        return None
    try:
        if _sparse_encoder is None:
            _sparse_encoder = FastembedSparseEncoder()
        return await _sparse_encoder.encode(texts)
    except Exception as e:
        _sparse_encoder_failed = True
        logger.warning("sparse_fallback_unavailable", error=str(e)[:200])
        return None
