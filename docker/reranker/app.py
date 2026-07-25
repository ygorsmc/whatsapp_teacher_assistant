"""
Reranker local (F4) — microserviço PyTorch CUDA que serve um cross-encoder no
MESMO contrato `/rerank` do TEI, consumido pelo provider `open` (OpenReranker).

Motivação: a imagem `turing-*` do TEI (Candle) não tem kernels otimizados para
SM75 (GTX 16xx), então subutiliza a GPU (~0.6s/doc). O PyTorch usa cuBLAS na
1660 Ti com eficiência (~10-30ms/doc), mantendo o mesmo modelo e o mesmo contrato
HTTP — zero mudança no código do bot.

Contrato (idêntico ao TEI, o que o OpenReranker espera):
    POST /rerank  {"query": "...", "texts": ["...", ...]}   (model/instruction ignorados)
    → [{"index": 0, "score": 0.97}, ...]   (ordenado por score desc; sigmoid 0-1)
    GET  /health  → {"status": "ok"}
"""

import os
from typing import List, Optional

import torch
from fastapi import FastAPI
from pydantic import BaseModel
from sentence_transformers import CrossEncoder

MODEL_ID = os.getenv("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")
MAX_LENGTH = int(os.getenv("RERANK_MAX_LENGTH", "512"))
BATCH_SIZE = int(os.getenv("RERANK_BATCH_SIZE", "64"))

_device = "cuda" if torch.cuda.is_available() else "cpu"
_model = CrossEncoder(MODEL_ID, max_length=MAX_LENGTH, device=_device)
# FP16 na GPU (a 1660 Ti roda FP16 a taxa dobrada; metade da VRAM/latência).
if _device == "cuda":
    _model.model.half()

_sigmoid = torch.nn.Sigmoid()  # scores 0-1, como o TEI reranker (alinha limiares CRAG)

app = FastAPI(title="local-reranker")


class RerankRequest(BaseModel):
    query: str
    texts: List[str]
    # Campos que o OpenReranker pode enviar — aceitos e ignorados (compat TEI).
    model: Optional[str] = None
    instruction: Optional[str] = None
    raw_scores: Optional[bool] = None


class RerankResult(BaseModel):
    index: int
    score: float


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_ID, "device": _device}


@app.post("/rerank", response_model=List[RerankResult])
def rerank(req: RerankRequest):
    if not req.texts:
        return []
    pairs = [[req.query, t] for t in req.texts]
    with torch.inference_mode():
        scores = _model.predict(
            pairs,
            batch_size=BATCH_SIZE,
            activation_fn=_sigmoid,  # sentence-transformers 5.x (era activation_fct)
            convert_to_numpy=True,
            show_progress_bar=False,
        )
    results = [RerankResult(index=i, score=float(s)) for i, s in enumerate(scores)]
    results.sort(key=lambda r: r.score, reverse=True)
    return results
