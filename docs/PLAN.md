# Faxina de Arquivos Não Utilizados (Cleanup Plan)

## Objetivo
O projeto acumulou diversos scripts de testes locais, *scratches* de exploração (ex: manipulação de Qdrant, testes do banco de dados na Azure) e arquivos temporários na raiz do repositório. O objetivo desta faxina é limpar o diretório raiz mantendo apenas os arquivos de configuração de infraestrutura (`Makefile`, `Dockerfile`, `docker-compose.yml`, etc) e remover scripts não oficiais ou migrá-los para pastas isoladas caso sejam úteis para auditorias futuras.

## Escopo de Arquivos

### 🗑️ 1. Arquivos para DELETAR (Lixo Puro / Temporários)
Arquivos gerados dinamicamente ou *outputs* de scripts rodados pontualmente:
- `db_fix_output.txt`
- `azure_env.json`
- `envs.json`

### 📁 2. Scripts Exploratórios e Fixes Manuais
Estes scripts foram usados para debugar e consertar o banco/Qdrant pontualmente. Muitos são cruciais para registro histórico de debug, então vamos movê-los para uma pasta específica de `_docs/scratch/` ou apagá-los se o professor preferir.

**Lista de Scripts soltos na Raiz:**
- `scratch.py` até `scratch7.py` (Testes pontuais de banco e planilhas)
- `scratch_qdrant.py`, `scratch_qdrant2.py`, `scratch_qdrant3.py` (Testes do VectorDB)
- `scratch_sheets.py`, `scratch_topics.py`
- `fix_db.py`, `fix_qdrant.py`, `fix_remote_db*.py`, `fix_stamp.py`
- `encode.py`, `encode2.py`
- `run_sync.py`, `check_redis.py`
- `patch_command.py`
- `doc_generator.py`
- `test_db.py`, `test_llm_pibep.py`, `test_qdrant_rag.py`, `test_query.py` (Testes que estão na raiz, deveriam ser deletados pois há a pasta `tests/`)

## Ação Proposta (Orquestração Fase 2)
Após a sua aprovação deste plano:
1. **[Deletar]** Apagaremos arquivos de texto inúteis.
2. **[Migrar ou Deletar]** Aguardamos a sua decisão se deseja deletar definitivamente os scripts da sessão 2 ou apenas varrê-los para uma pasta `scripts/legacy_scratch/`.

---

## ❓ Perguntas para Aprovação (User Review Required)

1. **Scripts Scratch:** Você prefere que eu **delete definitivamente** todos esses `scratch*.py`, `fix_*.py` e afins, ou que os mova para uma pasta isolada como `scripts/archive` só para ter de backup de código de consulta manual?
2. Concorda com o plano de ação (Y/N)?
