# Reranker local em PyTorch CUDA (F4). Base com torch+CUDA já compilado para
# SM75+ (inclui a GTX 1660 Ti). Serve o cross-encoder no contrato /rerank do TEI.
FROM pytorch/pytorch:2.4.1-cuda12.1-cudnn9-runtime

# HF cache direto em /data (mesmo layout que o TEI usa → reaproveita o modelo já
# baixado; sem isso o HF_HOME=/data cai em /data/hub e re-baixa os 2GB).
ENV HF_HOME=/data \
    HF_HUB_CACHE=/data \
    HF_HUB_DISABLE_TELEMETRY=1 \
    TRANSFORMERS_NO_ADVISORY_WARNINGS=1

RUN pip install --no-cache-dir \
    "sentence-transformers==5.6.0" \
    "fastapi>=0.111" \
    "uvicorn[standard]>=0.30"

WORKDIR /app
COPY app.py /app/app.py

EXPOSE 80
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "80"]
