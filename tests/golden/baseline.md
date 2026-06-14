# Baseline do RAG (F0) — PENDENTE DE EXECUÇÃO NO CORPUS REAL

> **Status (2026-06-12):** o harness (`scripts/eval_rag.py`), o formato do
> golden set e as métricas estão prontos e testados (testes unitários das
> métricas em `tests/unit/test_rag_precision.py`). O baseline NUMÉRICO ainda
> não foi medido porque o ambiente local não tem credenciais (`.env` local é
> vazio; Qdrant Cloud + embedder Azure vivem em produção via Key Vault).
> **Nenhuma fase pode ser considerada "validada" antes desta medição** — o
> código novo fica atrás de flags (tudo OFF = sistema atual) exatamente por
> isso.

## Como medir (passo a passo)

1. Curar o golden set (ver `tests/golden/README.md`): 40-80 linhas com
   perguntas e substrings do corpus REAL (as linhas atuais são drafts).
2. Em ambiente com acesso (ex.: `ENV_FILE=.env.prod` para o Qdrant Cloud +
   chaves de embedder, ou `az containerapp exec` no worker-llm):

   ```bash
   python scripts/eval_rag.py --stage dense --json > tests/golden/baseline_dense.json
   python scripts/eval_rag.py --stage dense
   ```

3. Preencher a tabela abaixo e commitar.

## Baseline (preencher com a saída do harness)

| Métrica | dense (atual) |
|---|---|
| Context Recall@5 (geral, em escopo) | _pendente_ |
| Recall@5 — categoria `termos_exatos` | _pendente_ |
| Recall@5 — categoria `excecao_regra` | _pendente_ |
| Hit de fonte | _pendente_ |
| MRR | _pendente_ |
| Abstenção correta (`fora_do_escopo`) | n/a (sem CRAG no baseline) |
| Latência p50 / p95 | _pendente_ |

## Metas (da F8, números do relatório a validar no NOSSO corpus)

- Recall@5 "termos exatos" ≥ **95%**
- Recall@5 agregado ≥ **90%**
- Abstenção correta ≥ **90%**, com falsos-recusados ≤ **5%**

## Regra de ouro

**Nenhuma fase fecha sem delta registrado.** Toda mudança de camada reexecuta
o eval (`--stage` correspondente) e registra o delta aqui ou em `results.md`
(tabela comparativa das 5 configurações, F8).
