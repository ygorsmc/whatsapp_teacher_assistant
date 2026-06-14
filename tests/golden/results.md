# Resultados do Eval (F8) — tabela comparativa

> Preencher após a reindexação única (`scripts/reindex_all.py`), rodando
> `scripts/eval_rag.py --json` nas 5 configurações abaixo contra o golden set
> CURADO (ver `README.md`). Cada coluna é um run; o diff entre os JSONs conta
> a contribuição de cada fase.

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
