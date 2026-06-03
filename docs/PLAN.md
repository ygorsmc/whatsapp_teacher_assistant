# Plano de Limpeza do Repositório (Auditoria de Código)

**Objetivo:** Passar um pente-fino no código-fonte, removendo arquivos obsoletos, scripts de debug temporários e patches de infraestrutura que foram criados ao longo do desenvolvimento e que não fazem mais parte do funcionamento em produção do Assistente WhatsApp. Isso manterá o projeto enxuto e profissional.

## 🔴 User Review Required

> [!IMPORTANT]
> **Aprovação Necessária**
> Verifique a lista de arquivos identificados para exclusão abaixo. 
> Todos esses arquivos foram identificados como scripts locais de debug, logs ou patches que já cumpriram sua função e só estão gerando ruído no repositório.
> NENHUM arquivo fonte das pastas `src/` será apagado. O arquivo `command_worker.py` foi mantido pois suas funções são importadas e rodadas dentro do `llm_worker.py`.
> 
> **Você aprova a exclusão desses arquivos?**

## 🗑️ Arquivos Identificados para Exclusão (Fase 2)

Abaixo estão as 3 categorias de arquivos redundantes ou inúteis encontrados na raiz e na pasta `infra/`.

### 1. Scripts de Validação/Teste Locais (Raiz)
*Esses scripts foram usados para validar partes específicas da infraestrutura durante o desenvolvimento e não são usados em produção.*
- `check_msgs.py`, `check_queue.py`, `check_redis.py`, `check_sb.py`, `check_teachers.py`, `check_telemetry.py`
- `db_check.py`, `query_db.py`, `debug.py`, `patch.py`
- `test_cache.py`, `test_db.py`, `test_dlq.py`, `test_langchain_embed.py`, `test_qdrant_filter.py`, `test_rag.py`, `test_redis.py`, `test_sync.py`, `test_trigger.py`, `test_worker.py`
- `fix_default_teacher.py`, `fix_ids.py`, `fix_sync.py`, `move_pass.py`
- `peek_dlq.py`

### 2. Arquivos de Log Temporários (Raiz)
*Logs salvos localmente que não devem ser versionados e só ocupam espaço.*
- `full_logs.txt`
- `logs.txt`
- `sync_log.txt`

### 3. Patches e YAMLs Temporários (`infra/`)
*Foram usados scripts Python para injetar secrets nos manifestos do KEDA temporariamente durante os testes de deploy. Agora que o `deploy_app.sh` lida com as imagens via CLI, essas sobras podem sumir.*
- `infra/patch_keda.py`
- `infra/patch_keda_clean.py`
- `infra/patch_keda_fix.py`
- `infra/patch_scheduler.py`
- `infra/patch_yaml.py`
- `infra/worker-llm-keda-patch.yaml`
- `infra/worker-llm-keda-patch2.yaml`
- `infra/worker-llm-keda-patch3.yaml`
- `infra/worker-llm-patched.yaml`
- `infra/worker-scheduler-patched.yaml`

## ✅ Verification Plan
Na Fase 2, os agentes vão deletar esses arquivos. A verificação consistirá em rodar o comando `python3 doc_generator.py` novamente para confirmar que a contagem de arquivos rastreados caiu consideravelmente e apenas a arquitetura oficial sobrou.
