# Resultados do Eval (F8) — tabela comparativa

## Validação LOCAL do reranker (F4) — 2026-07-03

Rodada de validação do estágio de reranking **de forma local** (dev), pelo
`scripts/eval_rag.py` contra o golden set curado com o corpus indexado no Qdrant
local.

**Setup local:**

- **Embedder:** Ollama `bge-m3` (denso 1024d) · collection `documents_bge1024`
  (**dense-only** — sem braço esparso, então `--stage hybrid` degrada para dense).
- **Reranker (`open`):** TEI (`text-embeddings-inference:cpu-1.6`) servindo
  `BAAI/bge-reranker-v2-m3` em **CPU**, exposto em `http://localhost:8080/rerank`
  (serviço `reranker` no `docker-compose.yml`). Contrato `/rerank` do TEI casa 1:1
  com o `OpenReranker` (request `{query,texts}` → `[{index,score}]`).
- **Overrides locais (CPU):** `RERANK_CANDIDATES=25`, `RERANK_TIMEOUT_SECONDS=120`
  (em prod voltam a 50 / 3.0 com reranker managed/GPU). `RERANK_SCORE_THRESHOLD=0.3`
  (default), `CRAG_GREEN=0.5`, `CRAG_RED=0.2`.
- **Golden set:** 25 linhas (19 em-escopo + 6 fora-de-escopo), curadas dos 3 cursos
  reais do corpus local — English Course (A1–C2), Gestão de Projetos (PMP) e
  Inteligência Artificial (Design Thinking / IA nos Negócios / PIBEP). 0 drafts.

| Métrica | baseline (dense) | +hybrid¹ | +rerank | +crag |
|---|---|---|---|---|
| Context Recall@5 (geral) | 100% (19/19) | 100% (19/19) | 94.7% (18/19) | 94.7% (18/19) |
| Recall@5 `termos_exatos` | 100% (6/6) | 100% (6/6) | 100% (6/6) | 100% (6/6) |
| Recall@5 `excecao_regra` | 100% (1/1) | 100% (1/1) | 100% (1/1) | 100% (1/1) |
| Recall@5 `capitulo_livro` | 100% (2/2) | 100% (2/2) | 100% (2/2) | 100% (2/2) |
| Recall@5 `conceitual` | 100% (10/10) | 100% (10/10) | 90% (9/10) | 90% (9/10) |
| Hit de fonte | 100% | 100% | 94.7% | 94.7% |
| MRR | 0.939 | 0.939 | 0.912 | 0.912 |
| Abstenção correta (`fora_do_escopo`) | n/a | n/a | n/a | **100% (6/6)** |
| Falsos-recusados | n/a | n/a | n/a | 5.3% (1/19) |
| Latência p50 / p95 (CPU) | 277 / 456 ms | 268 / 331 ms | 31.3 / 60.2 s | 35.3 / 65.2 s |
| Latência p50 / p95 (GPU²) | — | — | **10.2 / 13.9 s** | ~GPU (não re-rodado) |

¹ collection local é dense-only → `hybrid` degrada para dense (idêntico ao baseline);
o braço esparso RRF **não** é exercitado localmente (só existe após a reindexação F8).
² GPU = GTX 1660 Ti via microserviço PyTorch (`docker/reranker/`), `RERANK_CANDIDATES=25`,
`max_length=512`. Recall/MRR/abstenção são **idênticos** ao CPU (mesmo modelo/scores) — a GPU
só muda a latência. Detalhes e o gargalo em "Migração para GPU" abaixo.

**Leitura dos resultados:**

- **O funil está funcionando end-to-end:** rerank aplicado em **25/25** queries,
  **0 fallbacks silenciosos** (o TEI precisou de `--max-batch-tokens`/`--max-client-batch-size`
  maiores para aceitar os 25 candidatos numa chamada — ver `docker-compose.yml`).
- **Ganho principal = separação de abstenção (Recusa Honesta).** Os `best_score`
  do reranker separam com folga enorme em-escopo × fora-de-escopo:
  - fora-de-escopo (6): `0.0000–0.0009` (todos ≪ `CRAG_RED=0.2`).
  - em-escopo (19): a maioria `> 0.5`; o reranker também empurra quase tudo para
    **rank 1** (precisão@1 alta).
  - É por isso que o reranker é pré-requisito do CRAG-lite: sem o score dele, a
    abstenção não tem avaliador.
- **Recall/MRR levemente abaixo do dense é esperado neste corpus:** cada disciplina
  tem poucos documentos, então o dense já **satura** (100% recall, MRR 0.94) — não há
  headroom de recuperação. A única perda (`advertising in project procurement`,
  conceitual) foi um **corte de threshold**, não de recuperação: o chunk correto (curto)
  pontuou `0.121`, abaixo de `RERANK_SCORE_THRESHOLD=0.3`, e foi filtrado.
- **CRAG-lite (`--stage crag`) confirmou a Recusa Honesta:** abstenção correta
  **100% (6/6)** fora-de-escopo; o 🟡 (`implicature`, `best_score=0.399`) disparou
  **uma reescrita de query e recuperou** (não abstém). O único falso-recusado (5.3%,
  1/19) é o mesmo caso `advertising` (`0.121 < CRAG_RED=0.2`) — some ao baixar o
  threshold (abaixo).
- **Recomendação de calibração** (regra da casa §7.1 do `RAG_OPERATIONS.md`): os scores
  do `bge-reranker-v2-m3` são muito polarizados (≈1 relevante, ≈0 irrelevante); neste
  corpus vale **baixar `RERANK_SCORE_THRESHOLD` para ~0.1** (recupera o caso terso sem
  reabrir fora-de-escopo, já que estes ficam em ~0.0004). Nenhum ajuste de produção sem
  repetir o eval.

**Caveats:**

- **Latência de CPU (31–60 s/query)** é inviável para o bot ao vivo (o `RERANK_TIMEOUT_SECONDS=3`
  de prod cairia sempre no fallback). Uso ao vivo exige reranker **managed** (Cohere/Voyage)
  ou TEI em **GPU**. O valor local aqui é validar o encanamento e medir o delta, não servir tráfego.
- Como a collection local é dense-only, o delta do braço **híbrido** (símbolos/termos exatos)
  não foi medido — isso só sai na collection `documents_v2` da janela F8.

### Migração para GPU (GTX 1660 Ti, 6GB) — o gargalo é a GPU, não o serving

Para tirar o reranker da CPU (~2s/doc), migramos para GPU:

- **Infra:** `nvidia-container-toolkit` + runtime `nvidia` no Docker.
- **Serviço:** trocamos o TEI por um microserviço **PyTorch CUDA** (`docker/reranker/`,
  FastAPI + `sentence-transformers` CrossEncoder) servindo o **mesmo** `bge-reranker-v2-m3`
  e o **mesmo contrato `/rerank`** do TEI — zero mudança no provider `open`. FP16, o modelo
  carrega em ~1.1–2.8GB de VRAM.

**Achado (contra-intuitivo):** o PyTorch **empatou** com o TEI-Turing — o gargalo é a
**própria 1660 Ti** (sem tensor cores) rodando um XLM-RoBERTa-large de 560M, não o
framework de serving. A latência escala **linear com o nº de tokens** por par:

| max_length (tokens) | ms/doc | 25 docs | vs CPU |
|---|---|---|---|
| 512 (default) | ~528 ms | ~13 s | ~3.5x |
| 256 | ~261 ms | ~6.5 s | ~7x |
| 128 | ~132 ms | ~3.3 s | ~14x |

| stack (50 docs @ 512 tok) | latência |
|---|---|
| CPU (TEI) | ~60 s |
| GPU TEI-Turing (Candle, FP16) | ~40 s |
| GPU PyTorch CrossEncoder (FP16) | ~28 s |

**Conclusão operacional:**

- Para o **eval**, a GPU basta (roda em ~3–5 min); qualidade **idêntica** (mesmo modelo/scores).
- Para **servir tráfego ao vivo** nesta GPU, `bge-reranker-v2-m3` a 512 tok ainda é lento
  (>o `RERANK_TIMEOUT_SECONDS=3` de prod). Os levers reais são **truncar a sequência**
  (`RERANK_MAX_LENGTH=256` no serviço ≈ 7x, perda de qualidade a medir no eval) ou um
  **modelo menor** (mmarco-MiniLM ~118M / bge-reranker-base ~278M — decisão por eval).
  Em prod a decisão continua sendo reranker **managed** (Cohere/Voyage) ou GPU com tensor cores.

---

> A tabela abaixo é o **plano de produção (F8)** — preencher após a reindexação única
> (`scripts/reindex_all.py`), rodando `scripts/eval_rag.py --json` nas 5 configurações
> contra o golden set curado, com o embedder decidido pelo eval e a collection híbrida.

| Métrica | baseline (dense) | +chunking+contextual | +hybrid (embedder do eval) | +rerank | +crag |
|---|---|---|---|---|---|
| Context Recall@5 (geral) | | | | | |
| Recall@5 `termos_exatos` | | | | | |
| Recall@5 `excecao_regra` | | | | | |
| Recall@5 `capitulo_livro` | | | | | |
| Hit de fonte | | | | | |
| MRR | | | | | |
| Abstenção correta (`fora_do_escopo`) | n/a | n/a | n/a | n/a | |
| Falsos-recusados | n/a | n/a | n/a | n/a | |
| Latência p50 / p95 | | | | | |

**Metas:** recall@5 `termos_exatos` ≥ 95% · agregado ≥ 90% · abstenção
correta ≥ 90% com falsos-recusados ≤ 5%.

## Decisão de embedder (F3a — registrar os números que a justificaram)

| Candidato | Recall@5 geral | Recall@5 termos exatos | Custo / 1k chunks | Latência p95 query | Decisão |
|---|---|---|---|---|---|
| atual (azure, 1536d) | | | | | |
| bge-m3 (1024d, denso+esparso) | | | | | |
| _Plano B: atual + fastembed BM25_ | | | | | |

> Plano C (Qwen3-Embedding + MRL 4096→1024) só entra se A e B reprovarem —
> documentado, não implementado.

## Custos da reindexação (logados pelo worker)

- chunks totais: _pendente_
- chamadas de enriquecimento (F2): _pendente_
- tokens de prompt aproximados (e % cacheados pelo provider): _pendente_
