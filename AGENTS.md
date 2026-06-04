# AGENTS.md

## Project State

This is a **greenfield project in design phase** — no source code exists yet. The two design documents are the authoritative specification:
- `Estrutura do Projeto - Assistente WhatsApp.md` — detailed architecture, data model, pipeline pseudocode
- `Resumo Arquitetural - Assistente WhatsApp.md` — summary and engineering decisions

## Antigravity Kit

The `.agent/` directory contains Antigravity Kit — agent definitions, skills, workflows, and global rules. All agents must follow the protocol in `.agent/rules/GEMINI.md` (always-on). Key agents for this project: `mlops-expert`, `backend-specialist`, `database-architect`.

Validation scripts: `python .agent/scripts/checklist.py .` (pre-commit) and `python .agent/scripts/verify_all.py .` (pre-deploy).

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python (async everywhere) |
| API framework | FastAPI (ASGI — sync I/O blocks the event loop) |
| Orchestration | LangChain (`ConversationalRetrievalChain`, `ConversationBufferMemory`) |
| LLMs | Gemini (initial), DeepSeek (planned); embedding provider independent |
| Infra | Multi-Cloud (Azure, GCP, AWS) via `Makefile`. Docker-based with PostgreSQL, Redis, and Qdrant. |
| Vector DB | Qdrant Cloud (Scalar Quantization INT8) |
| Document parsing | Azure Document Intelligence |
| Retry | Tenacity (exponential backoff on all external APIs) |
| CI | GitHub Actions: ruff → mypy → pytest |

## Architecture: Hard Invariants

These are easily missed and cause production bugs if violated:

### Webhook (src/webhook/)
- **Returns HTTP 200 in <200ms — never calls LLM or executes business logic.** Only validates, routes, and enqueues.
- **Debouncing is in the Worker, NOT the webhook.** Sleeping in the webhook handler violates the 200ms SLA.
- Global `try/except` — Meta must never receive status != 2xx.
- Endpoint versioned: `/v1/webhook`
- **Telegram Webhook Security:** `setWebhook` API calls MUST pass `secret_token=TELEGRAM_WEBHOOK_SECRET` or the application will reject all incoming POSTs with `webhook_signature_failed`.
- `minReplicas=1` mandatory (cold start = ~5-30s, breaks Meta's 200ms SLA).
- Route `/` commands to `command_messages` queue **before** `inbound_messages`. Students sending commands are rejected at the edge without touching Service Bus.

### Mensageria / Filas (Agnostic via IMessageQueue)
- **Redis Streams** é o provedor nativo via variável `QUEUE_BACKEND=redis`.
- Azure Service Bus mantido como alternativa legada (`QUEUE_BACKEND=servicebus`).
- **NUNCA** injetar SDKs específicos de nuvem diretamente nos Workers ou no Webhook. O `QueueFactory` gerencia a abstração.

### Cache / Redis
- **`role:{phone}` stores `"student"` or `"teacher"`, NOT just a presence bit.** Needed for edge-level command rejection.
- Cache invalidation on role/status change must use Tenacity (3 retries) — silent failure leaves stale data.
- **`ConversationBufferMemory` only.** Never use `ConversationTokenBufferMemory` — it truncates by tokens in memory, conflicting with the LTRIM approach.
- `OptimizedRedisChatMessageHistory` intercepts message add and runs `LTRIM` natively (last 40 messages). Use `aadd_message` (async), not `add_message` (sync).
- Debounce drain uses an atomic Lua script to prevent race conditions between `LRANGE` and `DEL`.

### Data Model & PostgreSQL Strictness
- **`lesson_id` is a stable identifier, NOT a positional index.** Inserting a lesson in the sequence must not break existing student pointers.
- `StudentEnrollment(student_id, course_id, lesson_id)` — pointer is per enrollment (N:N), not per student.
- Lesson pointer advances based on `ScheduledClasses` + `Attendance.confirmed`, not raw numeric increment.
- **Every SQL and vector query MUST filter by `teacher_id` AND `course_id`.** Missing filters = data leak between courses.
- **PostgreSQL Case-Sensitivity:** ALL table names in Raw SQL must be explicitly quoted (e.g., `SELECT * FROM "Teachers"`).
- **PostgreSQL Data Types:** Do NOT use SQLite semantics for Booleans (use `true`/`false`, not `1`/`0`) and always pass native `datetime` Python objects to `asyncpg` bindings (do NOT use `.isoformat()` or `.strftime()` strings in parametrized queries).

### Qdrant
- **Scalar Quantization INT8 must be configured at collection creation** — not retroactive without full reindex.
- Hard delete of vectors scheduled via `ScheduledEnqueueTimeUtc` to 03:00 BRT (06:00 UTC). Deleting during peak hours degrades HNSW search latency for all students.
- `query_filter` must include `teacher_id`, `course_id`, and `lesson_id IN (allowed_ids_up_to_pointer)`.

### LLM / RAG
- **`str.format()` for System Prompt, NOT f-string.** F-strings conflict with LangChain template variables `{{{context}}}`, `{{{history}}}`, `{{{question}}}`.
- System Prompt rebuilt on every call (no caching) — anchors the student's current lesson ID to prevent recency bias.
- `LLMProvider` (completion) and `EmbeddingProvider` (embeddings) are separate interfaces. Swapping the generation LLM must not require reindexing.
- All LLM provider methods are async.

### Auth / Infra & Segredos (12-Factor App)
- **Nenhum Vendor Lock-in na configuração.** Segredos são mapeados estritamente via variáveis de ambiente no formato nativo (ex: `postgresql+asyncpg://...`).
- O módulo `src/shared/azure_kv.py` é importado apenas condicionalmente se `AZURE_KEY_VAULT_URL` existir (em ambientes Azure Bicep). Em GCP e AWS, usa-se a injeção nativa de variáveis.
- **Managed Identity (`DefaultAzureCredential`)** atua apenas no módulo opcional do Key Vault, evitando falhas em outros ambientes.

### Ingestion Worker
- `max_concurrent_calls=2` (per replica) + `maxReplicas=2` = max 4 concurrent Document Intelligence calls.
- Tenacity retry with exponential backoff on all external API calls (Document Intelligence, Embedding).
- `processing_started_at` column to detect stuck "processing" status (Timer Trigger every 15 min).

### DLQ
- Daily Timer Trigger (04:00 BRT) inspects and purges dead letters across 4 queues.
- `MAX_MESSAGES_PER_RUN=50`, `complete_message` (NOT abandon). Messages are logged then purged — no reprocessing.

## Directory Structure (planned)

```
src/webhook/        # FastAPI app, handlers, middleware
src/workers/        # LLM, Ingestion, Deletion, Command
src/rag/            # pipeline, prompt_builder, retriever
src/llm/            # base, factory, Gemini provider, DeepSeek provider
src/db/             # models, repositories, migrations
src/cache/          # Redis abstractions (debounce, whitelist, session)
src/drive/          # Google Drive sync, Service Account
src/shared/         # config, telemetry, constants, correlation
functions/          # Azure Functions (DLQ Janitor, Scheduler, Health Check)
infra/              # Bicep/Terraform, KEDA manifests
tests/unit/
tests/integration/
tests/e2e/
```

## Testing

- Unit: `pytest + pytest-asyncio` — mock external I/O
- Integration: `pytest + Qdrant Docker + PostgreSQL + Redis` (local infra emulated via `docker-compose.yml`)
- E2E: `pytest + WhatsApp Cloud API Sandbox` — validate 200ms webhook timeout
- Load: Locust — simulate N concurrent students, validate concurrency limits

## Google Drive Integration

- Service Account (not user OAuth). Teacher must manually share their Drive folder with the Service Account email.
- Painel must display the sharing instructions and Service Account email (from Key Vault, not hardcoded).
- Weekly Azure Function verifies Service Account access still works.

## Logging

- JSON structured (`structlog` or `python-json-logger`).
- Required dimensions: `correlation_id`, `teacher_id`, `student_id`, `course_id`, `operation`.
- Correlation ID generated at webhook, propagated through Service Bus payload to Worker.

## What NOT to do

- Do NOT call the LLM from the webhook handler.
- Do NOT use `ConversationTokenBufferMemory`.
- Do NOT use f-strings for LangChain templates.
- Do NOT use connection strings anywhere.
- Do NOT use positional indices for lesson pointers.
- Do NOT query without `teacher_id + course_id` filters.
- Do NOT hard-delete Qdrant vectors during business hours.
- Do NOT use synchronous I/O in FastAPI routes or Worker handlers.
