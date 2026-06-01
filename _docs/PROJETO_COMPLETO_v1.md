# 📘 Guia Completo do Projeto: `whatsapp_teacher_assistant`

Este documento foi gerado automaticamente e contém a estrutura e o código-fonte do projeto.

> **Nota:** os comentários `<!-- FILE_PATH: ... -->` são invisíveis neste render, mas permitem reconstruir o projeto com `doc_reconstructor.py`.

---

## 🌳 Estrutura do Projeto

```
/home/ygor/Antigravity_Projects/whatsapp_teacher_assistant
├── AGENTS.md
├── doc_generator.py
├── docker-compose.yml
├── Dockerfile
├── emulator_config.json
├── Estrutura do Projeto - Assistente WhatsApp.md
├── global.env
├── global_example.env
├── local_dev.db
├── Makefile
├── Makefile_example
├── requirements.txt
├── Resumo Arquitetural - Assistente WhatsApp.md
├── scripts
│   ├── seed_local_db.py
│   ├── seed_qdrant.py
│   └── simulate_whatsapp.py
├── service_account.json
└── src
    ├── cache
    │   ├── debounce.py
    │   ├── __init__.py
    │   ├── redis_client.py
    │   └── session_history.py
    ├── db
    │   ├── database.py
    │   ├── __init__.py
    │   └── models.py
    ├── drive
    │   └── client.py
    ├── __init__.py
    ├── llm
    │   ├── factory.py
    │   ├── __init__.py
    │   ├── interfaces.py
    │   └── providers.py
    ├── rag
    │   ├── __init__.py
    │   ├── prompt_builder.py
    │   ├── qdrant_client.py
    │   ├── quiz_generator.py
    │   └── retriever.py
    ├── shared
    │   ├── config.py
    │   ├── __init__.py
    │   ├── service_bus.py
    │   ├── telemetry.py
    │   └── whatsapp_interactive.py
    ├── webhook
    │   ├── handlers.py
    │   ├── __init__.py
    │   ├── main.py
    │   └── security.py
    └── workers
        ├── ingestion_worker.py
        ├── __init__.py
        ├── llm_worker.py
        └── micro_learning.py
```

---

<!-- FILE_PATH: AGENTS.md -->
### 📄 `AGENTS.md`

```markdown
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
| Infra | Azure Container Apps + KEDA (Managed Identity), Azure Functions, Service Bus Standard, SQL DTU Basic, Redis, Blob, Key Vault |
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
- `minReplicas=1` mandatory (cold start = ~5-30s, breaks Meta's 200ms SLA).
- Route `/` commands to `command_messages` queue **before** `inbound_messages`. Students sending commands are rejected at the edge without touching Service Bus.

### Service Bus
- **Tier Standard** (required for session FIFO).
- `inbound_messages` queue: `requiresSession=true`, `LockDuration=PT5M` (default 30s is too short for LLM calls — causes infinite retry loops).
- Session lock renewal: background task renews lock every 60s during pipeline processing.
- Messages enqueued with `SessionId=phone` for FIFO per student.

### Cache / Redis
- **`role:{phone}` stores `"student"` or `"teacher"`, NOT just a presence bit.** Needed for edge-level command rejection.
- Cache invalidation on role/status change must use Tenacity (3 retries) — silent failure leaves stale data.
- **`ConversationBufferMemory` only.** Never use `ConversationTokenBufferMemory` — it truncates by tokens in memory, conflicting with the LTRIM approach.
- `OptimizedRedisChatMessageHistory` intercepts message add and runs `LTRIM` natively (last 40 messages). Use `aadd_message` (async), not `add_message` (sync).
- Debounce drain uses an atomic Lua script to prevent race conditions between `LRANGE` and `DEL`.

### Data Model
- **`lesson_id` is a stable identifier, NOT a positional index.** Inserting a lesson in the sequence must not break existing student pointers.
- `StudentEnrollment(student_id, course_id, lesson_id)` — pointer is per enrollment (N:N), not per student.
- Lesson pointer advances based on `ScheduledClasses` + `Attendance.confirmed`, not raw numeric increment.
- **Every SQL and vector query MUST filter by `teacher_id` AND `course_id`.** Missing filters = data leak between courses.

### Qdrant
- **Scalar Quantization INT8 must be configured at collection creation** — not retroactive without full reindex.
- Hard delete of vectors scheduled via `ScheduledEnqueueTimeUtc` to 03:00 BRT (06:00 UTC). Deleting during peak hours degrades HNSW search latency for all students.
- `query_filter` must include `teacher_id`, `course_id`, and `lesson_id IN (allowed_ids_up_to_pointer)`.

### LLM / RAG
- **`str.format()` for System Prompt, NOT f-string.** F-strings conflict with LangChain template variables `{{{context}}}`, `{{{history}}}`, `{{{question}}}`.
- System Prompt rebuilt on every call (no caching) — anchors the student's current lesson ID to prevent recency bias.
- `LLMProvider` (completion) and `EmbeddingProvider` (embeddings) are separate interfaces. Swapping the generation LLM must not require reindexing.
- All LLM provider methods are async.

### Auth / Infra
- **Managed Identity (`DefaultAzureCredential`) everywhere — zero connection strings in code.**
- KEDA scalers authenticate via `keda-mi-auth` (Managed Identity), not connection strings.
- Secrets via Azure Key Vault only.

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
- Integration: `pytest + Qdrant Docker + Azurite` (local Service Bus emulation)
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

```

<!-- FILE_PATH: Dockerfile -->
### 📄 `Dockerfile`

```dockerfile
# Usa uma imagem oficial do Python, versão slim para reduzir o tamanho (ideal para containers na nuvem)
FROM python:3.11-slim

# Define variáveis de ambiente
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

# Define o diretório de trabalho
WORKDIR /app

# Instala dependências do sistema necessárias (ex: para compilar pacotes C, se houver)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copia e instala as dependências do Python
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copia o código-fonte para o container
COPY ./src /app/src

# Expõe a porta 8000 (necessário apenas para o Webhook/FastAPI)
EXPOSE 8000

# Cria um usuário não-root por questões de segurança
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# O comando padrão iniciará o webhook. 
# Para rodar o worker no Azure Container Apps, basta sobrescrever o comando na definição do container:
# command: ["python", "-m", "src.workers.llm_worker"]
CMD ["uvicorn", "src.webhook.main:app", "--host", "0.0.0.0", "--port", "8000"]

```

<!-- FILE_PATH: Estrutura do Projeto - Assistente WhatsApp.md -->
### 📄 `Estrutura do Projeto - Assistente WhatsApp.md`

```markdown
# **Assistente WhatsApp para Professores — Estrutura do Projeto**

## **1\. Visão Geral da Arquitetura**

WhatsApp API  
     ↓  
\[Webhook Receiver\]                              ← retorna HTTP 200 em \< 200ms  
├── 0\. Validação de assinatura HMAC-SHA256 (X-Hub-Signature-256)  ← rejeita requests forjados  
├── 1\. Whitelist \+ role check (Cache-Aside: Redis → SQL) ← retorna "student" ou "teacher"  
├── 2\. Rate limiting por telefone (60/min)              ← proteção contra flood  
├── 3\. Filtro de tipo de mensagem (só texto)             ← executado ANTES do roteamento  
├── 4\. Roteamento de comandos (mensagens com /)          ← role=student → rejeita na borda  
├── 5\. Idempotência: lock por message\_id  
├── 6\. Enfileira → Service Bus: inbound\_messages (com SessionId \= phone, correlation\_id)  
└── 7\. Retorna HTTP 200 em \< 200ms — NUNCA executa lógica de negócio  
     ↓  
\[Service Bus\]  
├── inbound\_messages     → \[Worker LLM\]         ← pipeline RAG \+ debouncing \+ memória persistida em Redis (com LTRIM)  
├── command\_messages     → \[Command Handler\]    ← processa comandos /aula etc. (nomes com espaços)  
├── index\_documents      → \[Worker Ingestão\]    ← parse \+ embedding \+ Qdrant (maxReplicas=2, Tenacity)  
└── delete\_vectors (\*)   → \[Worker Exclusão\]    ← hard delete agendado para madrugada (KEDA garante acionamento)  
     (\*) mensagem enfileirada com ScheduledEnqueueTimeUtc \= 03:00 BRT

\[DLQ — Dead Letter Queue\]  
└── Timer Trigger diário → lê DLQ, consolida no Application Insights, expurga mensagens irrecuperáveis  
    (functionTimeout ajustado, MAX\_MESSAGES\_PER\_RUN \= 50\)

\[LLM Abstraction Layer\]  
├── Gemini Adapter  
└── DeepSeek Adapter

## **2\. Serviços Azure**

| **Função** | **Serviço** | **Observação** |  
| Webhook Receiver | Azure Container Apps | **minReplicas=1 obrigatório** — cold start (5–30s) quebraria o SLA de 200ms do Meta. Workers escalam via KEDA e podem ir a zero. |  
| Workers (LLM, Ingestão, Exclusão, Command) | Azure Container Apps | Escalam via KEDA a partir de zero. Worker de Exclusão noturna obrigatoriamente com KEDA azure-servicebus-queue para acordar. |  
| Triggers agendados | Azure Functions (Timer Trigger) | Scheduler de aulas, DLQ Janitor, health check do Qdrant e verificação de acesso à Service Account. |  
| Filas \+ DLQ | Azure Service Bus (Standard) | Tier Standard obrigatório para sessions FIFO. Filas: inbound\_messages, command\_messages, index\_documents, delete\_vectors (agendada). Autenticação via Managed Identity — nunca usar connection strings no código. |  
| Banco de dados relacional | Azure SQL Database (DTU Basic) | \~US$ 5/mês, sem cold start, sem auto-pause. |  
| Cache de sessão \+ whitelist \+ locks \+ histórico | Azure Cache for Redis | Idempotência, debouncing, sessão ativa, whitelist (Cache-Aside) e histórico de conversa via RedisChatMessageHistory. |  
| Banco vetorial | Qdrant Cloud (free tier) | Mesma região que os Container Apps — verificar antes de provisionar. |  
| Parse de documentos | Azure Document Intelligence | OCR avançado para PDFs pedagógicos. |  
| Orquestração RAG \+ Memória | LangChain \+ FastAPI | ConversationBufferMemory com backend Redis. |  
| Autenticação | Microsoft Entra ID | Login via Azure Static Web Apps. |  
| Segredos | Azure Key Vault | Armazenamento de chaves e strings de conexão. |  
| TTS | Azure Cognitive Services Speech | Geração de áudio. |  
| Arquivos de áudio | Azure Blob Storage | TTL de 24–48h. |  
| Logs e telemetria | Azure Application Insights | Erros do worker, tokens consumidos, falhas de ingestão, **alertas de custo do LLM**. |  
| Painel do professor | Azure Static Web App \+ Azure Function | |  
**Por que DTU Basic e não SQL Serverless?** O modelo Serverless cobra por segundo de vCore ativo. Um Timer Trigger de aquecimento manteria o banco ativo 24/7, anulando a economia. O DTU Basic oferece custo fixo de \~US$ 5/mês, sem cold start e sem armadilha de cobrança.  
**Alerta de capacidade DTU Basic:** 5 DTUs suportam carga transacional leve. Monitorar via Application Insights e definir limiar de upgrade para DTU Standard S0 (\~US$ 15/mês). Configurar alerta para uso acima de 70% por mais de 5 minutos consecutivos.  
**Banco vetorial — regra de deploy:** Verificar disponibilidade de região Azure no Qdrant Cloud **antes** de provisionar qualquer serviço. Todos os recursos devem ser alinhados à região disponível. Cruzamento de regiões adiciona 150–200ms por busca vetorial.  
**Qdrant Cloud free tier — limitação de inatividade:** Clusters gratuitos são suspensos após 1 semana de inatividade e deletados após 4 semanas. Para evitar suspensão acidental, uma Azure Function Timer Trigger executa um health check (busca simples no Qdrant) a cada 6 dias.  
**Alerta de custo LLM:** Configurar budgets no Azure Cost Management e alertas via Application Insights para limite diário de gasto com chamadas ao LLM (tokens). O custo pode escalar rapidamente com o número de alunos ativos.  
**Service Bus Session Lock Duration:** O lock duration padrão da session é 30s — insuficiente para chamadas ao LLM que frequentemente ultrapassam esse tempo. Se o lock expirar, a sessão é desbloqueada e a mensagem volta para a fila, causando loop infinito. **Correção:** Configurar `LockDuration` da fila inbound\_messages para 5 minutos (ISO 8601: `PT5M`) no momento da criação da fila.  
**Estratégia de backup:** SQL Database possui backup automatizado (7–35 dias retenção, conforme tier). Redis deve ter persistência AOF habilitada (appendonly yes) para sobreviver a reinicializações sem perda de sessão. Qdrant Cloud (tier pago) oferece snapshots programados; no free tier, a reindexação a partir dos PDFs é o mecanismo de recuperação.

## **3\. Ambientes de Desenvolvimento**

| **Ambiente** | **Banco Vetorial** | **Objetivo** |  
| Desenvolvimento local | Qdrant via Docker | Sem restrições, sem custo, sem risco de expiração. |  
| Testes de integração | Qdrant Cloud free tier | Valida conectividade e latência com os serviços Azure. |  
| Produção | Qdrant Cloud pago | Mesma região, alta disponibilidade. |

## **3\.1 Endpoint do Webhook**

O webhook deve usar URL versionada para permitir evolução futura sem breaking changes:
`/v1/webhook` — prefixo de versão obrigatório. Se a estrutura do payload mudar, criar `/v2/webhook` mantendo o anterior ativo durante a transição.

## **3\.2 Estrutura de Pastas do Código-Fonte**

```
src/
  webhook/           ← FastAPI app, handlers, middleware
  workers/           ← LLM, Ingestão, Exclusão, Command
  rag/               ← pipeline, prompt_builder, retriever
  llm/               ← base, factory, providers (Gemini, DeepSeek)
  db/                ← models, repositories, migrations
  cache/             ← Redis abstractions (debounce, whitelist, session)
  drive/             ← Google Drive sync, Service Account
  shared/            ← config, telemetry, constants, correlation
functions/           ← Azure Functions (DLQ Janitor, Scheduler, Health Check)
infra/               ← Bicep/Terraform, KEDA manifests
tests/
  unit/
  integration/
  e2e/
```

## **4\. Camada de Abstração de LLM**

Toda chamada ao LLM passa por uma interface única. Troca de provider \= mudar uma variável de ambiente, sem alteração de código.  

**Hierarquia de interfaces — Separação de responsabilidades:**

\# llm/base.py  
class LLMProvider(ABC):  
    """Interface para geração de texto (completion)."""  
    @abstractmethod  
    async def complete(self, prompt: str, context: list) \-\> str: ...

class EmbeddingProvider(ABC):  
    """Interface para geração de embeddings — independente do provider de completion."""  
    @abstractmethod  
    async def embed(self, text: str) \-\> list\[float\]: ...

\# llm/factory.py — Registry Pattern (extensível sem alterar código)  
\_providers: dict\[str, Type\[LLMProvider\]\] \= {}  
\_embedders: dict\[str, Type\[EmbeddingProvider\]\] \= {}

def register\_provider(name: str, cls: Type\[LLMProvider\]):  
    \_providers\[name\] \= cls

def register\_embedder(name: str, cls: Type\[EmbeddingProvider\]):  
    \_embedders\[name\] \= cls

def get\_provider() \-\> LLMProvider:  
    name \= os.getenv("LLM\_PROVIDER", "gemini")  
    cls \= \_providers.get(name)  
    if cls is None:  
        raise ValueError(f"Provider '{name}' não registrado.")  
    return cls()

def get\_embedder() \-\> EmbeddingProvider:  
    name \= os.getenv("EMBEDDING\_PROVIDER", "multilingual-e5")  
    cls \= \_embedders.get(name)  
    if cls is None:  
        raise ValueError(f"Embedder '{name}' não registrado.")  
    return cls()

\# No módulo de cada provider, o registro é feito automaticamente no import:  
\# llm/gemini\_provider.py  
from llm.factory import register\_provider  
register\_provider("gemini", GeminiProvider)  
register\_embedder("gemini", GeminiEmbedder)

**Regra arquitetural:** Todos os métodos devem ser async. FastAPI opera sobre ASGI — qualquer I/O síncrono bloqueia o event loop.  
**Embeddings independentes do provider de geração:** As interfaces `LLMProvider` (completion) e `EmbeddingProvider` (embeddings) são separadas — trocar o LLM de geração não exige reindexação. Recomendado: multilingual-e5 (open source) ou text-embedding-3-small (OpenAI).  
O cliente Qdrant deve ser isolado atrás de uma interface própria para permitir migração sem impacto no restante do código.

## **5\. Estrutura de Pastas no Google Drive**

/curso-ingles/  
  /B1/  
    sequencia.json        ← \["les\_01", "les\_02", ...\]  (IDs estáveis, não índices)  
    aula\_01.pdf           ← mapeado como "les\_01"  
    aula\_02.pdf           ← mapeado como "les\_02"  
    ...  
  /B2/  
    sequencia.json        ← \["les\_B2\_01", "les\_B2\_02", ...\]  
    ...

/curso-matematica/  
  /basico/  
    sequencia.json        ← \["mat\_01", "mat\_02", ...\]  
    aula\_01.pdf           ← mapeado como "mat\_01"  
    ...

A integração é feita via **Service Account**, sem depender do login do professor. A sincronização é disparada manualmente via botão **"Sincronizar Arquivos"** no painel.

### **⚠️ Requisito obrigatório de configuração — Service Account**

Uma Service Account é um "usuário invisível" com seu próprio endereço de e-mail (ex: bot-ingles@projeto.iam.gserviceaccount.com). Ela não tem acesso automático às pastas privadas do Google Drive do professor — o compartilhamento precisa ser feito manualmente uma única vez.  
O painel do professor deve exibir o seguinte aviso com destaque visual antes do botão "Sincronizar":  
┌─────────────────────────────────────────────────────────────────┐  
│  📁  Antes de sincronizar, compartilhe sua pasta no Drive       │  
│                                                                 │  
│  1\. Abra o Google Drive e localize a pasta raiz do seu curso    │  
│  2\. Clique com o botão direito → "Compartilhar"                 │  
│  3\. Adicione o e-mail abaixo como "Leitor":                     │  
│                                                                 │  
│     bot-ingles@projeto.iam.gserviceaccount.com    \[Copiar\]      │  
│                                                                 │  
│  ✅ Feito isso, clique em "Sincronizar Arquivos"                │  
└─────────────────────────────────────────────────────────────────┘

**Comportamento sem compartilhamento:** o Worker de Ingestão não encontrará arquivos no Drive e encerrará sem erros — o professor verá zero arquivos sincronizados e pode confundir com um bug. O aviso visual previne essa frustração. O e-mail da Service Account deve ser lido dinamicamente a partir do Key Vault ou de variável de ambiente, e não hardcoded no frontend.

### **🔄 Verificação Periódica de Acesso**

Para detectar perda de acesso à Service Account (ex: revogação acidental de permissão), uma Azure Function semanal tenta listar um arquivo conhecido da pasta do professor. Em caso de falha, um alerta é registrado no Application Insights e uma notificação é enviada ao painel do professor.

## **6\. Mapeamento de Aulas por Aluno**

### **Correção de design: lesson\_id estável, não índice posicional**

O ponteiro do aluno **não** é um índice numérico da posição no array `sequencia.json`. Se o professor inserir `aula_02b.pdf` entre `aula_02.pdf` e `aula_03.pdf`, um índice posicional quebraria o alinhamento de todos os alunos a partir da posição 3.  
**Correção:** `lesson_pointer` armazena um `lesson_id` estável. O `sequencia.json` define a ordem de precedência (`["les_01", "les_02", "les_03", ...]`), e o ponteiro avança seguindo essa ordem, não incremento numérico bruto.

```json
// sequencia.json — referências estáveis
["lesson_uuid_001", "lesson_uuid_002", "lesson_uuid_003", ...]
```

### **Modelo de dados: Cursos, Alunos e Matrícula (N:N)**

```sql
-- Tabela de cursos — obrigatória para validar course_id e associar ao professor
CREATE TABLE Courses (
    id          VARCHAR PRIMARY KEY,   -- ex: "B1", "basico", slug único
    teacher_id  VARCHAR NOT NULL REFERENCES Teachers(id),
    name        VARCHAR NOT NULL,      -- "Inglês B1", "Matemática Básica"
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE Students (
    id         VARCHAR PRIMARY KEY,
    name       VARCHAR NOT NULL,
    phone      VARCHAR UNIQUE NOT NULL,
    is_active  BOOLEAN DEFAULT TRUE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

Um aluno pode estar em múltiplos cursos simultaneamente. O ponteiro é por matrícula, não por aluno:

```sql
CREATE TABLE StudentEnrollment (
    enrollment_id  VARCHAR PRIMARY KEY,
    student_id     VARCHAR NOT NULL REFERENCES Students(id),
    course_id      VARCHAR NOT NULL REFERENCES Courses(id),
    lesson_id      VARCHAR NOT NULL,  -- ponteiro: última aula concluída (lesson_id estável)
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (student_id, course_id)
);
```

Nível B1: sequencia.json → `["les_01", "les_02", "les_03", ...]`  
Aluno João:  lesson_id = `les_04` → última aula = aula_04.pdf  
Aluno Maria: lesson_id = `les_07` → última aula = aula_07.pdf  
Aluno João (matemática): lesson_id = `mat_02` → ponteiro independente

### **Avanço automático do ponteiro**

O avanço automático depende de duas tabelas ausentes no design original:

```sql
CREATE TABLE ScheduledClasses (
    class_id       VARCHAR PRIMARY KEY,
    teacher_id     VARCHAR NOT NULL REFERENCES Teachers(id),
    course_id      VARCHAR NOT NULL REFERENCES Courses(id),
    lesson_id      VARCHAR NOT NULL,  -- qual aula do sequencia.json
    scheduled_date DATE NOT NULL,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE Attendance (
    attendance_id  VARCHAR PRIMARY KEY,
    student_id     VARCHAR NOT NULL REFERENCES Students(id),
    class_id       VARCHAR NOT NULL REFERENCES ScheduledClasses(class_id),
    confirmed      BOOLEAN NOT NULL DEFAULT FALSE,
    created_at     DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (student_id, class_id)
);
```

### **Índices explícitos (além dos gerados por UNIQUE)**

```sql
-- Queries frequentes sem cobertura automática de UNIQUE:
CREATE INDEX idx_sync_teacher_status ON file_sync_state(teacher_id, status);
CREATE INDEX idx_scheduled_course_date ON ScheduledClasses(course_id, scheduled_date);
CREATE INDEX idx_courses_teacher ON Courses(teacher_id);
```

### **Tabela de Quizzes Determinísticos**

Quando o pipeline RAG gera uma questão de múltipla escolha (micro-learning ou revisão), o resultado é salvo em formato JSON estruturado. A validação da resposta do aluno é feita por comparação direta (`if/else`), sem chamar o LLM novamente.

```sql
CREATE TABLE PendingQuizzes (
    quiz_id          VARCHAR PRIMARY KEY,
    student_id       VARCHAR NOT NULL REFERENCES Students(id),
    course_id        VARCHAR NOT NULL REFERENCES Courses(id),
    lesson_id        VARCHAR NOT NULL,            -- aula de referência para a pergunta
    question_text    TEXT NOT NULL,               -- pergunta exibida ao aluno
    alternatives     JSONB NOT NULL,              -- [{"id": "A", "text": "..."}, ...]
    correct_answer   VARCHAR NOT NULL,            -- id da alternativa correta (ex: "B")
    explanation      TEXT NOT NULL,               -- explicação pedagógica pré-gerada pelo LLM
    status           VARCHAR DEFAULT 'pending',   -- 'pending' | 'answered' | 'expired'
    student_answer   VARCHAR,                     -- id da alternativa escolhida pelo aluno
    is_correct       BOOLEAN,                     -- resultado da comparação determinística
    answered_at      DATETIME,
    expires_at       DATETIME NOT NULL,           -- TTL para expirar quizzes não respondidos
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at       DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_quiz_student_status ON PendingQuizzes(student_id, status);
CREATE INDEX idx_quiz_expires ON PendingQuizzes(expires_at) WHERE status = 'pending';
```

**Fluxo de validação determinística:**
```
[LLM gera quiz em JSON estruturado]
        ↓
[Salva no PendingQuizzes com status='pending']
        ↓
[Envia ao aluno via Reply Buttons ou List Message]
        ↓
[Aluno clica em uma opção → payload com quiz_id + answer_id]
        ↓
[Backend: if student_answer == correct_answer → is_correct=True]
        ↓
[Envia explicação pré-gerada — zero tokens consumidos]
```

**Vantagens:**
- Latência de resposta < 50ms (lookup + comparação, sem round-trip ao LLM)
- Zero risco de alucinação na correção
- Economia crítica de tokens (explicação já foi gerada junto com a pergunta)

### **Tabela de Controle de Message Templates**

Controla os templates aprovados pelo Meta Business Manager, utilizados pelo Scheduler para abrir janelas de serviço de 24h.

```sql
CREATE TABLE MessageTemplates (
    template_id      VARCHAR PRIMARY KEY,
    teacher_id       VARCHAR NOT NULL REFERENCES Teachers(id),
    template_name    VARCHAR NOT NULL,            -- nome registrado no Meta (ex: "daily_review")
    language_code    VARCHAR NOT NULL DEFAULT 'pt_BR',
    category         VARCHAR NOT NULL,            -- 'UTILITY' | 'MARKETING'
    status           VARCHAR NOT NULL,            -- 'APPROVED' | 'PENDING' | 'REJECTED'
    header_text      TEXT,
    body_text        TEXT NOT NULL,               -- texto do template com placeholders {{1}}, {{2}}
    footer_text      TEXT,
    has_buttons      BOOLEAN DEFAULT FALSE,
    button_config    JSONB,                       -- configuração dos botões do template
    meta_template_id VARCHAR,                     -- ID retornado pelo Meta após aprovação
    created_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at       DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_template_teacher ON MessageTemplates(teacher_id, status);
```

Fluxo de avanço:
```
[Data da aula passou + Attendance.confirmed = TRUE]
                    ↓
     Sistema avança a lesson_id do aluno para a PRÓXIMA
     aula na ordem definida pelo sequencia.json do curso
```

Se o aluno não confirmou ou faltou, o ponteiro não avança. O Scheduler (seção 9.2) é responsável por gerar mensagens de confirmação e registrar presença.

### **Filtro de contexto no RAG**

A query ao Qdrant filtra por `lesson_id` que aparece **até a posição atual** do ponteiro (inclusive), conforme a ordem do `sequencia.json`, e por `teacher_id` + `course_id`.

### **⚠️ Recency Bias — ancoragem da aula atual no System Prompt**

À medida que o aluno avança (ex: aula 50), a base de busca abrange todo o histórico de aulas. O banco vetorial pode retornar chunks de aulas antigas com alta similaridade vetorial — mas que usam premissas defasadas em relação ao nível atual do aluno. Isso dilui a precisão da resposta gerada (fenômeno conhecido como *Lost in the Middle*).  
**Correção — ancoragem explícita via System Prompt:** A solução mais pragmática é injetar o ponteiro do aluno diretamente no System Prompt antes de cada chamada ao LLM. O modelo recebe uma âncora explícita de contexto temporal e prioriza o conteúdo da aula atual mesmo que chunks de aulas anteriores apareçam nos resultados do Qdrant.  
A estrutura completa do System Prompt — incluindo esse mecanismo de ancoragem — está especificada na seção 9.8.  
**Por que System Prompt e não reranking vetorial?** Reranking por lesson\_number exigiria pós-processamento dos resultados do Qdrant antes de montar o contexto, adicionando latência e complexidade. A âncora no System Prompt é aplicada em uma única instrução, sem round-trip adicional, e o LLM a respeita consistentemente quando a diretriz é clara e posicionada no início do prompt.

### **Comportamento por situação**

| **Situação** | **O que acontece** |  
| Aula confirmada e data passou | Ponteiro avança para a próxima lesson\_id no sequencia.json |  
| Aluno faltou ou não confirmou | Ponteiro permanece na posição atual |  
| Aluno entrou no meio do curso | Professor usa override uma vez |  
| Professor adiciona aula nova (final) | Atualiza somente o sequencia.json — ponteiros não são afetados |  
| Professor insere aula no meio da sequência | lesson\_id não muda — ponteiros permanecem alinhados |  
| Turma inteira no mesmo ritmo | Todos os alunos com mesmo ponteiro avançam juntos |

### **Override pelo professor**

Professor envia: /aula @João 6  
Sistema responde: ✓ João atualizado para aula 6

O parser do comando /aula aceita nomes com espaços. Exemplo: /aula Maria Silva 7 também é interpretado corretamente. Detalhes na seção 9.3.1.  
O override atualiza a `lesson_id` do aluno na tabela `StudentEnrollment`, usando o N-ésimo elemento do `sequencia.json`.

## **7\. Pipeline RAG e Ingestão de Documentos**

### **Ingestão assíncrona com controle de hash e diff**

async def sync\_drive(teacher\_id):  
    drive\_files \= await get\_drive\_file\_ids(teacher\_id)  
    db\_files    \= await get\_synced\_file\_ids(teacher\_id)

    to\_index  \= drive\_files \- db\_files  
    to\_update \= await files\_with\_changed\_hash(drive\_files)  
    to\_delete \= db\_files \- drive\_files

    for file\_id in to\_delete:  
        await db.update("file\_sync\_state", file\_id, is\_deleted=True)  
        await service\_bus.enqueue(  
            queue="delete\_vectors",  
            payload={"file\_id": file\_id},  
            scheduled\_enqueue\_time=next\_3am\_utc()  \# Service Bus agenda entrega  
        )

    for file\_id in (to\_index | to\_update):  
        await service\_bus.enqueue("index\_documents", file\_id=file\_id)

### **Agendamento via Service Bus (sem Timer Trigger)**

from datetime import datetime, timezone, timedelta

def next\_3am\_utc() \-\> datetime:  
    now    \= datetime.now(timezone.utc)  
    target \= now.replace(hour=6, minute=0, second=0, microsecond=0)  
    \# 03:00 BRT \= 06:00 UTC  
    \# Premissa: UTC-3 fixo. O Brasil não observa horário de verão desde 2019.  
    \# Se isso mudar, substituir por zoneinfo.ZoneInfo("America/Sao\_Paulo").  
    if target \<= now:  
        target \+= timedelta(days=1)  
    return target

O Service Bus entrega a mensagem apenas no horário configurado. Um Service Bus Trigger padrão acorda o Worker de Exclusão quando a mensagem fica disponível. Para garantir que o Container App do Worker de Exclusão acorde nesse momento (fora do pico), é obrigatório configurar um KEDA Scaler baseado na fila delete\_vectors.  
**Manifesto do KEDA (Evitando Connection Strings):**  
scale:  
  minReplicas: 0  
  maxReplicas: 1  
  triggers:  
    \- type: azure-servicebus  
      metadata:  
        queueName: delete\_vectors  
        messageCount: "1"  
      authenticationRef:  
        name: keda-mi-auth \# OBRIGATÓRIO: Uso de Managed Identity do Container App

**Por que não Timer Trigger consumindo fila?** Timer Trigger é projetado para rotinas de relógio, não para consumir filas. Pull manual de Service Bus via Timer requer gerenciamento explícito de message lock e é propenso a travamentos. O ScheduledEnqueueTimeUtc nativo do Service Bus é o mecanismo correto para mensagens com entrega futura, mas exige o KEDA Scaler para acionar a réplica.

### **⚠️ Controle de concorrência e rate limit na ingestão**

Quando o professor sincroniza muitos arquivos de uma vez (ex: 30 PDFs), o Service Bus enfileira 30 mensagens em index\_documents. O Azure Container Apps escala automaticamente e pode criar dezenas de instâncias do Worker em paralelo.  
Se não houver restrição, 5 réplicas rodando paralelamente podem gerar chamadas massivas ao Azure Document Intelligence, causando HTTP 429 Too Many Requests.  
**Correção obrigatória — três mecanismos combinados:**  
**1\. Limitar concorrência no consumidor do Service Bus (max\_concurrent\_calls):**  
\# worker/ingestion\_worker.py  
from azure.servicebus.aio import ServiceBusClient  
from azure.identity.aio import DefaultAzureCredential

FULLY\_QUALIFIED\_NAMESPACE \= os.getenv("SERVICEBUS\_FQDN")  \# ex: "meu-namespace.servicebus.windows.net"  
QUEUE\_NAME \= "index\_documents"

\# Valor conservador: 2 a 3 instâncias processando em paralelo por réplica.  
MAX\_CONCURRENT\_CALLS \= int(os.getenv("INGESTION\_MAX\_CONCURRENT\_CALLS", "2"))

async def run\_ingestion\_worker():  
    credential \= DefaultAzureCredential()  
    async with ServiceBusClient(  
        fully\_qualified\_namespace=FULLY\_QUALIFIED\_NAMESPACE,  
        credential=credential,  
    ) as client:  
        async with client.get\_queue\_receiver(  
            queue\_name=QUEUE\_NAME,  
            max\_wait\_time=5,  
            prefetch\_count=MAX\_CONCURRENT\_CALLS,  \# não busca mais mensagens do que processa  
        ) as receiver:  
            semaphore \= asyncio.Semaphore(MAX\_CONCURRENT\_CALLS)

            async def handle\_with\_semaphore(msg):  
                async with semaphore:  
                    await process\_document\_message(msg)  
                    await receiver.complete\_message(msg)

            async for message in receiver:  
                asyncio.create\_task(handle\_with\_semaphore(message))

**2\. Limitar número máximo de réplicas do Worker de Ingestão (maxReplicas):**  
Para evitar escalonamento horizontal descontrolado, o Container App do Worker de Ingestão deve ter limite no KEDA:  
scale:  
  minReplicas: 0  
  maxReplicas: 2  \# máximo de 2 réplicas processando em paralelo  
  triggers:  
    \- type: azure-servicebus  
      metadata:  
        queueName: index\_documents  
        messageCount: "1"  
      authenticationRef:  
        name: keda-mi-auth

Com max\_concurrent\_calls=2 e maxReplicas=2, teremos no máximo 4 chamadas simultâneas ao Document Intelligence. **Mitigação adicional:** se o projeto escalar massivamente, avaliar o uso do Azure API Management (APIM) na frente do Document Intelligence para aplicar políticas globais de *rate-limit*.  
**3\. Retry com exponential backoff via Tenacity nas chamadas às APIs externas:**  
\# worker/document\_processor.py  
from tenacity import (  
    retry,  
    stop\_after\_attempt,  
    wait\_exponential,  
    retry\_if\_exception\_type,  
    before\_sleep\_log,  
)  
import logging  
import httpx

logger \= logging.getLogger(\_\_name\_\_)

@retry(  
    retry=retry\_if\_exception\_type(httpx.HTTPStatusError),  
    stop=stop\_after\_attempt(5),  
    wait=wait\_exponential(multiplier=1, min=4, max=60),  
    before\_sleep=before\_sleep\_log(logger, logging.WARNING),  
    reraise=True,  
)  
async def call\_document\_intelligence(pdf\_bytes: bytes) \-\> dict:  
    """Parse o PDF via Azure Document Intelligence com retry automático em 429.  
    ⚠️ Em produção, substituir httpx por DocumentAnalysisClient do SDK oficial  
       com DefaultAzureCredential (Managed Identity). O header Ocp-Apim-Subscription-Key  
       é usado aqui como pseudocódigo simplificado — viola a regra de zero connection strings.  
    """  
    async with httpx.AsyncClient() as client:  
        response \= await client.post(  
            url=os.getenv("DOCUMENT\_INTELLIGENCE\_ENDPOINT"),  
            content=pdf\_bytes,  
            headers={"Ocp-Apim-Subscription-Key": os.getenv("DOCUMENT\_INTELLIGENCE\_KEY")},  
            timeout=120,  
        )  
        response.raise\_for\_status()  \# lança HTTPStatusError em 429 → Tenacity reage  
        return response.json()

@retry(  
    retry=retry\_if\_exception\_type(httpx.HTTPStatusError),  
    stop=stop\_after\_attempt(5),  
    wait=wait\_exponential(multiplier=1, min=4, max=60),  
    before\_sleep=before\_sleep\_log(logger, logging.WARNING),  
    reraise=True,  
)  
async def call\_embedding\_api(text: str) \-\> list\[float\]:  
    """Vetoriza texto com retry automático em caso de 429 ou falha transitória."""  
    embedder \= get\_embedder()  
    return await embedder.embed(text)

**Monitoramento adicional:** Configurar alerta no Application Insights para taxa de HTTP 429 elevada por mais de 10 minutos consecutivos, indicando necessidade de ajuste de concorrência ou aumento de cota.

### **Estratégia de exclusão de vetores**

| **Etapa** | **Quando** | **O que faz** |  
| Soft delete no SQL | Imediato, na sincronização | is\_deleted \= True — visível no painel do professor |  
| Hard delete no Qdrant | Madrugada, via mensagem agendada | Worker de Exclusão consome delete\_vectors, remove vetores fisicamente |  
A flag is\_deleted no SQL serve apenas para o painel do professor. Ela não é usada como filtro nas queries do Qdrant — os vetores são fisicamente removidos na madrugada, eliminando a necessidade de sincronização de estado entre dois sistemas.

### **Fluxo completo de sincronização**

\[Botão "Sincronizar"\]  
     ↓  
Diff: Drive vs. file\_sync\_state (SQL)  
     ├── Arquivos removidos → is\_deleted=True (SQL)  
     │                     \+ Service Bus: delete\_vectors (entrega às 03:00 BRT)  
     └── Arquivos novos/modificados → Service Bus: index\_documents (imediato)  
                                              ↓  
                                    \[Worker de Ingestão\]  
                                    (max\_concurrent\_calls=2, maxReplicas=2, Tenacity retry)  
                                    LangChain \+ AzureAIDocumentIntelligenceReader  
                                    → Parse e chunking semântico  
                                    → Embedding (multilingual-e5)  
                                    → Qdrant Cloud (com metadados)  
                                    → file\_sync\_state: status='concluído'

\[Service Bus entrega delete\_vectors às 03:00 BRT\]  
     ↓  
\[Worker de Exclusão\]  ← KEDA Scaler aciona réplica  
├── 1. Lock distribuído: Redis SETNX com TTL (ex: 60s) sobre chave "delete:{file\_id}"  
│      Evita exclusão duplicada durante rolling update ou pico de escala  
├── 2. qdrant.delete(filter={"file\_id": file\_id})  
└── 3. db.delete("file\_sync\_state", file\_id=file\_id)

\[Painel\]: pendente → processando → concluído | falhou

### **Tabela de controle de sincronização**

CREATE TABLE file\_sync\_state (  
    file\_id       VARCHAR PRIMARY KEY,  
    teacher\_id    VARCHAR NOT NULL REFERENCES Teachers(id),  
    file\_hash     VARCHAR,  
    last\_modified DATETIME,  
    status              VARCHAR,       \-- 'pendente' | 'processando' | 'concluído' | 'falhou'  
    is\_deleted          BOOLEAN DEFAULT FALSE,  
    error\_message       VARCHAR(500),  \-- truncado; nunca armazena stack traces completos  
    last\_synced         DATETIME,  
    processing\_started\_at DATETIME,    \-- preenchido ao iniciar processamento; usado para detectar travamentos  
    created\_at          DATETIME DEFAULT CURRENT\_TIMESTAMP,  
    updated\_at          DATETIME DEFAULT CURRENT\_TIMESTAMP  
);

**Detecção de status "processando" travado:** Um Timer Trigger (ex: a cada 15 minutos) identifica registros com `status = 'processando'` e `processing_started_at` há mais de 10 minutos, marcando-os como `status = 'falhou'` com `error_message = 'Timeout: processamento excedeu 10 minutos'`. Isso previne arquivos travados indefinidamente após crash do Worker.

### **Tratamento de falhas no Worker de Ingestão**

try:  
    await process\_document(file\_id)  
    await update\_sync\_state(file\_id, status="concluído")  
except Exception as e:  
    \# ⚠️ Sanitizar erro antes de persistir — str(e) pode conter paths internos ou credenciais  
    sanitized\_error \= str(e)\[:500\]  
    await update\_sync\_state(file\_id, status="falhou", error=sanitized\_error)  
    await move\_to\_dead\_letter\_queue(message)

### **Chunking por tipo de conteúdo**

| **Tipo** | **Estratégia** |  
| Texto corrido | Chunking semântico por parágrafo ou seção |  
| Tabela | Tabela inteira como chunk único — nunca dividir |  
| Fórmula | Fórmula \+ parágrafo de contexto adjacente como chunk único |  
| Imagem com legenda | Legenda \+ descrição gerada por Vision LLM como chunk |  
| Imagem sem legenda | Descrição gerada por Vision LLM na ingestão — custo único por arquivo |

### **Metadados obrigatórios por chunk**

{  
  "content": "...",  
  "chunk\_type": "text | table | formula | image",  
  "file\_id": "id\_do\_arquivo\_no\_drive",  
  "lesson\_id": "les\_01",  
  "course\_id": "B1",  
  "page\_number": 7,  
  "section": "Introdução aos verbos modais",  
  "teacher\_id": "sub\_do\_entra\_id"  
}

### **Configuração da Collection no Qdrant (Scalar Quantization)**

Deve ser configurada na criação — não é retroativa sem reindexação completa.  
from qdrant\_client.models import (  
    VectorParams, Distance,  
    ScalarQuantizationConfig, ScalarQuantization, ScalarType  
)

client.create\_collection(  
    collection\_name="lessons",  
    vectors\_config=VectorParams(size=768, distance=Distance.COSINE),  
    quantization\_config=ScalarQuantizationConfig(  
        scalar=ScalarQuantization(  
            type=ScalarType.INT8,  
            quantile=0.99,  
            always\_ram=True  
        )  
    )  
)

Converte vetores de float32 para inteiros de 8 bits — redução de \~75% no uso de RAM. Obrigatório no free tier (1GB RAM).

## **8\. Segurança e Autenticação**

### **Autenticação do professor**

* Azure Static Web Apps com Entra ID (Microsoft/Google login) — sem código OAuth customizado  
* O claim sub do JWT é a identidade imutável do professor no sistema

### **Cache-Aside para whitelist — com role cacheado**

Redis como cache de performance; SQL como fonte de verdade. O cache armazena o papel (role) do usuário, não apenas um bit de presença. Isso permite que o webhook rejeite comandos de alunos diretamente na borda, sem tocar no Service Bus.  
\# Chave: "role:{phone}" → valor: "student" | "teacher" | (ausente \= não autorizado)  
ROLE\_CACHE\_TTL \= 86400  \# 24h

async def get\_user\_role(phone: str) \-\> str | None:  
    """  
    Retorna o papel do usuário ("student" ou "teacher") ou None se não autorizado.  
    Utiliza Cache-Aside: Redis primeiro, SQL como fallback.  
    """  
    \# 1\. Tenta o cache primeiro  
    cached \= await redis.get(f"role:{phone}")  
    if cached:  
        return cached.decode()  \# "student" ou "teacher"

    \# 2\. Cache miss → verifica alunos  
    student \= await db.fetch(  
        "SELECT 1 FROM Students WHERE phone \= :phone AND is\_active \= TRUE",  
        phone=phone  
    )  
    if student:  
        await redis.set(f"role:{phone}", "student", ex=ROLE\_CACHE\_TTL)  
        return "student"

    \# 3\. Verifica professores  
    teacher \= await db.fetch(  
        "SELECT 1 FROM Teachers WHERE phone \= :phone AND is\_active \= TRUE",  
        phone=phone  
    )  
    if teacher:  
        await redis.set(f"role:{phone}", "teacher", ex=ROLE\_CACHE\_TTL)  
        return "teacher"

    return None  \# não autorizado

async def is\_authorized(phone: str) \-\> bool:  
    """Atalho booleano para compatibilidade com o fluxo de whitelist."""  
    return await get\_user\_role(phone) is not None

**Por que armazenar o papel e não só "1"?** Com "1", o webhook sabe que o número é autorizado, mas não sabe se é aluno ou professor. Para rejeitar comandos de alunos na borda (antes de tocar o Service Bus), é necessário conhecer o papel. Armazenar "student" ou "teacher" no Redis não aumenta o custo da operação — apenas muda o valor da string.  
**Uma única leitura Redis por requisição:** get\_user\_role é chamado uma vez no webhook. O resultado é usado tanto para autorização quanto para roteamento de comandos, sem segundo round-trip ao cache.  
**Invalidação do cache ao alterar papel ou status:**  
A operação de invalidação é crítica — se falhar silenciosamente (ex: Redis indisponível), o cache velho persiste com dados incorretos. Deve-se aplicar retry com Tenacity e logar falhas no Application Insights:  
```python
from tenacity import retry, stop_after_attempt, wait_fixed

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2), reraise=True)
async def invalidate_role_cache(phone: str):
    await redis.delete(f"role:{phone}")
```

### **Isolamento de dados por professor**

**Regra de desenvolvimento obrigatória:** toda query SQL e toda busca vetorial deve incluir filtro por `teacher_id` E `course_id`. Query sem esses filtros \= bug de segurança que vaza dados entre cursos do mesmo professor.  
results \= await qdrant.search(  
    collection\_name="lessons",  
    query\_vector=embedding,  
    query\_filter=Filter(  
        must=\[  
            FieldCondition(key="teacher\_id", match=MatchValue(value=sub)),  
            FieldCondition(key="course\_id", match=MatchValue(value=course\_id)),  
            FieldCondition(key="lesson\_id", match=MatchAny(any=allowed\_lesson\_ids))  
        \]  
    )  
)

### **Nota sobre multi-tenancy**

A infraestrutura de isolamento por teacher\_id já constitui a base de um sistema multi-tenant. O que está fora do escopo desta versão é a automação de onboarding e faturamento em modo SaaS — não a separação de dados, que já existe.

## **9\. Módulos de Negócio**

### **9.1 Validação de Usuário**

* Cache-Aside: Redis → SQL. Aluno legítimo nunca bloqueado por falha de cache  
* Cache armazena o papel do usuário ("student" ou "teacher"), não apenas um bit de presença  
* O papel cacheado permite rejeição de comandos de alunos na borda, sem tocar o Service Bus  
* Número não cadastrado → resposta padronizada orientando contato com o professor  
* Cache invalidado imediatamente ao alterar papel ou status do usuário

### **9.2 Scheduler (Triggers por Aluno) — Estratégia de Janela de 24h**

#### **Modelo de Cobrança da Meta (Conversation-Based Pricing)**

A Meta cobra por **conversa** (não por mensagem individual). Existem dois tipos:

| Tipo | Gatilho | Custo (BRL, estimado) | Janela |
|---|---|---|---|
| **Business-Initiated (BIC)** | Empresa envia primeiro (requer Template aprovado) | ~R$ 0,25–0,50 | 24h a partir do envio |
| **User-Initiated (UIC)** | Aluno envia primeiro | ~R$ 0,10–0,15 | 24h a partir da resposta do aluno |
| **Service Window** | Qualquer mensagem dentro da janela aberta | **Gratuito** | Dentro das 24h |

**Regra crítica:** Toda mensagem enviada **dentro** de uma janela de 24h já aberta é gratuita. O objetivo do Scheduler é abrir **uma única janela por aluno/dia**, concentrando toda a interação (quiz, revisão, suporte RAG) dentro dela.

#### **Fluxo "Isca e Exploração"**

```
[Scheduler — Timer Trigger]
        ↓
1. Envia 1 Utility Template por aluno/dia
   (ex: "📚 Sua revisão diária de {course_name} está pronta! Clique para começar.")
   → Custo fixo: ~R$ 0,05 (Utility Template, tarifa reduzida)
        ↓
2. Aluno clica no botão do template
   → Abre janela de serviço de 24h
        ↓
3. TODAS as interações dentro da janela são GRATUITAS:
   - Quiz de micro-learning (Reply Buttons / List Messages)
   - Perguntas ao RAG sobre o conteúdo
   - Feedback de correção
   - Suporte pedagógico
        ↓
4. Janela expira após 24h
   → Próximo ciclo do Scheduler reenvia template no dia seguinte
```

#### **Implementação do Scheduler**

```python
# functions/scheduler/__init__.py
# Azure Function — Timer Trigger a cada 30 minutos
# schedule: "0 */30 * * * *"

import azure.functions as func
from datetime import datetime, timezone

async def main(timer: func.TimerRequest) -> None:
    """Dispara Utility Templates para alunos com agendamento pendente."""
    now = datetime.now(timezone.utc)

    # Busca alunos com horário de trigger <= agora E sem janela ativa
    pending = await db.fetch_all("""
        SELECT s.id, s.phone, s.name, se.course_id, c.name AS course_name,
               ss.preferred_hour, ss.content_type
        FROM StudentSchedule ss
        JOIN Students s ON s.id = ss.student_id
        JOIN StudentEnrollment se ON se.student_id = s.id AND se.course_id = ss.course_id
        JOIN Courses c ON c.id = se.course_id
        WHERE ss.status = 'pending'
          AND ss.next_trigger_at <= :now
          AND s.is_active = TRUE
          AND c.is_active = TRUE
    """, now=now)

    for student in pending:
        # Verifica se já existe janela de 24h aberta (evita template redundante)
        window_key = f"service_window:{student.phone}"
        if await redis.exists(window_key):
            continue  # janela ainda ativa — não gasta template

        # Envia Utility Template aprovado pelo Meta
        template = await get_approved_template(
            teacher_id=student.teacher_id,
            category='UTILITY',
            content_type=student.content_type
        )
        if not template:
            continue

        await send_template_message(
            phone=student.phone,
            template_name=template.template_name,
            language=template.language_code,
            parameters=[student.name, student.course_name]
        )

        # Marca como enviado e agenda próximo trigger
        await db.execute("""
            UPDATE StudentSchedule
            SET status = 'sent', last_sent_at = :now,
                next_trigger_at = :next_trigger
            WHERE student_id = :sid AND course_id = :cid
        """, now=now, next_trigger=compute_next_trigger(student),
             sid=student.id, cid=student.course_id)
```

#### **Rastreamento da Janela de Serviço**

```python
# cache/service_window.py

SERVICE_WINDOW_TTL = 86400  # 24 horas

async def open_service_window(phone: str):
    """Registra abertura da janela de 24h quando o aluno responde ao template."""
    await redis.set(f"service_window:{phone}", "1", ex=SERVICE_WINDOW_TTL)

async def is_window_active(phone: str) -> bool:
    """Verifica se o aluno está dentro da janela de serviço gratuita."""
    return await redis.exists(f"service_window:{phone}")

async def get_window_ttl(phone: str) -> int:
    """Retorna segundos restantes da janela (0 se expirou)."""
    return await redis.ttl(f"service_window:{phone}")
```

**Decisão sobre mensagens fora da janela:** Se o aluno envia mensagem sem janela ativa (janela expirada), o Worker LLM responde normalmente — isso abre uma conversa UIC (~R$0,10). A janela de 24h é aberta automaticamente pela resposta da empresa. O Scheduler não reenvia template se detectar janela UIC já aberta.

#### **Tabela de Agendamento**

```sql
CREATE TABLE StudentSchedule (
    schedule_id     VARCHAR PRIMARY KEY,
    student_id      VARCHAR NOT NULL REFERENCES Students(id),
    course_id       VARCHAR NOT NULL REFERENCES Courses(id),
    preferred_hour  TIME NOT NULL,              -- horário preferido do aluno (BRT)
    content_type    VARCHAR NOT NULL,           -- 'review' | 'preview' | 'quiz' | 'attendance'
    status          VARCHAR DEFAULT 'pending',  -- 'pending' | 'sent' | 'failed'
    last_sent_at    DATETIME,
    next_trigger_at DATETIME NOT NULL,
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (student_id, course_id, content_type)
);

CREATE INDEX idx_schedule_trigger ON StudentSchedule(next_trigger_at, status)
    WHERE is_active = TRUE;
```

#### **Função de envio de Template Message**

```python
# shared/whatsapp_api.py

async def send_template_message(
    phone: str,
    template_name: str,
    language: str = "pt_BR",
    parameters: list[str] | None = None,
) -> dict:
    """Envia um Message Template aprovado pelo Meta via Cloud API.
    
    ⚠️ Templates precisam ser criados e aprovados no Meta Business Manager
    antes de serem utilizados. O campo template_name deve corresponder
    exatamente ao nome registrado no Meta.
    """
    body = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language},
        }
    }
    if parameters:
        body["template"]["components"] = [{
            "type": "body",
            "parameters": [
                {"type": "text", "text": p} for p in parameters
            ]
        }]

    async with httpx.AsyncClient() as client:
        response = await client.post(
            url=f"{WHATSAPP_API_URL}/{PHONE_NUMBER_ID}/messages",
            headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
            json=body,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
```

**Impacto financeiro estimado (50 alunos):**

| Estratégia | Cálculo | Custo mensal estimado |
|---|---|---|
| Sem otimização (BIC avulso) | 50 alunos × 30 dias × R$0,25 = | **R$ 375/mês** |
| Com janela de 24h (Utility Template) | 50 alunos × 30 dias × R$0,05 = | **R$ 75/mês** |
| **Economia** | | **~R$ 300/mês (80%)** |

### **9.3 Webhook Receiver**

O webhook atua exclusivamente como coletor e roteador. Retorna HTTP 200 para o Meta em menos de 200ms. Nunca executa lógica de negócio nem chama o LLM. **O debouncing foi movido para o Worker LLM** — o webhook apenas enfileira e retorna.

#### **Validação de assinatura do Meta (obrigatória)**

Toda requisição do Meta inclui o header `X-Hub-Signature-256` com HMAC-SHA256 do body. Sem essa validação, qualquer atacante que descubra a URL `/v1/webhook` pode injetar mensagens falsas e consumir tokens LLM.

```python
import hmac, hashlib
from uuid import uuid4

APP_SECRET = os.getenv("META_APP_SECRET")  # via Key Vault

async def verify_meta_signature(request: Request) -> bool:
    """Valida HMAC-SHA256 do payload contra o App Secret do Meta."""
    signature = request.headers.get("X-Hub-Signature-256", "")
    body = await request.body()
    expected = "sha256=" + hmac.new(
        APP_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(signature, expected)
```

#### **Rate limiting por telefone**

Proteção contra flood (atacante ou bug no Meta). O Meta já faz rate limiting do seu lado, mas a defesa deve ser em profundidade.

```python
RATE_LIMIT_PER_MINUTE = 60

async def check_rate_limit(phone: str) -> bool:
    """Retorna True se dentro do limite; False se excedeu."""
    rate_key = f"rate:{phone}"
    count = await redis.incr(rate_key)
    if count == 1:
        await redis.expire(rate_key, 60)
    return count <= RATE_LIMIT_PER_MINUTE
```

#### **Handler principal**

```python
async def handle_webhook(request: Request, message):
    try:
        # 0. Validação de assinatura — rejeita requests forjados
        if not await verify_meta_signature(request):
            return Response(status_code=200)  # nunca revelar falha de auth

        # 1. Whitelist + role — Cache-Aside (Redis → SQL)
        user_role = await get_user_role(message.phone)
        if user_role is None:
            return Response(status_code=200)  # número não autorizado — descarta silenciosamente

        # 2. Rate limiting — proteção contra flood
        if not await check_rate_limit(message.phone):
            return Response(status_code=200)  # drop silencioso

        # 3. Filtro de tipo — aceita "text" e "interactive" (Reply Buttons / List Messages).
        #    Mensagens de outros tipos (imagem, áudio, etc.) são rejeitadas.
        #    Payloads interativos contêm button_reply ou list_reply em vez de texto livre.
        if message.type == "interactive":
            # Extrai payload interativo (clique em botão ou seleção de lista)
            interactive = message.interactive
            if interactive.get("type") == "button_reply":
                interactive_payload = {
                    "type": "button_reply",
                    "id": interactive["button_reply"]["id"],
                    "title": interactive["button_reply"]["title"],
                }
            elif interactive.get("type") == "list_reply":
                interactive_payload = {
                    "type": "list_reply",
                    "id": interactive["list_reply"]["id"],
                    "title": interactive["list_reply"]["title"],
                    "description": interactive["list_reply"].get("description", ""),
                }
            else:
                return Response(status_code=200)  # tipo interativo desconhecido
            user_text = interactive_payload["title"]  # fallback textual
            message_type = "interactive"
        elif message.type == "text":
            interactive_payload = None
            user_text = message.text.strip()
            message_type = "text"
        else:
            await send_whatsapp_message(message.phone,
                "No momento, só consigo entender mensagens de texto e botões interativos.")
            return Response(status_code=200)

        # 3.1. Abertura de janela de serviço — registra no Redis quando o aluno
        #      responde (abre janela de 24h para mensagens gratuitas).
        await open_service_window(message.phone)

        # 4. Roteamento de comandos — bifurcação ANTES do RAG.
        #    Mensagens iniciadas com "/" são comandos administrativos.
        #    Alunos que tentarem enviar comandos recebem resposta de erro na borda —
        #    sem tocar no Service Bus, sem acionar nenhum Worker.
        #    ⚠️ Apenas mensagens de texto podem ser comandos (interativas nunca começam com "/").
        if message_type == "text" and user_text.startswith("/"):
            if user_role != "teacher":
                await send_whatsapp_message(message.phone,
                    "Comando não autorizado.")
                return Response(status_code=200)
            await service_bus.enqueue("command_messages", {
                "phone": message.phone,
                "text":  user_text
            })
            return Response(status_code=200)

        # 5. Idempotência — lock por message_id (TTL 5 min)
        lock_key = f"webhook:{message.message_id}"
        if not await redis.set(lock_key, "1", nx=True, ex=300):
            return Response(status_code=200)  # duplicata — descarta silenciosamente

        # 6. Enfileiramento COM SESSION ID e correlation_id para rastreamento
        #    ⚠️ Typing indicator NÃO é enviado aqui — é responsabilidade do Worker,
        #    logo antes de chamar o pipeline RAG, para evitar que o aluno veja
        #    "digitando..." desaparecer durante o debounce (2s) + pipeline (5-15s).
        correlation_id = str(uuid4())
        await service_bus.enqueue(
            queue="inbound_messages",
            payload={
                "phone": message.phone,
                "text": user_text,
                "message_type": message_type,          # "text" ou "interactive"
                "interactive_payload": interactive_payload,  # None para texto puro
                "correlation_id": correlation_id,
                "message_id": message.message_id
            },
            session_id=message.phone
        )

        return Response(status_code=200)

    except Exception as e:
        # Toda exceção é capturada — o Meta NUNCA recebe status != 2xx
        track_event("webhook_error", {
            "phone": message.phone if hasattr(message, 'phone') else "unknown",
            "error": str(e)
        })
        # Responde ao aluno para que ele não fique sem retorno
        try:
            await send_whatsapp_message(message.phone,
                "Ocorreu um erro ao processar sua mensagem, tente novamente.")
        except Exception:
            pass  # falha ao enviar mensagem de erro — logado acima
        return Response(status_code=200)
```

**Por que o debouncing foi removido do webhook?** O design anterior usava `asyncio.sleep(DEBOUNCE_TTL)` para aguardar mensagens adicionais do mesmo usuário antes de enfileirar. Esse sleep dentro do handler bloqueava o retorno HTTP 200, quebrando o SLA de <200ms do Meta. Com o sleep, o WhatsApp interpretava timeout e reenviava a mensagem repetidamente.  
**Novo design:** O debouncing foi movido para o Worker LLM (seção 9.4), que consome a fila inbound_messages com session FIFO (`SessionId=phone`). O Worker aplica a janela de debounce (ex: aguarda 2s após a primeira mensagem, consolida mensagens do buffer Redis, e só então chama o pipeline RAG). Isso preserva a funcionalidade de agrupamento de mensagens sem violar o SLA do webhook.  
**Por que o typing indicator foi removido do webhook?** O typing indicator ("digitando...") do WhatsApp dura ~25s. Como o Worker aplica debounce (2s) + pipeline RAG (5-15s), enviar o indicator no webhook faz o aluno ver "digitando..." desaparecer e reaparecer. Movido para o Worker, logo antes da chamada ao pipeline — timing mais preciso.

### **9.3.1 Command Handler (fila command\_messages)**

Consome a fila command\_messages. Processa exclusivamente comandos administrativos enviados pelo professor via WhatsApp. Nunca aciona o pipeline RAG.  
\# Comandos suportados (extensível)  
COMMAND\_HANDLERS \= {  
    "/aula": handle\_override\_lesson\_pointer,  
}

async def process\_command(event):  
    phone \= event\["phone"\]  
    text  \= event\["text"\]

    \# Identifica o professor pelo número de telefone  
    teacher \= await get\_teacher\_by\_phone(phone)  
    if not teacher:  
        return

    parts   \= text.split()  
    command \= parts\[0\].lower()  \# ex: "/aula"  
    args    \= parts\[1:\]         \# ex: \["@João", "6"\]

    handler \= COMMAND\_HANDLERS.get(command)  
    if handler:  
        response \= await handler(teacher, args)  
    else:  
        response \= f"Comando '{command}' não reconhecido."

    await send\_whatsapp\_message(phone, response)

async def handle\_override\_lesson\_pointer(teacher, args) \-\> str:  
    """  
    Comando: /aula @NomeAluno \<número\_da\_aula\>  
    Exemplos: /aula @João ingles 6  
              /aula Maria Silva 7  (se o aluno tiver apenas um curso ativo)  
    """  
    if len(args) \< 2:  
        return "Uso correto: /aula @NomeAluno \<número\_da\_aula\>  \\n" \\  
               "Se o aluno tiver múltiplos cursos: /aula @NomeAluno \<nome\_curso\> \<número\>"

    \# O último argumento é o número da aula; o resto é o nome do aluno (e opcionalmente o curso)  
    try:  
        lesson\_number \= int(args\[-1\])  
    except ValueError:  
        return f"Número de aula inválido: '{args\[-1\]}'. Uso: /aula Nome Aluno \<número\>"

    \# Junta todos os elementos antes do último como o nome (remove @ inicial se houver)  
    name\_parts \= args\[:-1\]  
    student\_name \= " ".join(name\_parts).lstrip("@")

    student \= await get\_student\_by\_name(teacher.id, student\_name)  
    if not student:  
        return f"Aluno '{student\_name}' não encontrado."

    \# Resolve course\_id: se o aluno tem 1 enrollment → usa direto. Se múltiplos → erro.  
    enrollments \= await get\_enrollments(student.id, teacher.id)  
    if len(enrollments) \== 0:  
        return f"Aluno '{student\_name}' não está matriculado em nenhum curso."  
    if len(enrollments) \> 1:  
        course\_names \= ", ".join(e.course\_id for e in enrollments)  
        return f"Aluno '{student\_name}' está em múltiplos cursos ({course\_names}). " \\  
               f"Use: /aula @{student\_name} \<curso\> \<número\>"

    enrollment \= enrollments\[0\]  
    \# Converte lesson\_number (posição ordinal) para lesson\_id estável via sequencia.json  
    sequence \= await get\_course\_sequence(enrollment.course\_id)  
    if lesson\_number \< 1 or lesson\_number \> len(sequence):  
        return f"Aula {lesson\_number} fora do intervalo. O curso tem {len(sequence)} aulas."  
    lesson\_id \= sequence\[lesson\_number \- 1\]  \# converte posição 1-indexed para lesson\_id

    await update\_enrollment\_pointer(enrollment.enrollment\_id, lesson\_id)  
    return f"✓ {student\_name} atualizado para aula {lesson\_number} (lesson\_id: {lesson\_id})"

**Por que fila separada e não processamento inline no webhook?** O webhook deve retornar HTTP 200 em menos de 200ms. Qualquer consulta ao banco para validar o comando quebraria essa garantia. A fila command\_messages desacopla o roteamento do processamento, mantendo o webhook como coletor puro.

### **9.3.2 Mensagens Interativas (WhatsApp Cloud API)**

O sistema utiliza os componentes nativos de mensagens interativas da WhatsApp Business Cloud API para interfaces estruturadas. Isso substitui a entrada de texto livre por cliques, reduzindo erros de input e melhorando a experiência do aluno.

#### **Reply Buttons (até 3 opções)**

Botões de resposta rápida anexados à mensagem. Ideais para validações binárias ou múltipla escolha simples.

```python
# shared/whatsapp_interactive.py

async def send_reply_buttons(
    phone: str,
    body_text: str,
    buttons: list[dict],  # [{"id": "quiz_123_A", "title": "Opção A"}, ...] (máx 3)
    header: str | None = None,
    footer: str | None = None,
) -> dict:
    """Envia mensagem com até 3 Reply Buttons via WhatsApp Cloud API.
    
    O payload de resposta do aluno (webhook) terá:
    message.interactive.button_reply.id = "quiz_123_A"
    message.interactive.button_reply.title = "Opção A"
    """
    if len(buttons) > 3:
        raise ValueError("Reply Buttons suportam no máximo 3 opções")

    message = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body_text},
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {"id": btn["id"], "title": btn["title"][:20]}  # título máx 20 chars
                    }
                    for btn in buttons
                ]
            }
        }
    }
    if header:
        message["interactive"]["header"] = {"type": "text", "text": header}
    if footer:
        message["interactive"]["footer"] = {"text": footer}

    return await _send_whatsapp_request(message)
```

**Exemplos de uso:**
- Quiz Verdadeiro/Falso: 2 botões (`Verdadeiro`, `Falso`)
- Confirmação de presença: 2 botões (`✅ Confirmo`, `❌ Não posso`)
- Múltipla escolha simples: 3 botões (`A`, `B`, `C`)

#### **List Messages (até 10 opções)**

Menu retrátil com seções e itens selecionáveis. Ideal para múltipla escolha completa (A–E) ou navegação de tópicos.

```python
async def send_list_message(
    phone: str,
    body_text: str,
    button_text: str,  # texto do botão que abre o menu (máx 20 chars)
    sections: list[dict],  # [{"title": "Seção", "rows": [{"id": "A", "title": "...", "description": "..."}]}]
    header: str | None = None,
    footer: str | None = None,
) -> dict:
    """Envia mensagem com menu de lista via WhatsApp Cloud API.
    
    O payload de resposta do aluno (webhook) terá:
    message.interactive.list_reply.id = "quiz_456_B"
    message.interactive.list_reply.title = "Opção B"
    message.interactive.list_reply.description = "Descrição da opção"
    """
    total_rows = sum(len(s.get("rows", [])) for s in sections)
    if total_rows > 10:
        raise ValueError("List Messages suportam no máximo 10 itens")

    message = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body_text},
            "action": {
                "button": button_text[:20],
                "sections": sections
            }
        }
    }
    if header:
        message["interactive"]["header"] = {"type": "text", "text": header}
    if footer:
        message["interactive"]["footer"] = {"text": footer}

    return await _send_whatsapp_request(message)
```

**Exemplos de uso:**
- Quiz completo A–E: 1 seção com 5 rows
- Navegação de tópicos: múltiplas seções (`"Aula 1"`, `"Aula 2"`) com subtópicos em cada
- Seleção de horário preferido: seções `"Manhã"`, `"Tarde"`, `"Noite"` com horários

#### **Função base de envio**

```python
async def _send_whatsapp_request(message: dict) -> dict:
    """Envio centralizado para a WhatsApp Cloud API (Graph API)."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url=f"{WHATSAPP_API_URL}/{PHONE_NUMBER_ID}/messages",
            headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
            json=message,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
```

#### **Decisão: Reply Buttons vs. List Messages**

| Critério | Reply Buttons | List Messages |
|---|---|---|
| Máximo de opções | 3 | 10 |
| Visibilidade | Sempre visíveis | Retrátil (menu) |
| Melhor para | V/F, Sim/Não, A-B-C | A-E, navegação, seleção complexa |
| UX | Mais direto (1 clique) | Requer 2 cliques (abrir + selecionar) |
| Uso no quiz | Perguntas com ≤3 alternativas | Perguntas com 4-5 alternativas |

**Regra do Worker:** ao montar o quiz interativo, o Worker escolhe automaticamente o componente adequado:
- `len(alternatives) <= 3` → Reply Buttons
- `len(alternatives) > 3` → List Message

### **9.4 Worker de Mensagens (pipeline LLM)**

Consome inbound\_messages. Desacoplado do webhook — sem restrição de tempo de resposta HTTP.  
**O debouncing foi movido para cá:** como o Service Bus session (SessionId=phone) garante FIFO estrito, o Worker recebe as mensagens do mesmo aluno em ordem. O debouncing é aplicado no início do processamento: o Worker aguarda uma janela de 2s para consolidar mensagens consecutivas do mesmo aluno antes de chamar o pipeline RAG.

**Constantes de configuração:**  
DEBOUNCE\_TTL \= int(os.getenv("DEBOUNCE\_TTL\_SECONDS", "2"))  \# segundos de espera para consolidar mensagens rápidas  
MAX\_BUFFER\_CHARS \= int(os.getenv("MAX\_BUFFER\_CHARS", "4096"))  \# \~1024 tokens; suficiente para pergunta detalhada

**Debouncing atômico via Lua script (prevenção de race condition):**  
O design anterior usava `redis.lrange` + `redis.delete` em operações separadas, criando uma janela onde mensagens poderiam ser adicionadas ao buffer entre a leitura e a deleção — causando perda silenciosa. A solução é um Lua script que executa atomicamente no Redis:

\# worker/debounce.py  
DEBOUNCE\_DRAIN\_SCRIPT \= """  
local buffer\_key \= KEYS\[1\]  
local lock\_key   \= KEYS\[2\]  
local cooldown   \= ARGV\[1\]

\-\- Lê todas as mensagens do buffer  
local messages \= redis.call('LRANGE', buffer\_key, 0, \-1)

\-\- Deleta o buffer atomicamente  
redis.call('DEL', buffer\_key)

\-\- Redefine o lock de debounce com TTL curto (cooldown) para permitir novas interações  
redis.call('SET', lock\_key, '1', 'EX', cooldown)

return messages  
"""

async def drain\_buffer(redis\_client, phone: str, cooldown: int \= 2) \-\> list\[str\]:  
    """Drena o buffer de debouncing atomicamente via Lua script."""  
    buffer\_key \= f"buffer:{phone}"  
    lock\_key   \= f"debounce:{phone}"  
    raw \= await redis\_client.eval(  
        DEBOUNCE\_DRAIN\_SCRIPT,  
        2,  \# número de KEYS  
        buffer\_key, lock\_key,  
        str(cooldown)  
    )  
    return \[m.decode() for m in raw\] if raw else \[\]

async def process\_inbound(event, receiver, message):  
    phone           \= event\["phone"\]  
    text            \= event\["text"\]  
    message\_type    \= event.get("message\_type", "text")  
    interactive\_payload \= event.get("interactive\_payload")  
    student \= await get\_student\_by\_phone(phone)

    \# Guard clause: aluno pode ter sido desativado entre webhook e processamento  
    if not student:  
        await send\_whatsapp\_message(phone, "Sua conta não está mais ativa. Entre em contato com o professor.")  
        await receiver.complete\_message(message)  
        return

    \# ── Fast-path: resposta interativa de quiz (validação determinística) ──  
    \# Quando o payload interativo contém um ID com prefixo "quiz\_", significa  
    \# que o aluno está respondendo a um quiz previamente gerado. A validação  
    \# é feita por comparação direta no banco — sem chamar o LLM.  
    if message\_type \== "interactive" and interactive\_payload:  
        reply\_id \= interactive\_payload.get("id", "")  
        if reply\_id.startswith("quiz\_"):  
            await handle\_quiz\_response(phone, student, reply\_id, interactive\_payload)  
            await receiver.complete\_message(message)  
            return

    \# ── Fluxo padrão: debouncing + pipeline RAG ──  
    buffer\_key \= f"buffer:{phone}"  
    user\_key   \= f"debounce:{phone}"

    await redis.rpush(buffer\_key, text)  
    await redis.expire(buffer\_key, 10)

    is\_first \= await redis.set(user\_key, "1", nx=True, ex=DEBOUNCE\_TTL)  
    if not is\_first:  
        return  \# outra mensagem do mesmo aluno já está em debounce — esta será consolidada

    await asyncio.sleep(DEBOUNCE\_TTL)  \# seguro aqui: Worker não tem SLA de 200ms

    \# Drena o buffer atomicamente via Lua script — previne race condition  
    messages \= await drain\_buffer(redis, phone)  
    combined \= " ".join(messages)

    if not combined or len(combined) \> MAX\_BUFFER\_CHARS:  
        if combined:  
            await send\_whatsapp\_message(phone,  
                "Sua mensagem é muito longa. Por favor, seja mais conciso.")  
        return

    \# Renovação periódica do session lock durante processamento do pipeline RAG  
    async def renew\_lock\_periodically(rcv, msg, interval\=60):  
        """Renova o lock a cada 'interval' segundos para evitar timeout durante pipeline longo."""  
        while True:  
            await asyncio.sleep(interval)  
            try:  
                await rcv.renew\_message\_lock(msg)  
            except Exception:  
                break  \# lock já expirou ou mensagem já foi completada

    lock\_task \= asyncio.create\_task(renew\_lock\_periodically(receiver, message))

    try:  
        \# Typing indicator enviado aqui (não no webhook) para timing preciso  
        await send\_whatsapp\_typing(phone)  
        response \= await run\_rag\_pipeline(student, combined)  
        await send\_whatsapp\_message(phone, response)  
        await receiver.complete\_message(message)  
    except Exception as e:  
        track\_event("worker\_error", {"error": str(e), "phone": phone})  
        await send\_whatsapp\_message(phone,  
            "Enfrentei um problema técnico para gerar sua resposta. "  
            "Por favor, tente novamente.")  
        \# Move diretamente para DLQ para evitar retentativas e spam ao aluno  
        await receiver.dead\_letter\_message(message, reason="Pipeline Error", error\_description=str(e))  
    finally:  
        lock\_task.cancel()

#### **Validação Determinística de Quiz (fast-path)**

```python
async def handle_quiz_response(
    phone: str,
    student: Student,
    reply_id: str,
    interactive_payload: dict,
) -> None:
    """Valida resposta do aluno a um quiz por comparação direta (if/else).
    
    Fluxo:
    1. Extrai quiz_id e answer_id do reply_id (formato: "quiz_{quiz_id}_{answer_id}")
    2. Busca o quiz no banco
    3. Compara student_answer com correct_answer
    4. Envia explicação pré-gerada (zero tokens LLM consumidos)
    
    Latência total: < 50ms (lookup SQL + comparação + envio WhatsApp)
    """
    # Parse do reply_id: "quiz_{quiz_id}_{answer_id}" → quiz_id, answer_id
    parts = reply_id.split("_", 2)  # ["quiz", quiz_id, answer_id]
    if len(parts) < 3:
        await send_whatsapp_message(phone, "Resposta inválida.")
        return
    
    quiz_id = parts[1]
    answer_id = parts[2]

    # Busca o quiz pendente
    quiz = await db.fetch_one("""
        SELECT quiz_id, correct_answer, explanation, question_text, status
        FROM PendingQuizzes
        WHERE quiz_id = :quiz_id AND student_id = :student_id
    """, quiz_id=quiz_id, student_id=student.id)

    if not quiz:
        await send_whatsapp_message(phone, "Este quiz não foi encontrado ou já expirou.")
        return

    if quiz.status != "pending":
        await send_whatsapp_message(phone, "Você já respondeu este quiz.")
        return

    # ── VALIDAÇÃO DETERMINÍSTICA — if/else puro, sem LLM ──
    is_correct = (answer_id == quiz.correct_answer)

    # Atualiza o registro do quiz
    await db.execute("""
        UPDATE PendingQuizzes
        SET student_answer = :answer, is_correct = :correct,
            status = 'answered', answered_at = CURRENT_TIMESTAMP
        WHERE quiz_id = :quiz_id
    """, answer=answer_id, correct=is_correct, quiz_id=quiz_id)

    # Monta feedback com emoji visual
    if is_correct:
        feedback = f"✅ *Correto!*\n\n{quiz.explanation}"
    else:
        feedback = (
            f"❌ *Incorreto.* A resposta correta era: *{quiz.correct_answer}*\n\n"
            f"{quiz.explanation}"
        )

    await send_whatsapp_message(phone, feedback)

    # Telemetria
    track_event("quiz_answered", {
        "student_id": student.id,
        "quiz_id": quiz_id,
        "is_correct": is_correct,
        "answer": answer_id,
    })
```

### **9.5 Gerenciamento de Sessão: Trimming Nativo no Redis**

A memória da conversa não reside em memória local do Worker. Ela é persistida no Redis para sobreviver a reinicializações e escalonamento horizontal. A implementação padrão do RedisChatMessageHistory do LangChain recupera **todas** as mensagens em memória antes de podá-las. Para prevenir estouro de I/O em rede com alunos muito ativos, é obrigatório interceptar a classe e utilizar a função LTRIM nativa do Redis.  
from langchain.memory import ConversationBufferMemory  
from langchain\_community.chat\_message\_histories import RedisChatMessageHistory  
from langchain\_core.messages import BaseMessage

class OptimizedRedisChatMessageHistory(RedisChatMessageHistory):  
    def add\_message(self, message: BaseMessage) \-\> None:  
        \# Bloqueia chamadas síncronas que ignorariam o LTRIM e preencheriam a memória  
        raise NotImplementedError("Use aadd\_message (async) para garantir LTRIM e não bloquear o event loop.")

    async def aadd\_message(self, message: BaseMessage) \-\> None:  
        """Adiciona a mensagem e roda um LTRIM nativo no Redis para limitar a 40 interações."""  
        await super().aadd\_message(message)  
        \# Corta a lista diretamente no banco: mantém apenas as últimas 40 mensagens  
        await self.redis\_client.ltrim(self.key, \-40, \-1)

def get\_or\_create\_memory(student\_id: str):  
    """  
    Retorna memória de conversa com histórico armazenado no Redis.  
    TTL de 24h para evitar acúmulo indefinido.  
    O gerenciamento de tamanho é feito EXCLUSIVAMENTE pelo LTRIM (40 mensagens).  
    O ConversationTokenBufferMemory NÃO é usado — ele trunca por contagem de tokens  
    em memória, operação redundante e conflitante com o LTRIM.  
    """  
    message\_history \= OptimizedRedisChatMessageHistory(  
        session\_id=student\_id,  
        url=os.getenv("REDIS\_URL"),  
        ttl=86400  \# 24 horas  
    )  
    \# Usar ConversationBufferMemory (sem limite de tokens) — o LTRIM já controla o tamanho  
    return ConversationBufferMemory(  
        chat\_memory=message\_history,  
        return\_messages=True  
    )

### **9.6 Micro-learning — Quiz Engine com Validação Determinística**

O micro-learning utiliza um **quiz engine** que separa a geração (LLM) da validação (determinística). O LLM gera perguntas em formato JSON estruturado durante a fase de geração. A correção da resposta do aluno é feita por comparação direta (`if/else`), sem nova chamada ao LLM.

#### **Fase 1: Geração do Quiz (LLM — custo único)**

```python
# rag/quiz_generator.py
from pydantic import BaseModel, Field
from langchain.output_parsers import PydanticOutputParser

class QuizAlternative(BaseModel):
    id: str = Field(description="Identificador da alternativa (A, B, C, D, E)")
    text: str = Field(description="Texto da alternativa")

class QuizOutput(BaseModel):
    question: str = Field(description="Pergunta clara e direta sobre o conteúdo da aula")
    alternatives: list[QuizAlternative] = Field(description="Lista de alternativas (2 a 5)")
    correct_answer: str = Field(description="ID da alternativa correta")
    explanation: str = Field(description="Explicação pedagógica de por que a resposta está correta")

parser = PydanticOutputParser(pydantic_object=QuizOutput)

QUIZ_GENERATION_PROMPT = """Com base no conteúdo da aula abaixo, gere uma questão de múltipla escolha
para testar o entendimento do aluno.

Conteúdo da aula:
{context}

{format_instructions}

Gere a questão em português, com alternativas claras e uma explicação pedagógica
detalhada para a resposta correta."""

async def generate_quiz(
    student: Student,
    lesson_content: str,
    num_alternatives: int = 4,
) -> QuizOutput:
    """Gera um quiz em JSON estruturado via LLM.
    
    O custo de tokens é ÚNICO: a pergunta, alternativas, resposta correta
    e explicação são gerados em uma única chamada. A validação posterior
    é determinística (zero tokens).
    """
    llm = get_provider().llm_instance()
    prompt = QUIZ_GENERATION_PROMPT.format(
        context=lesson_content,
        format_instructions=parser.get_format_instructions(),
    )
    response = await llm.ainvoke(prompt)
    return parser.parse(response.content)
```

#### **Fase 2: Persistência e Envio Interativo**

```python
# workers/micro_learning.py
from uuid import uuid4
from datetime import datetime, timezone, timedelta

async def send_quiz_to_student(student: Student, quiz: QuizOutput) -> str:
    """Salva o quiz no banco e envia ao aluno via mensagem interativa.
    
    Retorna o quiz_id para rastreamento.
    """
    quiz_id = str(uuid4())[:8]  # ID curto para caber no reply_id do WhatsApp
    
    # Persiste no banco para validação futura
    await db.execute("""
        INSERT INTO PendingQuizzes
            (quiz_id, student_id, course_id, lesson_id, question_text,
             alternatives, correct_answer, explanation, expires_at)
        VALUES
            (:quiz_id, :student_id, :course_id, :lesson_id, :question,
             :alternatives, :correct_answer, :explanation, :expires_at)
    """,
        quiz_id=quiz_id,
        student_id=student.id,
        course_id=student.course_id,
        lesson_id=student.lesson_pointer,
        question=quiz.question,
        alternatives=[{"id": a.id, "text": a.text} for a in quiz.alternatives],
        correct_answer=quiz.correct_answer,
        explanation=quiz.explanation,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )

    # Monta e envia mensagem interativa
    # Regra: ≤3 alternativas → Reply Buttons, >3 → List Message
    if len(quiz.alternatives) <= 3:
        buttons = [
            {"id": f"quiz_{quiz_id}_{alt.id}", "title": f"{alt.id}) {alt.text[:17]}"}
            for alt in quiz.alternatives
        ]
        await send_reply_buttons(
            phone=student.phone,
            body_text=f"📝 *Quiz de Revisão*\n\n{quiz.question}",
            buttons=buttons,
            footer="Clique na resposta correta",
        )
    else:
        rows = [
            {
                "id": f"quiz_{quiz_id}_{alt.id}",
                "title": f"Opção {alt.id}",
                "description": alt.text[:72],  # máximo 72 chars na descrição
            }
            for alt in quiz.alternatives
        ]
        await send_list_message(
            phone=student.phone,
            body_text=f"📝 *Quiz de Revisão*\n\n{quiz.question}",
            button_text="Ver opções",
            sections=[{"title": "Alternativas", "rows": rows}],
            footer="Selecione a resposta correta",
        )

    return quiz_id
```

#### **Fase 3: Validação Determinística (no Worker — seção 9.4)**

Quando o aluno clica em uma opção, o webhook recebe um payload `interactive` com `reply_id` no formato `quiz_{quiz_id}_{answer_id}`. O Worker detecta o prefixo `quiz_` e desvia para o fast-path de validação determinística (função `handle_quiz_response` na seção 9.4), que:

1. Faz lookup do quiz no banco por `quiz_id`
2. Compara `student_answer == correct_answer` (if/else puro)
3. Envia a explicação pré-gerada
4. Registra o resultado no banco e na telemetria

**Sem nova chamada ao LLM.** Latência total: < 50ms.

#### **Fluxo completo do Micro-learning**

```
[Scheduler envia Utility Template → aluno clica → janela de 24h aberta]
        ↓
[Worker de Micro-learning ativado]
        ↓
1. Busca chunks via RAG (filtro: lesson_id ≤ student_pointer + teacher_id + course_id)
2. LLM gera quiz em JSON estruturado (QuizOutput)
3. Salva no PendingQuizzes (status='pending', expires_at=+24h)
4. Envia ao aluno via Reply Buttons ou List Message
        ↓
[Aluno clica na opção — payload interativo retorna ao webhook]
        ↓
5. Webhook enfileira em inbound_messages (message_type="interactive")
6. Worker detecta prefixo "quiz_" → fast-path determinístico
7. Comparação if/else → feedback com explicação pré-gerada
8. Registra resultado (PendingQuizzes + telemetria)
        ↓
[Próxima pergunta enviada após intervalo configurável]
```

#### **Expiração de Quizzes**

Um Timer Trigger (a cada 30 minutos) marca quizzes não respondidos como expirados:

```python
# functions/quiz_expiry/__init__.py
async def main(timer: func.TimerRequest) -> None:
    await db.execute("""
        UPDATE PendingQuizzes
        SET status = 'expired', updated_at = CURRENT_TIMESTAMP
        WHERE status = 'pending' AND expires_at < CURRENT_TIMESTAMP
    """)
```

#### **Configuração por professor**

| Parâmetro | Default | Descrição |
|---|---|---|
| `quiz_per_session` | 3 | Número de perguntas por sessão de micro-learning |
| `quiz_interval_seconds` | 30 | Intervalo entre perguntas (nunca em bloco) |
| `quiz_alternatives` | 4 | Número de alternativas (2–5) |
| `quiz_difficulty` | "medium" | Nível de dificuldade ("easy", "medium", "hard") |

### **9.7 TTS (Text-to-Speech)**

* Habilitado por flag: tts\_enabled: true/false por professor  
* Indicado especialmente para cursos de idiomas  
* Fluxo: texto → Azure TTS → Blob Storage → URL via WhatsApp  
* TTL de 24–48h no Blob

### **9.8 System Prompt — Identidade Pedagógica e Controle de Escopo**

O System Prompt é o contrato de comportamento do assistente. Ele é montado dinamicamente a cada chamada ao LLM — nunca hardcoded — e cobre três responsabilidades: identidade e tom de voz do professor, ancoragem da aula atual (correção do recency bias) e restrição estrita de escopo.

#### **Estrutura do System Prompt**

\# rag/prompt\_builder.py

async def build\_system\_prompt(teacher: Teacher, student: Student) \-\> str:  
    """  
    Monta o System Prompt dinamicamente a partir do perfil do professor  
    e do estado atual do aluno. Chamado uma vez por execução do pipeline RAG.  

    ⚠️ Usamos str.format() em vez de f-string.
    F-string conflitaria com as variáveis de template do LangChain ({context}, {history}, {question}).
    Com str.format(), as chaves duplas {{var}} do LangChain são preservadas como {var} no output.  
    """  
    prompt \= """Você é o assistente pedagógico do professor {teacher_name}, \\  
especializado em {teacher_subject}.

## Identidade e Tom de Voz  
{teacher_persona}

## Aula Atual do Aluno  
O aluno {student_name} está atualmente na **{current_lesson_label}** \\  
do curso {course_name}.  
Ao formular sua resposta, priorize conceitos, exemplos e regras introduzidos \\  
nesta aula ou nas aulas imediatamente anteriores.  
Se o contexto recuperado contiver material de aulas mais antigas que contradiga \\  
ou se sobreponha ao conteúdo atual, utilize o da aula atual como referência \\  
principal.

## Restrição de Escopo — Regra Inegociável  
Você SOMENTE responde perguntas que possam ser respondidas com base no \\  
material das aulas do professor {teacher_name} que foram disponibilizadas \\  
para você como contexto.

Se a pergunta do aluno não puder ser respondida com esse material, \\  
responda EXATAMENTE assim (sem variações):  
"{out_of_scope}"

Exemplos de perguntas FORA do escopo que você deve recusar:  
- Dúvidas sobre outras disciplinas ou assuntos não cobertos pelo curso  
- Pedidos para escrever redações, resolver provas de terceiros ou fazer \\  
  tarefas não relacionadas ao material  
- Perguntas gerais de conhecimento geral (história, ciências, notícias etc.)  
- Qualquer solicitação que não seja sobre o conteúdo das aulas indexadas

## Contexto Recuperado das Aulas  
{{{{context}}}}

## Histórico da Conversa  
{{{{history}}}}

## Pergunta do Aluno  
{{{{question}}}}"""

    \# Resolve lesson\_id para nome legível (ex: "Aula 4 — Verbos Modais")  
    lesson\_label \= await resolve\_lesson\_label(student.course\_id, student.lesson\_pointer)

    return prompt.format(  
        teacher_name=teacher.name,  
        teacher_subject=teacher.subject,  
        teacher_persona=teacher.assistant_persona,  
        student_name=student.name,  
        current_lesson_label=lesson\_label,  
        course_name=student.course_name,  
        out_of_scope=teacher.out_of_scope_message,  
    )

async def resolve\_lesson\_label(course\_id: str, lesson\_id: str) \-\> str:  
    """Converte lesson\_id estável (ex: 'les\_04') para posição ordinal legível."""  
    sequence \= await get\_course\_sequence(course\_id)  
    try:  
        position \= sequence.index(lesson\_id) \+ 1  
        return f"Aula {position} (ID: {lesson\_id})"  
    except ValueError:  
        return f"Aula desconhecida ({lesson\_id})"

#### **Campos configuráveis por professor (armazenados no SQL)**

CREATE TABLE Teachers (  
    id                   VARCHAR PRIMARY KEY,  
    name                 VARCHAR NOT NULL,  
    subject              VARCHAR NOT NULL,       \-- "Inglês B1/B2", "Matemática Básica" etc.  
    phone                VARCHAR UNIQUE,  
    is\_active            BOOLEAN DEFAULT TRUE,

    \-- Campos de personalização do assistente  
    assistant\_persona    TEXT NOT NULL,          \-- tom de voz, estilo de resposta  
    out\_of\_scope\_message TEXT NOT NULL,          \-- mensagem exibida ao aluno quando fora do escopo

    created\_at           DATETIME DEFAULT CURRENT\_TIMESTAMP,  
    updated\_at           DATETIME DEFAULT CURRENT\_TIMESTAMP  
);

#### **Integração com LangChain**

\# rag/pipeline.py  
from langchain.prompts import ChatPromptTemplate, SystemMessagePromptTemplate  
from langchain.chains import ConversationalRetrievalChain

async def run\_rag\_pipeline(student: Student, question: str) \-\> str:  
    teacher  \= await get\_teacher(student.teacher\_id)  
    system\_p \= await build\_system\_prompt(teacher, student)

    prompt \= ChatPromptTemplate.from\_messages(\[  
        SystemMessagePromptTemplate.from\_template(system\_p),  
        \# O histórico e a pergunta são injetados pelo LangChain via {history} e {question}  
    \])

    chain \= ConversationalRetrievalChain.from\_llm(  
        llm=get\_provider().llm\_instance(),  
        retriever=build\_retriever(student),  \# filtro teacher\_id \+ course\_id \+ lesson\_ids até o ponteiro  
        memory=get\_or\_create\_memory(student.id),  
        combine\_docs\_chain\_kwargs={"prompt": prompt},  
    )

    result \= await chain.ainvoke({"question": question})  
    return result\["answer"\]

**Posicionamento da âncora de aula:** a instrução de prioridade de aula está no System Prompt, antes do contexto recuperado. LLMs respeitam mais instruções posicionadas no início do prompt do que as injetadas no meio do contexto.

### **9.9 Ciclo de Vida da Dead Letter Queue (DLQ)**

O Azure Service Bus não possui rotina nativa de auto-limpeza da DLQ. Mensagens envenenadas acumulam-se indefinidamente, consomem cota de armazenamento e ocultam erros novos.  
**Correção — Timer Trigger DIÁRIO de inspeção e expurgo com configuração segura:**  
\# functions/dlq\_janitor/\_\_init\_\_.py  
\# Azure Function com Timer Trigger — executa TODOS OS DIAS às 04:00 BRT (07:00 UTC)  
\# schedule: "0 0 7 \* \* \*"  
\# functionTimeout configurado para 10 minutos em host.json
\# ⚠️ Frequência semanal era insuficiente: um pico de erros (ex: 200 mensagens em um dia)  
\#    com MAX\_MESSAGES\_PER\_RUN=50 levaria 4 semanas para ser totalmente expurgado.

import azure.functions as func  
from azure.servicebus.aio import ServiceBusClient  
from azure.identity.aio import DefaultAzureCredential  
from applicationinsights import TelemetryClient

QUEUES\_WITH\_DLQ \= \["inbound\_messages", "index\_documents", "command\_messages", "delete\_vectors"\]  
MAX\_MESSAGES\_PER\_RUN \= 50  \# valor seguro para execução \< 5 minutos

async def main(timer: func.TimerRequest) \-\> None:  
    tc \= TelemetryClient(os.getenv("APPINSIGHTS\_INSTRUMENTATIONKEY"))

    credential \= DefaultAzureCredential()  
    async with ServiceBusClient(  
        fully\_qualified\_namespace=os.getenv("SERVICEBUS\_FQDN"),  
        credential=credential,  
    ) as client:

        for queue\_name in QUEUES\_WITH\_DLQ:  
            dlq\_path \= f"{queue\_name}/$deadletterqueue"  
            dead\_messages \= \[\]

            async with client.get\_queue\_receiver(  
                queue\_name=queue\_name,  
                sub\_queue="deadletter",  
                max\_wait\_time=5,  
            ) as receiver:  
                async for msg in receiver.receive\_messages(  
                    max\_message\_count=MAX\_MESSAGES\_PER\_RUN,  
                    max\_wait\_time=10,  
                ):  
                    dead\_messages.append({  
                        "queue":        queue\_name,  
                        "message\_id":   str(msg.message\_id),  
                        "enqueued\_at":  str(msg.enqueued\_time\_utc),  
                        "dead\_reason":  msg.dead\_letter\_reason,  
                        "dead\_desc":    msg.dead\_letter\_error\_description,  
                        \# ⚠️ NÃO logar msg.body — pode conter dados pessoais do aluno (LGPD)  
                    })  
                    tc.track\_event("dlq\_message\_found", dead\_messages\[-1\])

                    \# Expurgo: completa (remove) a mensagem da DLQ após logar  
                    await receiver.complete\_message(msg)

            tc.track\_metric(f"dlq\_purged\_{queue\_name}", len(dead\_messages))  
            tc.track\_event("dlq\_purge\_summary", {  
                "queue":   queue\_name,  
                "purged":  len(dead\_messages),  
                "run\_utc": str(datetime.now(timezone.utc)),  
            })

    tc.flush()

**Por que complete\_message e não dead\_letter ou abandon?** complete\_message remove a mensagem fisicamente da DLQ. abandon a reenfileira para uma nova tentativa de processamento — inadequado para mensagens que já falharam repetidamente. O Janitor não tenta reprocessar: ele registra e expurga.

## **10\. Checklist de Implementação**

Referência rápida dos pontos críticos documentados ao longo das seções anteriores. Cada item indica a seção de referência para detalhes.

### Infraestrutura e Mensageria
- [ ] Service Bus tier **Standard** obrigatório para sessions FIFO — seção 2
- [ ] `requiresSession=true` + `LockDuration=PT5M` na fila `inbound_messages` — seção 2
- [ ] KEDA com Managed Identity (`keda-mi-auth`), nunca connection strings — seção 7
- [ ] `DefaultAzureCredential` em todo código Python — seção 7
- [ ] Webhook: `minReplicas=1` (cold start proibido) — seção 2
- [ ] Workers: escalam a zero via KEDA — seção 2
- [ ] Separação de ambientes (dev, staging, prod) via Container Apps environments

### Webhook (seção 9.3)
- [ ] Validação de assinatura HMAC-SHA256 (`X-Hub-Signature-256`) — seção 9.3
- [ ] Rate limiting por telefone (60 req/min) — seção 9.3
- [ ] Debouncing **NÃO** ocorre no webhook — apenas enfileira e retorna 200
- [ ] `try/except` global — Meta nunca recebe status != 2xx
- [ ] Aceitar `message.type == "interactive"` (Reply Buttons / List Messages) além de `"text"` — seção 9.3
- [ ] Extrair `button_reply.id` / `list_reply.id` de payloads interativos — seção 9.3
- [ ] Propagar `message_type` e `interactive_payload` no enfileiramento — seção 9.3
- [ ] Abertura de janela de serviço (`open_service_window`) a cada resposta do aluno — seção 9.2
- [ ] Roteamento de `/comandos` antes do RAG; alunos bloqueados na borda
- [ ] Endpoint versionado: `/v1/webhook`
- [ ] Typing indicator enviado no **Worker** (não no webhook) — timing preciso
- [ ] `correlation_id` (UUID v4) gerado e propagado no payload — seção 16

### Worker LLM (seção 9.4)
- [ ] Guard clause: verificar `student is not None` antes de processar
- [ ] Debouncing atômico via Lua script (previne race condition) — seção 9.4
- [ ] Renovação periódica do session lock via background task — seção 9.4
- [ ] `complete_message` no sucesso, `dead_letter_message` no erro — seção 9.4
- [ ] Resolução de `course_id`: enrollment único → automático; múltiplos → erro orientativo
- [ ] Worker sempre retorna feedback ao aluno (sucesso ou erro explícito)
- [ ] `DEBOUNCE_TTL=2s` e `MAX_BUFFER_CHARS=4096` configuráveis via env — seção 9.4

### Cache e Autenticação (seção 8)
- [ ] Cache-Aside: Redis → SQL. Cache miss cai no SQL — aluno legítimo nunca bloqueado
- [ ] Chave `role:{phone}` retorna `"student"` ou `"teacher"` (não só bit de presença)
- [ ] Invalidação de cache com Tenacity (3 tentativas + retry)

### Modelo de Dados (seção 6)
- [ ] Tabela `Courses` com id, teacher\_id, name, is\_active — seção 6
- [ ] Tabela `Students` com id, name, phone, is\_active — seção 6
- [ ] Tabela `PendingQuizzes` para quizzes determinísticos — seção 6
- [ ] Tabela `MessageTemplates` para controle de templates do Meta — seção 6
- [ ] Tabela `StudentSchedule` para agendamento com janela de 24h — seção 9.2
- [ ] Foreign Keys em todas as tabelas (StudentEnrollment, ScheduledClasses, Attendance)
- [ ] Ponteiro por matrícula (N:N): `StudentEnrollment(student_id, course_id, lesson_id)`
- [ ] `lesson_id` estável (não índice posicional) — seção 6
- [ ] Índices explícitos: `file_sync_state(teacher_id, status)`, `ScheduledClasses(course_id, scheduled_date)`
- [ ] Override via `/aula` resolve ordinal → `lesson_id` via `sequencia.json` — seção 9.3.1
- [ ] `created_at` e `updated_at` em todas as tabelas

### Pipeline RAG e Ingestão (seção 7)
- [ ] Controle de concorrência: `max_concurrent_calls=2` + `maxReplicas=2` — seção 7
- [ ] Tenacity com exponential backoff em todas as APIs externas
- [ ] Hard delete agendado com `ScheduledEnqueueTimeUtc` + lock Redis — seção 7
- [ ] `processing_started_at` para detectar status "processando" travado — seção 7
- [ ] Scalar Quantization na criação da collection (não retroativa) — seção 7
- [ ] Filtro de isolamento: `teacher_id + course_id` em toda query — seção 8

### System Prompt e Memória (seções 9.5, 9.8)
- [ ] Âncora de aula: `lesson_id` resolvido para nome legível no System Prompt
- [ ] `str.format()`, não f-string (preserva variáveis do LangChain)
- [ ] Prompt reconstruído a cada chamada (sem cache)
- [ ] LTRIM nativo no Redis (40 mensagens) via `OptimizedRedisChatMessageHistory`
- [ ] `aadd_message` (async) para não bloquear event loop — seção 9.5
- [ ] `ConversationBufferMemory` — nunca `ConversationTokenBufferMemory`

### Observabilidade e LGPD
- [ ] Controle de custo de tokens com alertas no Azure Cost Management
- [ ] DLQ monitorada diariamente (4 filas incluindo `delete_vectors`) — seção 9.9
- [ ] **DLQ NÃO loga body da mensagem** (LGPD) — apenas metadados
- [ ] Health check do Qdrant Free Tier a cada 6 dias
- [ ] Correlation ID propagado do webhook ao Worker via payload do Service Bus
- [ ] Logging JSON estruturado com dimensões obrigatórias: `teacher_id`, `student_id`, `course_id`
- [ ] `error_message` sanitizado antes de persistir no SQL (truncar 500 chars)

### Backup e Recuperação
- [ ] SQL: backup automático Azure (7–35 dias)
- [ ] Redis: persistência AOF (`appendonly yes`)
- [ ] Qdrant free tier: reindexação a partir dos PDFs como plano de recuperação
- [ ] Service Account do Google Drive: verificação semanal de acesso

### Mensagens Interativas (seção 9.3.2)
- [ ] `send_reply_buttons()` para quizzes com ≤3 alternativas
- [ ] `send_list_message()` para quizzes com 4-5 alternativas
- [ ] `_send_whatsapp_request()` centralizado para envio Cloud API
- [ ] Seleção automática de componente baseada em `len(alternatives)`
- [ ] Validação de limites: Reply Buttons máx 3, List Messages máx 10
- [ ] Título de botão truncado em 20 chars, descrição de lista em 72 chars

### Quiz Determinístico (seções 9.4, 9.6)
- [ ] `QuizOutput` (Pydantic) com `PydanticOutputParser` do LangChain — seção 9.6
- [ ] Quiz gerado em JSON estruturado: pergunta + alternativas + resposta correta + explicação — seção 9.6
- [ ] Quiz salvo no `PendingQuizzes` com `status='pending'` e `expires_at` — seção 9.6
- [ ] Fast-path no Worker: payload com prefixo `quiz_` → validação determinística (if/else) — seção 9.4
- [ ] `handle_quiz_response()` sem chamada ao LLM — latência < 50ms — seção 9.4
- [ ] Timer Trigger para expirar quizzes não respondidos — seção 9.6
- [ ] Telemetria: `quiz_answered` com `student_id`, `quiz_id`, `is_correct` — seção 9.4

### Engenharia Financeira — WhatsApp Pricing (seção 9.2)
- [ ] Scheduler envia **1 Utility Template** por aluno/dia como isca — seção 9.2
- [ ] Rastreamento de janela de 24h via Redis (`service_window:{phone}`) — seção 9.2
- [ ] `send_template_message()` para envio de templates aprovados pelo Meta — seção 9.2
- [ ] Templates criados e aprovados no Meta Business Manager antes do deploy
- [ ] Tabela `MessageTemplates` para controle de templates e status de aprovação — seção 6
- [ ] Webhook registra abertura de janela (`open_service_window`) — seção 9.3
- [ ] Estimativa de custo WhatsApp incluída na seção 15

## **11\. Fora do Escopo (Versões Futuras)**

* Onboarding e faturamento SaaS automatizados — infraestrutura multi-tenant já existe via teacher\_id  
* Analytics pedagógico avançado  
* Integração com calendário do professor  
* Input de voz do aluno (Speech-to-Text)  
* Interpretação de imagens enviadas pelo aluno (Vision)  
* Perguntas genéricas ao LLM — o aluno tem ChatGPT, Gemini e Claude para isso

## **12\. Stack Resumida**

| **Camada** | **Tecnologia** |  
| Linguagem | Python (async em todo o projeto) |  
| Framework API | FastAPI (ASGI) |  
| Infraestrutura | Azure (Container Apps \+ KEDA via **Managed Identity**, Functions, Service Bus \+ DLQ, SQL DTU Basic, Redis, Blob, Key Vault) |  
| Autenticação | Microsoft Entra ID via Azure Static Web Apps |  
| Parse de documentos | Azure Document Intelligence |  
| Orquestração RAG \+ Memória | LangChain (ConversationalRetrievalChain \+ ConversationBufferMemory com OptimizedRedisChatMessageHistory) |  
| Banco vetorial | Qdrant Cloud (com Scalar Quantization INT8) |  
| LLM (inicial) | Gemini |  
| LLM (planejado) | DeepSeek |  
| Vision LLM (ingestão) | Gemini Vision ou GPT-4o |  
| Embedding | multilingual-e5 ou text-embedding-3-small (interface EmbeddingProvider separada) |  
| TTS | Azure Cognitive Services Speech |  
| Cache, locks, whitelist/role, debouncing, janela de serviço e histórico de chat | Azure Cache for Redis (redis.asyncio, persistência AOF) |  
| Repositório de conteúdo | Google Drive (Service Account compartilhada manualmente) |  
| Mensageria | Azure Service Bus **Standard** (com requiresSession=true, lock duration PT5M na fila de inbound) |  
| Mensagens interativas | WhatsApp Cloud API (Reply Buttons até 3 opções, List Messages até 10 opções) |  
| Quiz Engine | Geração JSON estruturada (LLM \+ PydanticOutputParser) com validação determinística (if/else, zero LLM na correção) |  
| WhatsApp Pricing | Utility Templates \+ janela de serviço de 24h (economia de \~80% vs. BIC avulso) |  
| Retry e resiliência | Tenacity (exponential backoff nas APIs externas + invalidação de cache) |  
| Manutenção de DLQ | Azure Function Timer Trigger diário (timeout 10 min, limite de 50 mensagens) |  
| Autenticação Azure | DefaultAzureCredential (Managed Identity) — zero connection strings no código |

## **13\. Estratégia de Testes**

| **Camada** | **Ferramenta** | **Cobertura esperada** |  
| Testes unitários | pytest + pytest-asyncio | Cobre regras de negócio (Cache-Aside, diff de arquivos, parser de comandos, build de System Prompt, debouncing). Mock de I/O externo. |  
| Testes de integração | pytest + Qdrant Docker + Azurite (Service Bus local) | Fluxo completo de ingestão e busca vetorial. Conectividade Redis local. |  
| Testes E2E | pytest + WhatsApp Cloud API Sandbox | Webhook → Worker → resposta ao aluno. Validar timeout de 200ms. |  
| Testes de carga | Locust | Simular N alunos simultâneos. Validar max\_concurrent\_calls e maxReplicas. |  

## **14\. CI/CD e Infraestrutura como Código**

| **Etapa** | **Ferramenta** |  
| CI | GitHub Actions — lint (ruff), type check (mypy), tests (pytest) em cada PR |  
| CD | GitHub Actions + Azure CLI — deploy nos Container Apps via `az containerapp up` |  
| IaC | Bicep ou Terraform — provisioning de todos os recursos Azure com RBAC de Managed Identity |  
| Secrets | Azure Key Vault — zero secrets em repositório ou variáveis de ambiente plaintext |  

## **15\. Estimativa de Custos (Produção — 1 professor, ~50 alunos)**

| **Recurso** | **Custo mensal estimado** |  
| Azure Container Apps (webhook minReplicas=1) | ~US$ 15–25 (1 vCPU, 2GB RAM 24/7) |  
| Azure Container Apps (workers, escala a zero) | ~US$ 5–15 (consumo sob demanda) |  
| Azure SQL Database (DTU Basic) | ~US$ 5 |  
| Azure Cache for Redis (Basic C0) | ~US$ 20 |  
| Azure Service Bus (Standard) | ~US$ 10/mês (tier Standard obrigatório para sessions FIFO) |  
| Qdrant Cloud (free tier) | US$ 0 |  
| Azure Functions (Timer Triggers) | ~US$ 0 (1M execuções grátis/mês) |  
| Azure Document Intelligence | ~US$ 1.50/1000 páginas — custo único na ingestão |  
| Azure Cognitive Services (TTS) | ~US$ 1/1M caracteres |  
| Azure Blob Storage | ~US$ 2 (áudio com TTL 48h) |  
| Azure Key Vault | ~US$ 0.50 |  
| Application Insights | ~US$ 5 |  
| **WhatsApp Cloud API (Utility Templates)** | **~R$ 75/mês** (~US$ 15) — 50 alunos × 30 dias × R$ 0,05/template. Com estratégia de janela de 24h (seção 9.2). Sem otimização seria ~R$ 375/mês. |  
| **LLM (Gemini)** | **⚠️ Maior variável** — estime ~100 tokens/resposta × 1000 interações/mês/aluno × 50 alunos = 5M tokens. Custo: ~US$ 8–40/mês dependendo do modelo. **Monitorar desde o dia 1 com alerta de custo.** Quiz determinístico reduz tokens: validação de resposta não consome tokens LLM. |  
| **Total estimado** | **~US$ 85–150/mês**, dominado pelo Container App do webhook (minReplicas=1), Redis, custo do LLM e WhatsApp messaging. |

**⚠️ Nota de otimização de custo do Redis:** Para ambientes de baixo volume (1 professor, ~50 alunos), considerar alternativas ao Azure Cache for Redis Basic C0 (~US$ 20/mês): Upstash Redis Serverless (~US$ 0 para esse volume), Redis em Docker sidecar no Container App (dev/staging), ou Azure Redis Consumption tier (preview, ~US$ 6/mês se disponível na região).  
**⚠️ Nota sobre custo do WhatsApp:** Os valores da Meta variam por país e categoria de template. A estimativa assume Utility Templates no Brasil (tarifa reduzida). Consultar a [tabela de preços oficial](https://developers.facebook.com/docs/whatsapp/pricing/) antes de ir para produção. O custo de mensagens UIC (aluno inicia) é menor (~R$ 0,10) e já está incluído na margem.

## **16\. Observabilidade e Rastreamento**

### Correlation ID

Cada requisição do webhook gera um `correlation_id` (UUID v4) que é propagado no payload do Service Bus até o Worker. Isso permite rastrear o ciclo completo de uma mensagem:

```
Webhook (gera correlation_id) → Service Bus (no payload) → Worker (injeta no logger)
```

Todos os `track_event` e logs devem incluir o `correlation_id` como dimensão.

### Logging Estruturado

- Formato: JSON (via `structlog` ou `python-json-logger`)
- Dimensões obrigatórias: `correlation_id`, `teacher_id`, `student_id`, `course_id`, `operation`
- Output: Application Insights via OpenTelemetry SDK

### Dashboards Base

| **Métrica** | **Alerta** |
| Latência do webhook (p95) | \> 150ms |
| Taxa de HTTP 429 nas APIs externas | \> 0 por 10 min |
| Tamanho da DLQ | \> 10 mensagens |
| Custo diário de tokens LLM | Acima do budget configurado |
| DTU usage do SQL | \> 70% por 5 min |
| Erros do Worker (taxa) | \> 5% das mensagens processadas |
```

<!-- FILE_PATH: Makefile -->
### 📄 `Makefile`

```makefile
# ==========================================
# LOCAL OPERATIONS — Infrastructure & CI/CD
# ==========================================

ENV = dev
PYTHONPATH_VAL = .

.PHONY: help dev dev-api dev-worker test test-integration test-all

help:
	@echo ""
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo "  WhatsApp Teacher Assistant — Commands"
	@echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
	@echo ""
	@echo "  🔧 LOCAL DEVELOPMENT"
	@echo "  make dev                 - Start infrastructure (docker-compose)"
	@echo "  make dev-api             - Start FastAPI server locally"
	@echo "  make dev-worker          - Start Celery/ServiceBus worker locally"
	@echo ""
	@echo "  🧪 TESTING"
	@echo "  make test                - Run unit tests"
	@echo "  make test-integration    - Run integration tests"
	@echo "  make test-all            - Run all tests with coverage"
	@echo ""

# ==========================================
# 1. LOCAL DEVELOPMENT
# ==========================================
dev:
	@echo "[INFO] Starting local infrastructure with docker-compose..."
	docker-compose up -d
	@echo "[INFO] Waiting 60s for Service Bus Emulator to initialize internal SQL..."
	sleep 60
	@echo "[INFO] Stack running: Redis, Azurite (Service Bus Emulator), Qdrant"

dev-api:
	@echo "[INFO] Starting FastAPI webhook server..."
	export PYTHONPATH=$(PYTHONPATH_VAL) && \
	export ENVIRONMENT=$(ENV) && \
	.venv/bin/uvicorn src.webhook.main:app --reload --host 0.0.0.0 --port 8000

dev-worker:
	@echo "[INFO] Starting LLM Worker..."
	export PYTHONPATH=$(PYTHONPATH_VAL) && \
	export ENVIRONMENT=$(ENV) && \
	.venv/bin/python -m src.workers.llm_worker

dev-worker-ingestion:
	@echo "[INFO] Starting Ingestion Worker (Google Drive -> Qdrant)..."
	export PYTHONPATH=$(PYTHONPATH_VAL) && \
	export ENVIRONMENT=$(ENV) && \
	.venv/bin/python -m src.workers.ingestion_worker

# ==========================================
# 2. TESTING
# ==========================================
test:
	@echo "[INFO] Running unit tests..."
	export PYTHONPATH=$(PYTHONPATH_VAL) && \
	pytest tests/unit/ -v

test-integration:
	@echo "[INFO] Running integration tests..."
	export PYTHONPATH=$(PYTHONPATH_VAL) && \
	pytest tests/integration/ -v

test-all:
	@echo "[INFO] Running full test suite with coverage..."
	export PYTHONPATH=$(PYTHONPATH_VAL) && \
	pytest tests/ -v --cov=src --cov-report=term-missing

```

<!-- FILE_PATH: Resumo Arquitetural - Assistente WhatsApp.md -->
### 📄 `Resumo Arquitetural - Assistente WhatsApp.md`

```markdown
# **Resumo Arquitetural: Assistente WhatsApp para Professores (RAG \+ MLOps)**

## **1\. Objetivo do Projeto**

Desenvolver um assistente virtual assíncrono via WhatsApp Business API focado na **gestão de atenção pedagógica**. O sistema atua como um tutor restrito: responde dúvidas de alunos utilizando estritamente o material fornecido pelo professor via RAG (Retrieval-Augmented Generation) e dispara trilhas de micro-learning baseadas no avanço individual do aluno. O projeto rejeita explicitamente atuar como um oráculo genérico (ChatGPT/Claude).

## **2\. Padrão Arquitetural**

A infraestrutura adota um modelo **orientado a eventos e assíncrono**, projetado para suportar os limites de tempo da API do Meta, evitar *cold starts* em rotinas de banco de dados e proteger as cotas de consumo de APIs externas.

* **Borda (Edge):** Webhook construído em FastAPI que opera exclusivamente como coletor, validador de segurança e roteador. O tempo de resposta garantido é inferior a 200ms.  
* **Mensageria:** Azure Service Bus como espinha dorsal, separando fluxos de chat, processamento de documentos e comandos administrativos.  
* **Processamento (Workers):** Contêineres que consomem as filas do Service Bus em *background* para execução do pipeline LLM e ingestão de dados.

## **3\. Decisões Críticas de Engenharia e Mitigação de Riscos**

Para avaliar a maturidade do desenho, estas foram as falhas de sistemas distribuídos contornadas até o momento:

### **A. Resiliência e Segurança do Webhook (Autenticação, Concorrência e Idempotência)**

O WhatsApp pode disparar webhooks duplicados (instabilidade de rede) ou múltiplos webhooks simultâneos (aluno enviando mensagens fragmentadas). Além disso, qualquer atacante que descubra a URL do webhook pode injetar mensagens falsas, consumindo tokens LLM.

* **Solução:** Três camadas de proteção: (1) Validação de assinatura HMAC-SHA256 via `X-Hub-Signature-256` rejeita requests forjados. (2) *Distributed Lock* no Redis via message\_id garante idempotência. (3) Rate limiting por telefone (60 req/min) previne flood. O *Debouncing* é assíncrono no Worker LLM (buffer Redis antes de processar). O webhook apenas valida, enfileira e retorna HTTP 200.

### **B. Proteção de Ingestão (Rate Limits)**

Sincronizar uma pasta do Google Drive com 30 PDFs simultaneamente causaria estrangulamento (*HTTP 429*) nas APIs do Azure Document Intelligence e do modelo de embedding.

* **Solução:** O *Worker* de Ingestão possui restrição nativa de concorrência (max\_concurrent\_calls) acoplada à biblioteca Tenacity, que aplica *retry* com *exponential backoff* em falhas de comunicação com as APIs externas.

### **C. Ciclo de Vida de Vetores e Performance do Índice**

Excluir dezenas de documentos do banco vetorial durante o horário comercial força o índice HNSW a se reconstruir, degradando a latência das buscas do RAG de todos os alunos ativos.

* **Solução:** O arquivo removido recebe *soft delete* no SQL instantaneamente, mas o *hard delete* no Qdrant é enviado para o Service Bus utilizando a propriedade ScheduledEnqueueTimeUtc. A mensagem só é entregue às 03:00 da manhã para o *Worker* de Exclusão.

### **D. Roteamento de Segurança na Borda (Role-Blindness)**

Processar comandos administrativos (ex: /aula @Joao 6\) dentro do *Worker* de LLM consome tokens e recursos desnecessários se o emissor for um aluno mal-intencionado.

* **Solução:** O cache da Whitelist no Redis (padrão *Cache-Aside* ancorado no SQL) armazena o role (student/teacher). O webhook usa essa flag para bloquear comandos de alunos na borda, antes de acionar o Service Bus.

### **E. Qualidade do RAG e Prevenção de Recency Bias**

Quando um aluno chega à Aula 50, a base vetorial abrange todo o curso. O modelo pode recuperar informações de aulas antigas que usam premissas defasadas para o nível atual do aluno (*Lost in the Middle*).

* **Solução:** Cada aluno possui um lesson\_pointer (ponteiro de aula atual). Esse ponteiro é injetado dinamicamente no *System Prompt* ("O aluno está na Aula X, priorize este contexto") e usado como filtro lógico de metadados na busca do Qdrant (lesson\_number \<= student\_pointer).

### **F. Gerenciamento de Memória Transacional**

Utilizar as últimas "N" mensagens para histórico da LLM pode estourar a janela de contexto se o aluno enviar textos massivos.

* **Solução:** Persistência do histórico no Redis via `OptimizedRedisChatMessageHistory`, que intercepta o `add_message` do LangChain e aplica `LTRIM` nativo no Redis para manter apenas as últimas 40 mensagens. A poda é feita diretamente no banco (sem carregar tudo em memória), utilizando `ConversationBufferMemory` como wrapper. O `ConversationTokenBufferMemory` não é usado — trunca por tokens em memória, operação redundante e conflitante com o LTRIM nativo.

### **G. Mensagens Interativas (Padrão de Mercado)**

A entrada de texto livre para quizzes e navegação é propensa a erros de parsing e degrada a experiência do usuário.

* **Solução:** O sistema utiliza os componentes nativos de mensagens interativas da WhatsApp Cloud API: **Reply Buttons** (até 3 opções) para validações rápidas (V/F, Sim/Não) e **List Messages** (até 10 opções) para múltipla escolha completa (A–E). O webhook aceita payloads do tipo `interactive` além de `text`, extraindo `button_reply.id` ou `list_reply.id` automaticamente.

### **H. Validação Determinística de Quizzes (Economia de Tokens)**

Corrigir respostas de quizzes via LLM consome tokens desnecessariamente, adiciona latência (2–5s) e introduz risco de alucinação na correção.

* **Solução:** O LLM gera perguntas em **JSON estruturado** (pergunta, alternativas, resposta correta, explicação pedagógica) via `PydanticOutputParser`. O quiz é salvo no banco (`PendingQuizzes`). Quando o aluno clica em uma opção, o Worker faz comparação direta (`if/else`) sem chamar o LLM. Latência de correção: < 50ms. Zero tokens consumidos na validação.

### **I. Engenharia Financeira — Janela de Serviço de 24h**

Sem otimização, cada mensagem proativa (quiz, revisão) abre uma conversa BIC (~R$ 0,25–0,50), escalando rapidamente o custo com o número de alunos.

* **Solução:** O Scheduler envia uma única **Utility Template** por aluno/dia (~R$ 0,05) como "isca". Quando o aluno responde, abre-se uma janela de serviço de 24h durante a qual todas as mensagens são gratuitas (quiz, RAG, feedback). Economia de ~80% vs. envio avulso. O estado da janela é rastreado via Redis (`service_window:{phone}`, TTL 24h).

## **4\. Stack Tecnológica Base**

| **Camada** | **Tecnologia Selecionada** |  
| **Computação** | Azure Container Apps (Workers) e Azure Functions (Timer Triggers, DLQ Janitor). |  
| **Banco Relacional** | Azure SQL Database (Camada DTU Basic \- para evitar a armadilha de custos do *auto-pause* do Serverless). |  
| **Banco Vetorial** | Qdrant Cloud (com *Scalar Quantization* configurada para otimização obrigatória de RAM no Free Tier). |  
| **Cache e Locks** | Azure Cache for Redis (redis.asyncio). Inclui rastreamento de janela de serviço 24h. |  
| **Mensageria** | Azure Service Bus Standard (tier Standard obrigatório para sessions FIFO; DLQ monitorada diariamente). |  
| **Parse de Arquivos** | Azure Document Intelligence (OCR de PDFs didáticos contendo tabelas e fórmulas). |  
| **Orquestração de IA** | LangChain integrado com FastAPI (ASGI obrigatório em todo I/O). |  
| **LLMs** | Gemini (inicial) / DeepSeek (planejado) com modelo de *embedding* independente (ex: multilingual-e5). |  
| **Mensagens Interativas** | WhatsApp Cloud API (Reply Buttons, List Messages) — sem BSPs intermediários. |  
| **Quiz Engine** | Geração via LLM (PydanticOutputParser → JSON) + validação determinística (if/else). |  
| **WhatsApp Pricing** | Utility Templates + janela de serviço 24h (∼R$ 0,05/aluno/dia vs. R$ 0,25 BIC avulso). |  
*Nota: Toda a arquitetura foi desenhada presumindo I/O assíncrono em Python (FastAPI/asyncio) para que a aplicação não sofra de bloqueio de thread (thread starvation) no processamento dos webhooks do Meta.*
```

<!-- FILE_PATH: docker-compose.yml -->
### 📄 `docker-compose.yml`

```yaml
version: '3.8'

services:
  # Redis para debouncing, histórico de conversas e tracking de janelas de 24h
  redis:
    image: redis:7-alpine
    container_name: whatsapp_redis
    ports:
      - "6379:6379"
    command: ["redis-server", "--appendonly", "yes"]
    volumes:
      - redis_data:/data

  # Qdrant para o banco vetorial local (RAG)
  qdrant:
    image: qdrant/qdrant:latest
    container_name: whatsapp_qdrant
    ports:
      - "6333:6333"
      - "6334:6334"
    volumes:
      - qdrant_data:/qdrant/storage

  # Azurite atua como um emulador local APENAS para Azure Blob Storage (não suporta Service Bus)
  azurite:
    image: mcr.microsoft.com/azure-storage/azurite
    container_name: whatsapp_azurite
    ports:
      - "10000:10000"
      - "10001:10001"
      - "10002:10002"
    volumes:
      - azurite_data:/workspace
    command: ["azurite", "-l", "/workspace", "--blobHost", "0.0.0.0", "--queueHost", "0.0.0.0"]

  # Banco de dados SQL interno necessário para o emulador do Service Bus guardar estado
  sqledge:
    image: mcr.microsoft.com/azure-sql-edge:latest
    container_name: whatsapp_sqledge
    environment:
      - ACCEPT_EULA=1
      - MSSQL_SA_PASSWORD=YourStrong!Passw0rd
    ports:
      - "1433:1433"

  # Emulador Oficial do Azure Service Bus
  servicebus:
    image: mcr.microsoft.com/azure-messaging/servicebus-emulator:latest
    container_name: whatsapp_servicebus
    ports:
      - "5672:5672"
      - "5300:5300"
    environment:
      - ACCEPT_EULA=Y
      - SQL_SERVER=sqledge
      - MSSQL_SA_PASSWORD=YourStrong!Passw0rd
      - CONFIG_PATH=/ServiceBus_Emulator/ConfigFiles/Config.json
    volumes:
      - ./emulator_config.json:/ServiceBus_Emulator/ConfigFiles/Config.json
    depends_on:
      - sqledge

volumes:
  redis_data:
  qdrant_data:
  azurite_data:

```

<!-- FILE_PATH: global.env -->
### 📄 `global.env`

```bash
ENVIRONMENT=development
LOG_LEVEL=INFO

# Meta / WhatsApp Webhook
META_APP_SECRET="dev_secret_override_in_prod"
WHATSAPP_TOKEN="EAAN7Py1gGUoBRVEIeUbUWeO7Nau9U0Cbw2um5bj01lLB8GAIpB7G5eH77fBC2VMZA9yUcV34gs8PhieUcIM8ZCNZB6IhbeZCkT0nZAIv4k84d4ZAvT76nsP3lDNYHI0x8FMnvbtW9rjM7g82XSceHLdZBZBENg5V8fvSB2WJNpiJQHkitpDoDQybTIhRd9UDLuU5liTXhhoNgNXRZCYo8lCFH5RltfjTmvVWfkx39JxssPTSgpdMIiDZCxcqaJadEquBh7EX4wO0vKvVCUPL5vuqKFboBS"
PHONE_NUMBER_ID="1119433844592935"
WHATSAPP_API_URL="https://graph.facebook.com/v25.0"

# Ngrok
NGROK_URL="https://5f0faccfdf79.ngrok-free.app"

# Redis
REDIS_URL="redis://localhost:6379/0"
DEBOUNCE_TTL_SECONDS=2
MAX_BUFFER_CHARS=4096

# Service Bus
SERVICEBUS_FQDN="your-namespace.servicebus.windows.net"
SERVICEBUS_CONNECTION_STRING=""

# Qdrant
QDRANT_URL="http://localhost:6333"
QDRANT_API_KEY=""

# Azure Document Intelligence
DOCUMENT_INTELLIGENCE_ENDPOINT="https://your-endpoint.cognitiveservices.azure.com/"
DOCUMENT_INTELLIGENCE_KEY=""

# DeepSeek / Gemini
DEEPSEEK_API_KEY=""
GOOGLE_API_KEY="AIzaSyBc3vY-EaGleIDrKlk6gwcDT0WpsJfFDWg"

# Database
DATABASE_URL="sqlite+aiosqlite:///./local_dev.db"

```

<!-- FILE_PATH: global_example.env -->
### 📄 `global_example.env`

```bash
# global.env
# Central configuration file. Defines the Azure region, subscription,


SUBSCRIPTION_ID="YOUR_SUBSCRIPTION_ID_HERE"
LOCATION="brazilsouth"
PROJECT_PREFIX="conc-insp-v1"
KEYVAULT_URL="https://kasdfas-dev.vault.azure.net/"
ENDPOINT_URL="https://xxxxxxxxx.brazilsouth.inference.ml.azure.com/score"
ENDPOINT_KEY="8gC9nLl16WlqKTQGgcieewGo01s9asdfdf a1UJd11SW1ijbs56HTAEODI9JQQJ99CCAAAAAAAAAAAAINFRAZML23Ms"

# Google Gemini API Key
#GOOGLE_API_KEY="API_KEY_HERE"

# Path to your Google Cloud credentials file
GOOGLE_APPLICATION_CREDENTIALS="name.json"

# Azure Speech Service Credentials
AZURE_SPEECH_KEY="Key"
AZURE_SPEECH_REGION="eastus"

# WhatsApp / Meta Credentials
VERIFY_TOKEN="my-secret-agent-token-123321"
WHATSAPP_TOKEN="EAAQHu586oZBsBPSmJZCnIPZCK6r0ZA1J7Xpm8Ye0zTxRO8YADDiDAAGAUN1TegQMIR80hZArKkMmFDHZAZAOvTbt4ViBqTl6gZCxF4NL2SeYyZC1kex24kEffyJr4ZCZA1p4R7SZBviJNcsCC7AiBW3JFJ66ZC6xN2Bk5tlub8t366IgfnkddNjIb1gZB4094EyQgkPVwpkKzxBjuzQMbkZBb1bZBtD0QEo7DQ0ZCV8pHB224IAl06tLjxDXePrNj1mBUMQZDZD"
PHONE_NUMBER_ID="821819627672859"
NGROK_URL="https://5f0faccfdf79.ngrok-free.app"
```

<!-- FILE_PATH: requirements.txt -->
### 📄 `requirements.txt`

```text
aiohappyeyeballs==2.6.1
aiohttp==3.13.5
aiosignal==1.4.0
aiosqlite==0.20.0
annotated-doc==0.0.4
annotated-types==0.7.0
anyio==4.13.0
attrs==26.1.0
azure-ai-documentintelligence==1.0.2
azure-core==1.41.0
azure-identity==1.16.0
azure-servicebus==7.12.2
certifi==2026.4.22
cffi==2.0.0
charset-normalizer==3.4.7
click==8.3.3
cryptography==48.0.0
distro==1.9.0
dnspython==2.8.0
email-validator==2.3.0
fastapi==0.111.0
fastapi-cli==0.0.24
filetype==1.2.0
frozenlist==1.8.0
google-api-core==2.30.3
google-api-python-client==2.196.0
google-auth==2.52.0
google-auth-httplib2==0.4.0
google-auth-oauthlib==1.4.0
google-genai==1.75.0
googleapis-common-protos==1.75.0
greenlet==3.5.0
grpcio==1.80.0
grpcio-tools==1.80.0
h11==0.16.0
h2==4.3.0
hpack==4.1.0
httpcore==1.0.9
httplib2==0.31.2
httptools==0.7.1
httpx==0.28.1
hyperframe==6.1.0
idna==3.13
isodate==0.7.2
jinja2==3.1.6
jiter==0.14.0
jsonpatch==1.33
jsonpointer==3.1.1
langchain==1.2.18
langchain-core==1.3.3
langchain-google-genai==4.2.2
langchain-openai==1.2.1
langchain-protocol==0.0.15
langchain-text-splitters==1.1.2
langgraph==1.1.10
langgraph-checkpoint==4.0.3
langgraph-prebuilt==1.0.13
langgraph-sdk==0.3.14
langsmith==0.8.3
markdown-it-py==4.2.0
markupsafe==3.0.3
mdurl==0.1.2
msal==1.36.0
msal-extensions==1.3.1
multidict==6.7.1
numpy==2.4.4
oauthlib==3.3.1
openai==2.36.0
orjson==3.11.9
ormsgpack==1.12.2
packaging==26.2
portalocker==2.10.1
propcache==0.5.2
proto-plus==1.28.0
protobuf==6.33.6
pyasn1==0.6.3
pyasn1-modules==0.4.2
pycparser==3.0
pydantic==2.13.4
pydantic-core==2.46.4
pydantic-settings==2.14.1
pygments==2.20.0
pyjwt==2.12.1
pyparsing==3.3.2
python-dotenv==1.0.1
python-multipart==0.0.27
pyyaml==6.0.3
qdrant-client==1.9.1
redis==5.0.4
regex==2026.5.9
requests==2.33.1
requests-oauthlib==2.0.0
requests-toolbelt==1.0.0
rich==15.0.0
rich-toolkit==0.19.7
setuptools==82.0.1
shellingham==1.5.4
sniffio==1.3.1
sqlalchemy==2.0.30
starlette==0.37.2
structlog==24.1.0
tenacity==8.3.0
tiktoken==0.12.0
tqdm==4.67.3
typer==0.25.1
typing-extensions==4.15.0
typing-inspection==0.4.2
ujson==5.12.1
uritemplate==4.2.0
urllib3==2.7.0
uuid-utils==0.14.1
uvicorn==0.30.1
uvloop==0.22.1
watchfiles==1.1.1
websockets==16.0
xxhash==3.7.0
yarl==1.23.0
zstandard==0.25.0

```

### 📁 `scripts`

<!-- FILE_PATH: scripts/seed_local_db.py -->
#### 📄 `scripts/seed_local_db.py`

```py
import asyncio
import uuid
from sqlalchemy import text
from src.db.database import init_db, AsyncSessionLocal
import src.db.models # Ensure models are registered with Base

async def seed():
    # Ensure tables are created
    await init_db()
    
    async with AsyncSessionLocal() as session:
        # Create a fake teacher
        teacher_id = str(uuid.uuid4())
        await session.execute(
            text("""
                INSERT OR IGNORE INTO Teachers (id, name, subject, phone, assistant_persona, out_of_scope_message)
                VALUES (:id, 'Professor Teste', 'Matemática', '5511000000000', 'Seja educado e prestativo', 'Isso está fora do escopo.')
            """),
            {"id": teacher_id}
        )
        
        # Create a fake course
        course_id = str(uuid.uuid4())
        await session.execute(
            text("""
                INSERT OR IGNORE INTO Courses (id, teacher_id, name)
                VALUES (:id, :teacher_id, 'Matemática Básica')
            """),
            {"id": course_id, "teacher_id": teacher_id}
        )
        
        # Create a real student for testing
        student_id = str(uuid.uuid4())
        phone = "5513991719595"
        await session.execute(
            text("""
                INSERT OR IGNORE INTO Students (id, name, phone)
                VALUES (:id, 'Ygor (Aluno)', :phone)
            """),
            {"id": student_id, "phone": phone}
        )
        
        # Enroll the student
        enrollment_id = str(uuid.uuid4())
        await session.execute(
            text("""
                INSERT OR IGNORE INTO StudentEnrollment (enrollment_id, student_id, course_id, lesson_id)
                VALUES (:id, :student_id, :course_id, 'lesson_1')
            """),
            {"id": enrollment_id, "student_id": student_id, "course_id": course_id}
        )
        
        await session.commit()
        print(f"Database seeded successfully!")
        print(f"Student Phone for Testing: {phone}")

if __name__ == "__main__":
    asyncio.run(seed())

```

<!-- FILE_PATH: scripts/seed_qdrant.py -->
#### 📄 `scripts/seed_qdrant.py`

```py
import asyncio
import uuid
from sqlalchemy import text
from src.db.database import AsyncSessionLocal
from src.rag.qdrant_client import init_qdrant_collection, get_qdrant
from src.llm.factory import get_embedder
from qdrant_client.models import PointStruct
import src.llm # triggers providers init

async def seed_qdrant():
    print("Inicializando Coleção Qdrant...")
    await init_qdrant_collection("documents")
    qdrant = await get_qdrant()
    embedder = get_embedder("gemini")

    print("Buscando Professor e Curso no banco SQLite...")
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("SELECT id, name FROM Teachers WHERE name = 'Professor Teste' LIMIT 1"))
        teacher = result.fetchone()
        if not teacher:
            print("❌ Professor não encontrado! Rode o seed_local_db.py primeiro.")
            return
        teacher_id = teacher[0]

        result = await session.execute(text("SELECT id, name FROM Courses WHERE teacher_id = :tid LIMIT 1"), {"tid": teacher_id})
        course = result.fetchone()
        course_id = course[0]

    print(f"Professor ID: {teacher_id}")
    print(f"Course ID: {course_id}")

    # Conteúdos sobre o Teorema de Pitágoras para a Aula 1
    chunks = [
        "O Teorema de Pitágoras diz que em qualquer triângulo retângulo, o quadrado do comprimento da hipotenusa é igual à soma dos quadrados dos comprimentos dos catetos (a² + b² = c²).",
        "A hipotenusa é sempre o lado mais longo do triângulo retângulo e fica oposto ao ângulo reto de 90 graus.",
        "O Teorema de Pitágoras só se aplica a triângulos retângulos. Ele não funciona para triângulos acutângulos ou obtusângulos."
    ]

    print("Gerando Embeddings (Gemini)...")
    embeddings = await embedder.aembed_documents(chunks)

    print("Inserindo no Qdrant...")
    points = []
    for i, (chunk, vector) in enumerate(zip(chunks, embeddings)):
        points.append(
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "teacher_id": teacher_id,
                    "course_id": course_id,
                    "lesson_id": "lesson_1",
                    "content": chunk,
                    "source": "Apostila Matemática Básica - Aula 1"
                }
            )
        )
        
    await qdrant.upsert(
        collection_name="documents",
        points=points
    )
    
    print("✅ Seed do Qdrant concluído com sucesso!")

if __name__ == "__main__":
    asyncio.run(seed_qdrant())

```

<!-- FILE_PATH: scripts/simulate_whatsapp.py -->
#### 📄 `scripts/simulate_whatsapp.py`

```py
import httpx
import asyncio
import sys
import uuid

async def simulate_message(text: str):
    url = "http://localhost:8000/v1/webhook"
    
    # Meta WhatsApp API Payload Structure
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "1234567890",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "1234567890",
                                "phone_number_id": "1234567890"
                            },
                            "contacts": [
                                {
                                    "profile": {
                                        "name": "Aluno Teste"
                                    },
                                    "wa_id": "5511999999999"
                                }
                            ],
                            "messages": [
                                {
                                    "from": "5511999999999",
                                    "id": f"wamid.{uuid.uuid4()}",
                                    "timestamp": "1609459200",
                                    "type": "text",
                                    "text": {
                                        "body": text
                                    }
                                }
                            ]
                        },
                        "field": "messages"
                    }
                ]
            }
        ]
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=payload, headers={"Content-Type": "application/json"})
            print(f"Status Code: {response.status_code}")
            print(f"Response: {response.text}")
            if response.status_code == 200:
                print("✅ Message successfully accepted by Webhook!")
            else:
                print("❌ Webhook rejected the message.")
        except Exception as e:
            print(f"❌ Connection error: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        text_message = sys.argv[1]
    else:
        text_message = "Olá professor, como funciona o curso?"
        
    asyncio.run(simulate_message(text_message))

```

### 📁 `src`

<!-- FILE_PATH: src/__init__.py -->
#### 📄 `src/__init__.py`

```py

```

#### 📁 `src/cache`

<!-- FILE_PATH: src/cache/__init__.py -->
##### 📄 `src/cache/__init__.py`

```py

```

<!-- FILE_PATH: src/cache/debounce.py -->
##### 📄 `src/cache/debounce.py`

```py
from src.cache.redis_client import get_redis

# Script Lua para drenar atomicamente o buffer e redefinir o lock
DEBOUNCE_DRAIN_SCRIPT = """
local buffer_key = KEYS[1]
local lock_key   = KEYS[2]
local cooldown   = ARGV[1]

-- Lê todas as mensagens do buffer
local messages = redis.call('LRANGE', buffer_key, 0, -1)

-- Deleta o buffer atomicamente
redis.call('DEL', buffer_key)

-- Redefine o lock de debounce com TTL curto (cooldown) para permitir novas interações
redis.call('SET', lock_key, '1', 'EX', cooldown)

return messages
"""

async def drain_buffer(phone: str, cooldown: int = 1) -> list[str]:
    """
    Drains all text currently accumulated in the user's buffer atomically.
    Prevents race conditions where messages are pushed right before DEL.
    """
    redis = await get_redis()
    buffer_key = f"buffer:{phone}"
    lock_key = f"debounce:{phone}"
    
    # Load and execute the Lua script
    raw_messages = await redis.eval(
        DEBOUNCE_DRAIN_SCRIPT,
        2,  # number of keys
        buffer_key, 
        lock_key,
        cooldown # args
    )
    
    if not raw_messages:
        return []
        
    return [msg.decode('utf-8') if isinstance(msg, bytes) else str(msg) for msg in raw_messages]

```

<!-- FILE_PATH: src/cache/redis_client.py -->
##### 📄 `src/cache/redis_client.py`

```py
import redis.asyncio as redis
from src.shared.config import settings

# Global Redis Connection Pool
redis_client = redis.from_url(
    settings.REDIS_URL,
    encoding="utf-8",
    decode_responses=True
)

async def get_redis():
    """Provides the global redis client"""
    return redis_client

```

<!-- FILE_PATH: src/cache/session_history.py -->
##### 📄 `src/cache/session_history.py`

```py
from langchain_core.chat_history import BaseChatMessageHistory
from langchain_core.messages import BaseMessage, messages_from_dict, message_to_dict
from src.cache.redis_client import get_redis
import json

class OptimizedRedisChatMessageHistory(BaseChatMessageHistory):
    """
    Redis-based chat history that aggressively enforces a maximum length natively in Redis via LTRIM,
    preventing Out-of-Memory errors and excessive token usage without needing ConversationTokenBufferMemory.
    
    WARNING: Synchronous methods are disabled. You MUST use async methods.
    """
    def __init__(self, session_id: str, max_messages: int = 40):
        self.session_id = session_id
        self.max_messages = max_messages
        self.key = f"chat_history:{session_id}"
        self.redis_client = None

    async def _get_client(self):
        if not self.redis_client:
            self.redis_client = await get_redis()
        return self.redis_client

    @property
    def messages(self) -> list[BaseMessage]:
        raise NotImplementedError("Use await get_messages() instead. Sync execution is blocked.")

    def add_message(self, message: BaseMessage) -> None:
        raise NotImplementedError("Use await aadd_message() instead. Sync execution is blocked.")

    def clear(self) -> None:
        raise NotImplementedError("Use await aclear() instead. Sync execution is blocked.")

    async def aget_messages(self) -> list[BaseMessage]:
        redis = await self._get_client()
        items = await redis.lrange(self.key, 0, -1)
        
        parsed_messages = []
        for item in items:
            try:
                parsed_messages.append(json.loads(item))
            except json.JSONDecodeError:
                continue
                
        return messages_from_dict(parsed_messages)

    async def aadd_messages(self, messages: list[BaseMessage]) -> None:
        redis = await self._get_client()
        
        # Serialize messages
        serialized = [json.dumps(message_to_dict(m)) for m in messages]
        
        # Push to redis and then immediately LTRIM
        # Pipeline ensures this happens efficiently
        async with redis.pipeline(transaction=True) as pipe:
            await pipe.rpush(self.key, *serialized)
            # Keep only the last max_messages
            # LTRIM -N -1 keeps the last N elements
            await pipe.ltrim(self.key, -self.max_messages, -1)
            # Extend TTL (e.g. 30 days)
            await pipe.expire(self.key, 60 * 60 * 24 * 30)
            await pipe.execute()

    async def aadd_message(self, message: BaseMessage) -> None:
        await self.aadd_messages([message])

    async def aclear(self) -> None:
        redis = await self._get_client()
        await redis.delete(self.key)

```

#### 📁 `src/db`

<!-- FILE_PATH: src/db/__init__.py -->
##### 📄 `src/db/__init__.py`

```py

```

<!-- FILE_PATH: src/db/database.py -->
##### 📄 `src/db/database.py`

```py
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import declarative_base
from src.shared.config import settings

# Used for local dev (SQLite) or production (Azure SQL with asyncpg/aioodbc)
# Production URL would look like: mssql+aioodbc://<client_id>@<server>.database.windows.net/<db>?driver=ODBC+Driver+18+for+SQL+Server&authentication=ActiveDirectoryMsi
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
    pool_pre_ping=True
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

Base = declarative_base()

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session

async def init_db():
    """Create all tables. Useful for local dev with SQLite."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

```

<!-- FILE_PATH: src/db/models.py -->
##### 📄 `src/db/models.py`

```py
from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.sql import func
from src.db.database import Base

class Teacher(Base):
    __tablename__ = "Teachers"
    
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    phone = Column(String, unique=True)
    is_active = Column(Boolean, default=True)
    
    assistant_persona = Column(Text, nullable=False)
    out_of_scope_message = Column(Text, nullable=False)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class Course(Base):
    __tablename__ = "Courses"
    
    id = Column(String, primary_key=True)
    teacher_id = Column(String, ForeignKey("Teachers.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class Student(Base):
    __tablename__ = "Students"
    
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    phone = Column(String, unique=True, nullable=False)
    is_active = Column(Boolean, default=True)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class StudentEnrollment(Base):
    __tablename__ = "StudentEnrollment"
    
    enrollment_id = Column(String, primary_key=True)
    student_id = Column(String, ForeignKey("Students.id"), nullable=False)
    course_id = Column(String, ForeignKey("Courses.id"), nullable=False)
    lesson_id = Column(String, nullable=False) # Stable identifier pointer
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    __table_args__ = (
        UniqueConstraint('student_id', 'course_id', name='uq_student_course'),
    )

class ScheduledClass(Base):
    __tablename__ = "ScheduledClasses"
    
    class_id = Column(String, primary_key=True)
    teacher_id = Column(String, ForeignKey("Teachers.id"), nullable=False)
    course_id = Column(String, ForeignKey("Courses.id"), nullable=False)
    lesson_id = Column(String, nullable=False)
    scheduled_date = Column(DateTime, nullable=False)
    
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index('idx_scheduled_course_date', 'course_id', 'scheduled_date'),
    )

class Attendance(Base):
    __tablename__ = "Attendance"
    
    attendance_id = Column(String, primary_key=True)
    student_id = Column(String, ForeignKey("Students.id"), nullable=False)
    class_id = Column(String, ForeignKey("ScheduledClasses.class_id"), nullable=False)
    confirmed = Column(Boolean, default=False, nullable=False)
    
    created_at = Column(DateTime, server_default=func.now())
    
    __table_args__ = (
        UniqueConstraint('student_id', 'class_id', name='uq_student_class'),
    )

class FileSyncState(Base):
    __tablename__ = "file_sync_state"
    
    file_id = Column(String, primary_key=True)
    teacher_id = Column(String, ForeignKey("Teachers.id"), nullable=False)
    file_hash = Column(String)
    last_modified = Column(DateTime)
    status = Column(String) # 'pendente' | 'processando' | 'concluído' | 'falhou'
    is_deleted = Column(Boolean, default=False)
    error_message = Column(String(500))
    last_synced = Column(DateTime)
    processing_started_at = Column(DateTime)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    __table_args__ = (
        Index('idx_sync_teacher_status', 'teacher_id', 'status'),
    )

class PendingQuizzes(Base):
    __tablename__ = "PendingQuizzes"
    
    quiz_id = Column(String, primary_key=True)
    student_id = Column(String, ForeignKey("Students.id"), nullable=False)
    course_id = Column(String, ForeignKey("Courses.id"), nullable=False)
    lesson_id = Column(String, nullable=False)
    question_text = Column(Text, nullable=False)
    alternatives = Column(Text, nullable=False) # Store JSON string
    correct_answer = Column(String, nullable=False)
    explanation = Column(Text, nullable=False)
    student_answer = Column(String, nullable=True)
    is_correct = Column(Boolean, nullable=True)
    status = Column(String, default="pending") # 'pending', 'answered', 'expired'
    
    expires_at = Column(DateTime, nullable=False)
    answered_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index('idx_pending_quiz_student', 'student_id', 'status'),
    )

class MessageTemplates(Base):
    __tablename__ = "MessageTemplates"
    
    template_name = Column(String, primary_key=True)
    language = Column(String, nullable=False)
    category = Column(String, nullable=False) # UTILITY, MARKETING, AUTHENTICATION
    status = Column(String, default="pending") # 'approved', 'rejected', 'pending'
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class StudentSchedule(Base):
    __tablename__ = "StudentSchedule"
    
    schedule_id = Column(String, primary_key=True)
    student_id = Column(String, ForeignKey("Students.id"), nullable=False)
    template_name = Column(String, ForeignKey("MessageTemplates.template_name"), nullable=False)
    next_trigger_date = Column(DateTime, nullable=False)
    status = Column(String, default="scheduled") # 'scheduled', 'sent', 'failed'
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index('idx_schedule_trigger', 'next_trigger_date', 'status'),
    )

```

#### 📁 `src/drive`

<!-- FILE_PATH: src/drive/client.py -->
##### 📄 `src/drive/client.py`

```py
import os
import io
import structlog
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from src.shared.config import settings

logger = structlog.get_logger(__name__)

# Scopes needed for downloading and listing files
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

class GoogleDriveClient:
    def __init__(self):
        self.creds = None
        self.service = None
        
        # Verify if service account file exists
        if not os.path.exists(settings.GOOGLE_SERVICE_ACCOUNT_FILE):
            logger.warning(f"Service account file not found: {settings.GOOGLE_SERVICE_ACCOUNT_FILE}")
            return
            
        try:
            self.creds = service_account.Credentials.from_service_account_file(
                settings.GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPES)
            self.service = build('drive', 'v3', credentials=self.creds)
            logger.info("Google Drive API initialized via Service Account")
        except Exception as e:
            logger.error("Failed to initialize Google Drive Client", error=str(e))

    async def list_pdf_files(self, folder_id: str):
        """
        Lists all PDF files in the specified Google Drive folder.
        Returns a list of dicts with id, name, and modifiedTime.
        """
        if not self.service:
            raise ValueError("Google Drive Service is not initialized.")
            
        query = f"'{folder_id}' in parents and mimeType='application/pdf' and trashed=false"
        
        # We use sync execute() here but it's okay for the ingestion worker background thread, 
        # or we could wrap it in asyncio.to_thread if strictly needed for concurrency.
        import asyncio
        loop = asyncio.get_running_loop()
        
        def _fetch():
            results = self.service.files().list(
                q=query,
                fields="files(id, name, modifiedTime)",
                spaces='drive'
            ).execute()
            return results.get('files', [])
            
        files = await loop.run_in_executor(None, _fetch)
        return files

    async def download_file(self, file_id: str) -> bytes:
        """
        Downloads a file from Google Drive directly into memory (bytes).
        """
        if not self.service:
            raise ValueError("Google Drive Service is not initialized.")
            
        import asyncio
        loop = asyncio.get_running_loop()
        
        def _download():
            request = self.service.files().get_media(fileId=file_id)
            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
            return fh.getvalue()
            
        file_bytes = await loop.run_in_executor(None, _download)
        return file_bytes

# Singleton instance
drive_client = GoogleDriveClient()

```

#### 📁 `src/llm`

<!-- FILE_PATH: src/llm/__init__.py -->
##### 📄 `src/llm/__init__.py`

```py
import src.llm.providers
```

<!-- FILE_PATH: src/llm/factory.py -->
##### 📄 `src/llm/factory.py`

```py
from typing import Dict, Type
from src.llm.interfaces import LLMProvider, EmbeddingProvider

_providers: Dict[str, Type[LLMProvider]] = {}
_embedders: Dict[str, Type[EmbeddingProvider]] = {}

def register_provider(name: str, cls: Type[LLMProvider]):
    _providers[name] = cls

def register_embedder(name: str, cls: Type[EmbeddingProvider]):
    _embedders[name] = cls

def get_provider(name: str = "openai") -> LLMProvider:
    cls = _providers.get(name)
    if cls is None:
        raise ValueError(f"Provider '{name}' not registered.")
    return cls()

def get_embedder(name: str = "openai") -> EmbeddingProvider:
    cls = _embedders.get(name)
    if cls is None:
        raise ValueError(f"Embedder '{name}' not registered.")
    return cls()

```

<!-- FILE_PATH: src/llm/interfaces.py -->
##### 📄 `src/llm/interfaces.py`

```py
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class LLMProvider(ABC):
    @abstractmethod
    async def agenerate(self, prompt: str, system_message: Optional[str] = None, history: Optional[List[Any]] = None) -> str:
        """Generates a text response from the model asynchronously."""
        pass

class EmbeddingProvider(ABC):
    @abstractmethod
    async def aembed_query(self, text: str) -> List[float]:
        """Embeds a single query string asynchronously."""
        pass

    @abstractmethod
    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        """Embeds a list of document strings asynchronously."""
        pass

```

<!-- FILE_PATH: src/llm/providers.py -->
##### 📄 `src/llm/providers.py`

```py
from src.llm.interfaces import LLMProvider, EmbeddingProvider
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage
from src.shared.config import settings
from typing import List, Optional, Any
from src.shared.telemetry import track_event

class OpenAILLMProvider(LLMProvider):
    def __init__(self, model_name: str = "gpt-4o-mini"):
        # Placeholder implementation - can be swapped to Gemini/DeepSeek
        self.llm = ChatOpenAI(
            model=model_name, 
            temperature=0.3,
            api_key=settings.OPENAI_API_KEY,
            max_retries=3
        )

    async def agenerate(self, prompt: str, system_message: Optional[str] = None, history: Optional[List[BaseMessage]] = None) -> str:
        messages = []
        if system_message:
            messages.append(SystemMessage(content=system_message))
        if history:
            messages.extend(history)
            
        messages.append(HumanMessage(content=prompt))
        
        # In a real impl, we'd wrap this to extract usage_metadata
        response = await self.llm.ainvoke(messages)
        
        track_event("llm_generation", {
            "model": self.llm.model_name,
            "prompt_tokens": response.response_metadata.get("token_usage", {}).get("prompt_tokens", 0),
            "completion_tokens": response.response_metadata.get("token_usage", {}).get("completion_tokens", 0),
        })
        
        return response.content

class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(self, model_name: str = "text-embedding-3-small"):
        self.embeddings = OpenAIEmbeddings(
            model=model_name,
            api_key=settings.OPENAI_API_KEY,
            max_retries=3
        )

    async def aembed_query(self, text: str) -> List[float]:
        return await self.embeddings.aembed_query(text)

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        # Batching is mandatory as per MLOps patterns
        return await self.embeddings.aembed_documents(texts)


class GeminiLLMProvider(LLMProvider):
    def __init__(self, model_name: str = "gemini-2.5-flash"):
        if not settings.GOOGLE_API_KEY:
            raise ValueError("GOOGLE_API_KEY is not set in environment.")
        
        self.llm = ChatGoogleGenerativeAI(
            model=model_name,
            temperature=0.3,
            google_api_key=settings.GOOGLE_API_KEY,
            max_retries=3
        )

    async def agenerate(self, prompt: str, system_message: Optional[str] = None, history: Optional[List[BaseMessage]] = None) -> str:
        messages = []
        if system_message:
            messages.append(SystemMessage(content=system_message))
        if history:
            messages.extend(history)
            
        messages.append(HumanMessage(content=prompt))
        
        response = await self.llm.ainvoke(messages)
        
        # Gemini usage metadata
        usage = response.response_metadata.get("token_usage", {}) if response.response_metadata else {}
        prompt_tokens = usage.prompt_token_count if hasattr(usage, "prompt_token_count") else 0
        completion_tokens = usage.candidates_token_count if hasattr(usage, "candidates_token_count") else 0
        
        track_event("llm_generation", {
            "model": self.llm.model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        })
        
        content = response.content
        if not content or not content.strip():
            content = "Desculpe, a inteligência artificial bloqueou a resposta ou retornou vazio."
            
        return content

class GeminiEmbeddingProvider(EmbeddingProvider):
    def __init__(self, model_name: str = "models/gemini-embedding-2"):
        if not settings.GOOGLE_API_KEY:
            raise ValueError("GOOGLE_API_KEY is not set in environment.")
            
        self.embeddings = GoogleGenerativeAIEmbeddings(
            model=model_name,
            google_api_key=settings.GOOGLE_API_KEY
        )

    async def aembed_query(self, text: str) -> List[float]:
        return await self.embeddings.aembed_query(text)

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        return await self.embeddings.aembed_documents(texts)


from src.llm.factory import register_provider, register_embedder
register_provider("openai", OpenAILLMProvider)
register_embedder("openai", OpenAIEmbeddingProvider)
register_provider("gemini", GeminiLLMProvider)
register_embedder("gemini", GeminiEmbeddingProvider)

```

#### 📁 `src/rag`

<!-- FILE_PATH: src/rag/__init__.py -->
##### 📄 `src/rag/__init__.py`

```py

```

<!-- FILE_PATH: src/rag/prompt_builder.py -->
##### 📄 `src/rag/prompt_builder.py`

```py
from src.db.models import Teacher, Student

# Using str.format() template to avoid LangChain variable collisions
SYSTEM_PROMPT_TEMPLATE = """
Você é o(a) professor(a) {teacher_name}, especialista em {teacher_subject}.
Sua personalidade é: {teacher_persona}

Você está conversando no WhatsApp com seu aluno(a) {student_name}, que está matriculado no curso '{course_name}'.
O aluno está atualmente estudando a aula: {current_lesson_label}.

DIRETRIZES:
1. Responda APENAS usando o contexto fornecido. Não invente informações.
2. Se a pergunta for sobre um assunto não relacionado ao curso ou que você não sabe a resposta pelo contexto, diga: "{out_of_scope}"
3. Mantenha a resposta curta, amigável e direta, adequada para leitura no WhatsApp.
"""

async def resolve_lesson_label(course_id: str, lesson_id: str) -> str:
    """
    Converts a stable lesson_id (e.g. 'les_04') to a readable ordinal position.
    In a real implementation, this would fetch from sequencia.json or database.
    """
    # Placeholder
    return f"Aula atual ({lesson_id})"

async def build_system_prompt(teacher: Teacher, student: Student, course_id: str, course_name: str, current_lesson_id: str) -> str:
    """
    Builds the system prompt asynchronously.
    Anchors the current lesson to mitigate Recency Bias.
    """
    lesson_label = await resolve_lesson_label(course_id, current_lesson_id)
    
    return SYSTEM_PROMPT_TEMPLATE.format(
        teacher_name=teacher.name,
        teacher_subject=teacher.subject,
        teacher_persona=teacher.assistant_persona,
        student_name=student.name,
        current_lesson_label=lesson_label,
        course_name=course_name,
        out_of_scope=teacher.out_of_scope_message
    )

```

<!-- FILE_PATH: src/rag/qdrant_client.py -->
##### 📄 `src/rag/qdrant_client.py`

```py
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, VectorParams, ScalarQuantization, ScalarQuantizationConfig, ScalarType
from src.shared.config import settings

qdrant_client = AsyncQdrantClient(
    url=settings.QDRANT_URL,
    api_key=settings.QDRANT_API_KEY
)

async def init_qdrant_collection(collection_name: str = "documents"):
    """
    Initializes the Qdrant collection with INT8 Scalar Quantization 
    to reduce RAM usage by ~75%, mandatory for the free tier.
    """
    exists = await qdrant_client.collection_exists(collection_name)
    if not exists:
        await qdrant_client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=3072, distance=Distance.COSINE),
            quantization_config=ScalarQuantization(
                scalar=ScalarQuantizationConfig(
                    type=ScalarType.INT8,
                    quantile=0.99,
                    always_ram=True
                )
            )
        )

async def get_qdrant():
    """Provides the configured async Qdrant client."""
    return qdrant_client

```

<!-- FILE_PATH: src/rag/quiz_generator.py -->
##### 📄 `src/rag/quiz_generator.py`

```py
from pydantic import BaseModel, Field
from langchain.output_parsers import PydanticOutputParser
from src.llm.factory import get_provider

class QuizAlternative(BaseModel):
    id: str = Field(description="Identificador da alternativa (A, B, C, D, E)")
    text: str = Field(description="Texto da alternativa")

class QuizOutput(BaseModel):
    question: str = Field(description="Pergunta clara e direta sobre o conteúdo da aula")
    alternatives: list[QuizAlternative] = Field(description="Lista de alternativas (2 a 5)")
    correct_answer: str = Field(description="ID da alternativa correta")
    explanation: str = Field(description="Explicação pedagógica de por que a resposta está correta")

parser = PydanticOutputParser(pydantic_object=QuizOutput)

QUIZ_GENERATION_PROMPT = """Com base no conteúdo da aula abaixo, gere uma questão de múltipla escolha
para testar o entendimento do aluno.

Conteúdo da aula:
{context}

{format_instructions}

Gere a questão em português, com alternativas claras e uma explicação pedagógica
detalhada para a resposta correta."""

async def generate_quiz(
    lesson_content: str,
    num_alternatives: int = 4,
) -> QuizOutput:
    """Gera um quiz em JSON estruturado via LLM.
    
    O custo de tokens é ÚNICO: a pergunta, alternativas, resposta correta
    e explicação são gerados em uma única chamada. A validação posterior
    é determinística (zero tokens).
    """
    llm = get_provider().llm_instance()
    prompt = QUIZ_GENERATION_PROMPT.format(
        context=lesson_content,
        format_instructions=parser.get_format_instructions(),
    )
    response = await llm.ainvoke(prompt)
    return parser.parse(response.content)

```

<!-- FILE_PATH: src/rag/retriever.py -->
##### 📄 `src/rag/retriever.py`

```py
from src.rag.qdrant_client import get_qdrant
from src.llm.factory import get_embedder
from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchAny
from typing import List

async def retrieve_context(teacher_id: str, course_id: str, allowed_lesson_ids: List[str], query: str, top_k: int = 3) -> str:
    """
    Pesquisa documentos relevantes no Qdrant aplicando os filtros arquiteturais de segurança.
    Vazamento de dados é evitado filtrando rigidamente por teacher_id e course_id.
    O lesson_id previne o aluno de receber contexto de aulas futuras.
    """
    qdrant = await get_qdrant()
    embedder = get_embedder("gemini")
    
    # 1. Embed the user's query
    query_vector = await embedder.aembed_query(query)
    
    # 2. Hard Invariants: Query must include teacher_id, course_id, and lesson_id IN (...)
    search_filter = Filter(
        must=[
            FieldCondition(key="teacher_id", match=MatchValue(value=teacher_id)),
            FieldCondition(key="course_id", match=MatchValue(value=course_id)),
            FieldCondition(key="lesson_id", match=MatchAny(any=allowed_lesson_ids))
        ]
    )
    
    # 3. Execute search
    results = await qdrant.search(
        collection_name="documents",
        query_vector=query_vector,
        query_filter=search_filter,
        limit=top_k
    )
    
    # 4. Consolidate context string
    if not results:
        return ""
        
    context_chunks = []
    for hit in results:
        payload = hit.payload
        if payload and "content" in payload:
            source = payload.get("source", "Desconhecido")
            context_chunks.append(f"[Fonte: {source}]\n{payload['content']}")
            
    return "\n\n".join(context_chunks)

```

#### 📁 `src/shared`

<!-- FILE_PATH: src/shared/__init__.py -->
##### 📄 `src/shared/__init__.py`

```py

```

<!-- FILE_PATH: src/shared/config.py -->
##### 📄 `src/shared/config.py`

```py
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
import os
from typing import Optional

class Settings(BaseSettings):
    # App
    ENVIRONMENT: str = Field(default="development")
    LOG_LEVEL: str = Field(default="INFO")
    
    # Meta / WhatsApp Webhook
    META_APP_SECRET: str = Field(default="dev_secret_override_in_prod")
    META_VERIFY_TOKEN: str = Field(default="whatsapp_teacher_token")
    WHATSAPP_TOKEN: str = Field(default="dev_token")
    PHONE_NUMBER_ID: str = Field(default="YOUR_PHONE_ID")
    WHATSAPP_API_URL: str = Field(default="https://graph.facebook.com/v21.0")
    
    # Redis
    REDIS_URL: str = Field(default="redis://localhost:6379/0")
    DEBOUNCE_TTL_SECONDS: int = Field(default=2)
    MAX_BUFFER_CHARS: int = Field(default=4096)
    
    # Service Bus
    SERVICEBUS_FQDN: str = Field(default="")
    SERVICEBUS_CONNECTION_STRING: Optional[str] = Field(default=None) # Only for local dev with Azurite/Emulator
    
    # Qdrant
    QDRANT_URL: str = Field(default="http://localhost:6333")
    QDRANT_API_KEY: Optional[str] = Field(default=None)
    
    # Azure Document Intelligence
    DOCUMENT_INTELLIGENCE_ENDPOINT: str = Field(default="")
    DOCUMENT_INTELLIGENCE_KEY: Optional[str] = Field(default=None) # Used only if Managed Identity isn't available
    
    # OpenAI / Embeddings (Placeholder for Gemini/DeepSeek depending on implementation)
    OPENAI_API_KEY: Optional[str] = Field(default=None)
    GOOGLE_API_KEY: Optional[str] = Field(default=None)
    
    # Google Drive
    GOOGLE_SERVICE_ACCOUNT_FILE: str = Field(default="service_account.json")
    GOOGLE_DRIVE_FOLDER_ID: str = Field(default="")
    
    # Database
    DATABASE_URL: str = Field(default="sqlite+aiosqlite:///./local_dev.db")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()

```

<!-- FILE_PATH: src/shared/service_bus.py -->
##### 📄 `src/shared/service_bus.py`

```py
from azure.servicebus.aio import ServiceBusClient
from azure.servicebus import ServiceBusMessage
from azure.identity.aio import DefaultAzureCredential
from src.shared.config import settings
import json
from src.shared.telemetry import track_event

class ServiceBusWrapper:
    def __init__(self):
        self.client = None
        self.credential = None

    async def get_client(self) -> ServiceBusClient:
        if not self.client:
            if settings.SERVICEBUS_CONNECTION_STRING:
                # Local dev only — plain AMQP (emulator doesn't support WebSockets)
                self.client = ServiceBusClient.from_connection_string(
                    conn_str=settings.SERVICEBUS_CONNECTION_STRING,
                    retry_total=10,
                    retry_backoff_factor=1,
                    retry_backoff_max=30
                )
            else:
                # Production Managed Identity
                self.credential = DefaultAzureCredential()
                self.client = ServiceBusClient(
                    fully_qualified_namespace=settings.SERVICEBUS_FQDN,
                    credential=self.credential
                )
        return self.client

    async def enqueue(self, queue: str, payload: dict, session_id: str = None):
        """
        Enqueues a message to Azure Service Bus.
        If session_id is provided, guarantees strict FIFO order per session (student).
        In dev mode (emulator), sessions are disabled.
        """
        import os
        is_dev = os.getenv("ENVIRONMENT", "development") in ("dev", "development")
        
        client = await self.get_client()
        sender = client.get_queue_sender(queue_name=queue)
        
        message_body = json.dumps(payload)
        # Emulator doesn't support sessions — strip session_id in dev
        effective_session_id = session_id if not is_dev else None
        message = ServiceBusMessage(message_body, session_id=effective_session_id)
        
        async with sender:
            await sender.send_messages(message)
            
        track_event("message_enqueued", {
            "queue": queue,
            "session_id": effective_session_id,
            "correlation_id": payload.get("correlation_id")
        })

    async def close(self):
        if self.client:
            await self.client.close()
        if self.credential:
            await self.credential.close()

# Singleton instance
service_bus = ServiceBusWrapper()

```

<!-- FILE_PATH: src/shared/telemetry.py -->
##### 📄 `src/shared/telemetry.py`

```py
import structlog
import logging
from typing import Any, Dict, Optional
import json

def setup_telemetry(log_level: str = "INFO"):
    """
    Configures structlog to output JSON logs.
    In production, this allows Application Insights to parse dimensions easily.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)
    
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer()
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    
    logging.basicConfig(
        format="%(message)s",
        level=level,
    )

logger = structlog.get_logger()

def track_event(event_name: str, dimensions: Optional[Dict[str, Any]] = None):
    """
    Helper function to log structured events.
    Required dimensions usually include: correlation_id, teacher_id, student_id, course_id, operation.
    """
    if dimensions is None:
        dimensions = {}
    
    logger.info(event_name, **dimensions)

```

<!-- FILE_PATH: src/shared/whatsapp_interactive.py -->
##### 📄 `src/shared/whatsapp_interactive.py`

```py
import os
import httpx
from src.shared.config import settings

# WhatsApp API Settings
WHATSAPP_API_URL = settings.WHATSAPP_API_URL
PHONE_NUMBER_ID = settings.PHONE_NUMBER_ID
ACCESS_TOKEN = settings.WHATSAPP_TOKEN

async def _send_whatsapp_request(message: dict) -> dict:
    """Envio centralizado para a WhatsApp Cloud API (Graph API)."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url=f"{WHATSAPP_API_URL}/{PHONE_NUMBER_ID}/messages",
            headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
            json=message,
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()

async def send_reply_buttons(
    phone: str,
    body_text: str,
    buttons: list[dict],  # [{"id": "quiz_123_A", "title": "Opção A"}, ...] (máx 3)
    header: str | None = None,
    footer: str | None = None,
) -> dict:
    """Envia mensagem com até 3 Reply Buttons via WhatsApp Cloud API."""
    if len(buttons) > 3:
        raise ValueError("Reply Buttons suportam no máximo 3 opções")

    message = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body_text},
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {"id": btn["id"], "title": btn["title"][:20]}  # título máx 20 chars
                    }
                    for btn in buttons
                ]
            }
        }
    }
    if header:
        message["interactive"]["header"] = {"type": "text", "text": header}
    if footer:
        message["interactive"]["footer"] = {"text": footer}

    return await _send_whatsapp_request(message)

async def send_list_message(
    phone: str,
    body_text: str,
    button_text: str,  # texto do botão que abre o menu (máx 20 chars)
    sections: list[dict],  # [{"title": "Seção", "rows": [{"id": "A", "title": "...", "description": "..."}]}]
    header: str | None = None,
    footer: str | None = None,
) -> dict:
    """Envia mensagem com menu de lista via WhatsApp Cloud API."""
    total_rows = sum(len(s.get("rows", [])) for s in sections)
    if total_rows > 10:
        raise ValueError("List Messages suportam no máximo 10 itens")

    message = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body_text},
            "action": {
                "button": button_text[:20],
                "sections": sections
            }
        }
    }
    if header:
        message["interactive"]["header"] = {"type": "text", "text": header}
    if footer:
        message["interactive"]["footer"] = {"text": footer}

    return await _send_whatsapp_request(message)

async def send_whatsapp_message(phone: str, text: str) -> dict:
    """Envia uma mensagem de texto simples via WhatsApp Cloud API."""
    message = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": phone,
        "type": "text",
        "text": {"body": text}
    }
    return await _send_whatsapp_request(message)

async def send_whatsapp_typing(phone: str) -> dict:
    """Placeholder para indicador de digitação."""
    # A Cloud API não suporta typing indicator direto (apenas mark as read)
    pass

```

#### 📁 `src/webhook`

<!-- FILE_PATH: src/webhook/__init__.py -->
##### 📄 `src/webhook/__init__.py`

```py

```

<!-- FILE_PATH: src/webhook/handlers.py -->
##### 📄 `src/webhook/handlers.py`

```py
from fastapi import Request, Response, APIRouter
from typing import Optional
from uuid import uuid4
import json
from sqlalchemy import text
from src.shared.config import settings

from src.webhook.security import verify_meta_signature, check_rate_limit
from src.shared.service_bus import service_bus
from src.shared.telemetry import track_event
from src.cache.redis_client import get_redis
from src.db.database import AsyncSessionLocal
from src.shared.whatsapp_interactive import send_whatsapp_message

router = APIRouter()

async def get_user_role(phone: str) -> Optional[str]:
    """
    Returns 'student' or 'teacher' or None.
    Cache-Aside: Redis first, SQL fallback.
    """
    redis = await get_redis()
    cache_key = f"role:{phone}"
    
    cached = await redis.get(cache_key)
    if cached:
        return cached

    # Fallback to DB
    async with AsyncSessionLocal() as session:
        # Check student
        result = await session.execute(
            text("SELECT 1 FROM Students WHERE phone = :phone AND is_active = 1"),
            {"phone": phone}
        )
        if result.scalar_one_or_none():
            await redis.set(cache_key, "student", ex=86400)
            return "student"
            
        # Check teacher
        result = await session.execute(
            text("SELECT 1 FROM Teachers WHERE phone = :phone AND is_active = 1"),
            {"phone": phone}
        )
        if result.scalar_one_or_none():
            await redis.set(cache_key, "teacher", ex=86400)
            return "teacher"
            
    return None

@router.get("/v1/webhook")
@router.post("/v1/webhook")
async def handle_webhook(request: Request):
    """
    Main webhook handler for Meta's WhatsApp API.
    SLA < 200ms. No LLM processing here.
    """
    try:
        body_bytes = await request.body()
        
        # Meta validation challenge
        if "hub.mode" in request.query_params:
            if request.query_params.get("hub.verify_token") != settings.META_VERIFY_TOKEN:
                return Response(content="Verification failed", status_code=403)
            return Response(content=request.query_params.get("hub.challenge"), media_type="text/plain")

        # 0. Signature validation
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not await verify_meta_signature(body_bytes, signature):
            print(f"[DEBUG] Signature validation failed. Expected match with META_APP_SECRET.")
            track_event("webhook_signature_failed")
            return Response(status_code=200) # Silent drop

        body = json.loads(body_bytes)
        print(f"\n[DEBUG] WEBHOOK PAYLOAD: {json.dumps(body, indent=2)}\n")

        # Parse message (Simplified for this example; Meta payload is deeply nested)
        try:
            entry = body.get("entry", [{}])[0]
            changes = entry.get("changes", [{}])[0]
            value = changes.get("value", {})
            if "messages" not in value:
                print("[DEBUG] No 'messages' found in payload. Returning 200.")
                return Response(status_code=200)
                
            msg_data = value["messages"][0]
            phone = msg_data["from"]
            msg_type = msg_data["type"]
            msg_id = msg_data["id"]
            
            # Extract text safely
            text = msg_data.get("text", {}).get("body", "") if msg_type == "text" else ""
            
        except (KeyError, IndexError):
            return Response(status_code=200)

        # 1. Whitelist + role - Cache-Aside
        user_role = await get_user_role(phone)
        if user_role is None:
            return Response(status_code=200)

        # 2. Rate limiting
        if not await check_rate_limit(phone):
            return Response(status_code=200)

        # 3. Message type filter and interactive parsing
        if msg_type == "interactive":
            interactive = msg_data.get("interactive", {})
            if interactive.get("type") == "button_reply":
                interactive_payload = {
                    "type": "button_reply",
                    "id": interactive["button_reply"]["id"],
                    "title": interactive["button_reply"]["title"],
                }
            elif interactive.get("type") == "list_reply":
                interactive_payload = {
                    "type": "list_reply",
                    "id": interactive["list_reply"]["id"],
                    "title": interactive["list_reply"]["title"],
                    "description": interactive["list_reply"].get("description", ""),
                }
            else:
                return Response(status_code=200) # Unknown interactive type
                
            text = interactive_payload["title"] # Text fallback
            message_type = "interactive"
            
        elif msg_type == "text":
            interactive_payload = None
            text = msg_data.get("text", {}).get("body", "").strip()
            message_type = "text"
            
        else:
            await send_whatsapp_message(phone, "No momento, só consigo entender mensagens de texto e botões interativos.")
            return Response(status_code=200)

        # 3.1. Open service window (24h)
        redis = await get_redis()
        await redis.set(f"service_window:{phone}", "1", ex=86400)

        # 4. Command routing (only for text messages)
        if message_type == "text" and text.startswith("/"):
            if user_role != "teacher":
                await send_whatsapp_message(phone, "Comando não autorizado.")
                return Response(status_code=200)
                
            await service_bus.enqueue("command_messages", {
                "phone": phone,
                "text": text,
                "message_id": msg_id
            })
            return Response(status_code=200)

        # 5. Idempotency lock
        lock_key = f"webhook:{msg_id}"
        is_new = await redis.set(lock_key, "1", nx=True, ex=300)
        if not is_new:
            return Response(status_code=200)

        # 6. Enqueue to Inbound Messages with session FIFO
        correlation_id = str(uuid4())
        await service_bus.enqueue(
            queue="inbound_messages",
            payload={
                "phone": phone,
                "text": text,
                "message_type": message_type,
                "interactive_payload": interactive_payload,
                "correlation_id": correlation_id,
                "message_id": msg_id
            },
            session_id=phone
        )

        return Response(status_code=200)

    except Exception as e:
        track_event("webhook_error", {"error": str(e)})
        # Best effort error response to user
        try:
            # Assumes 'phone' is bound in locals if it failed late
            if 'phone' in locals():
                await send_whatsapp_message(phone, "Ocorreu um erro ao processar sua mensagem, tente novamente.")
        except Exception:
            pass
        
        return Response(status_code=200)

```

<!-- FILE_PATH: src/webhook/main.py -->
##### 📄 `src/webhook/main.py`

```py
from fastapi import FastAPI
from contextlib import asynccontextmanager

from src.shared.config import settings
from src.shared.telemetry import setup_telemetry, track_event
from src.shared.service_bus import service_bus
from src.db.database import init_db
from src.webhook.handlers import router as webhook_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup structlog
    setup_telemetry(settings.LOG_LEVEL)
    track_event("app_startup", {"environment": settings.ENVIRONMENT})
    
    # Init DB tables (for local dev SQLite)
    await init_db()
    
    yield
    
    # Teardown
    await service_bus.close()
    track_event("app_shutdown")

app = FastAPI(
    title="WhatsApp Teacher Assistant Edge",
    lifespan=lifespan,
    docs_url=None, # Disable Swagger in production
    redoc_url=None
)

app.include_router(webhook_router)

@app.get("/health")
async def health_check():
    return {"status": "ok"}

```

<!-- FILE_PATH: src/webhook/security.py -->
##### 📄 `src/webhook/security.py`

```py
import hmac
import hashlib
from fastapi import Request
from src.shared.config import settings
from src.cache.redis_client import get_redis

async def verify_meta_signature(body: bytes, signature: str) -> bool:
    """
    Validates HMAC-SHA256 signature from Meta to prevent forged requests.
    """
    if settings.ENVIRONMENT in ["development", "dev"]:
        return True
        
    if not signature:
        return False
        
    expected = "sha256=" + hmac.new(
        settings.META_APP_SECRET.encode('utf-8'), 
        body, 
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(signature, expected)

async def check_rate_limit(phone: str, limit: int = 60, window: int = 60) -> bool:
    """
    Returns True if within limit; False if exceeded.
    Default: 60 requests per minute per phone.
    """
    redis = await get_redis()
    rate_key = f"rate:{phone}"
    
    count = await redis.incr(rate_key)
    if count == 1:
        await redis.expire(rate_key, window)
        
    return count <= limit

```

#### 📁 `src/workers`

<!-- FILE_PATH: src/workers/__init__.py -->
##### 📄 `src/workers/__init__.py`

```py

```

<!-- FILE_PATH: src/workers/ingestion_worker.py -->
##### 📄 `src/workers/ingestion_worker.py`

```py
import asyncio
import io
import uuid
import structlog
from datetime import datetime
from sqlalchemy import text
from src.shared.config import settings
from src.db.database import AsyncSessionLocal
from src.drive.client import drive_client
from src.rag.qdrant_client import get_qdrant, init_qdrant_collection
from src.llm.factory import get_embedder
from qdrant_client.models import PointStruct
from azure.core.credentials import AzureKeyCredential
from azure.ai.documentintelligence.aio import DocumentIntelligenceClient
from langchain_text_splitters import RecursiveCharacterTextSplitter
import src.llm # Init providers

logger = structlog.get_logger(__name__)

async def process_pdf(file_id: str, file_name: str, teacher_id: str, course_id: str):
    """
    Downloads a PDF from Google Drive, parses it with Azure DI, 
    embeds chunks with Gemini, and upserts to Qdrant.
    """
    logger.info(f"Starting ingestion for file: {file_name}")
    
    # 1. Download from Google Drive
    try:
        pdf_bytes = await drive_client.download_file(file_id)
        logger.info(f"Downloaded {len(pdf_bytes)} bytes from Drive.")
    except Exception as e:
        logger.error(f"Failed to download from Drive", error=str(e))
        return False
        
    # 2. Parse PDF with Azure Document Intelligence
    endpoint = settings.DOCUMENT_INTELLIGENCE_ENDPOINT
    key = settings.DOCUMENT_INTELLIGENCE_KEY
    if not endpoint or not key:
        logger.error("Azure Document Intelligence credentials missing.")
        return False
        
    try:
        async with DocumentIntelligenceClient(
            endpoint=endpoint, credential=AzureKeyCredential(key)
        ) as document_intelligence_client:
            
            # We need to pass the bytes to the Azure API
            poller = await document_intelligence_client.begin_analyze_document(
                "prebuilt-layout", 
                body=pdf_bytes
            )
            result = await poller.result()
            full_text = result.content
            logger.info(f"Extracted {len(full_text)} characters using Azure DI.")
    except Exception as e:
        logger.error("Azure Document Intelligence parsing failed", error=str(e))
        return False
        
    # 3. Simple Chunking Strategy (split by double newline/paragraphs)
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,       # Máximo de caracteres por bloco
        chunk_overlap=200,     # Sobreposição para não cortar ideias no meio
        separators=["\n\n", "\n", ".", " ", ""] # Tenta quebrar por parágrafo, depois frase, depois palavra
    )
    
    paragraphs = text_splitter.split_text(full_text)
    logger.info(f"Chunked document into {len(paragraphs)} chunks.")
        
    # 4. Generate Embeddings with Gemini
    embedder = get_embedder("gemini")
    logger.info("Generating embeddings...")
    try:
        embeddings = await embedder.aembed_documents(paragraphs)
    except Exception as e:
        logger.error("Embedding generation failed", error=str(e))
        return False
        
    # 5. Upsert to Qdrant
    qdrant = await get_qdrant()
    points = []
    
    # We default lesson_id to 'lesson_1' for this generic ingestion
    # Real-world apps might infer lesson_id from the folder structure or file name
    for chunk, vector in zip(paragraphs, embeddings):
        points.append(
            PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "teacher_id": teacher_id,
                    "course_id": course_id,
                    "lesson_id": "lesson_1", 
                    "content": chunk,
                    "source": file_name
                }
            )
        )
        
    await qdrant.upsert(
        collection_name="documents",
        points=points
    )
    
    logger.info(f"✅ Successfully upserted {len(points)} vectors to Qdrant for {file_name}")
    return True

async def sync_google_drive():
    """Polls the Google Drive folder for new PDFs to ingest."""
    folder_id = settings.GOOGLE_DRIVE_FOLDER_ID
    if not folder_id:
        logger.warning("GOOGLE_DRIVE_FOLDER_ID is not set in config.")
        return
        
    logger.info(f"Checking Google Drive folder: {folder_id}")
    files = await drive_client.list_pdf_files(folder_id)
    
    if not files:
        logger.info("No PDF files found in the folder.")
        return
        
    await init_qdrant_collection("documents")
    
    async with AsyncSessionLocal() as session:
        # Get the first teacher and course available in DB for the sake of development
        result = await session.execute(text("SELECT id FROM Teachers LIMIT 1"))
        teacher_row = result.fetchone()
        if not teacher_row:
            logger.error("No Teacher found in DB to link documents.")
            return
            
        result = await session.execute(text("SELECT id FROM Courses WHERE teacher_id = :tid LIMIT 1"), {"tid": teacher_row[0]})
        course_row = result.fetchone()
        
        teacher_id = teacher_row[0]
        course_id = course_row[0]
        
        for f in files:
            file_id = f['id']
            file_name = f['name']
            
            # Check if we already processed this file successfully
            result = await session.execute(
                text("SELECT status FROM file_sync_state WHERE file_id = :fid"),
                {"fid": file_id}
            )
            row = result.fetchone()
            
            if row and row[0] == 'concluído':
                logger.debug(f"File {file_name} already processed. Skipping.")
                continue
                
            # Upsert state as 'processando'
            await session.execute(
                text("""
                    INSERT INTO file_sync_state (file_id, teacher_id, status, processing_started_at)
                    VALUES (:fid, :tid, 'processando', CURRENT_TIMESTAMP)
                    ON CONFLICT(file_id) DO UPDATE SET status='processando', processing_started_at=CURRENT_TIMESTAMP
                """),
                {"fid": file_id, "tid": teacher_id}
            )
            await session.commit()
            
            success = await process_pdf(file_id, file_name, teacher_id, course_id)
            
            new_status = 'concluído' if success else 'falhou'
            await session.execute(
                text("UPDATE file_sync_state SET status = :status, last_synced = CURRENT_TIMESTAMP WHERE file_id = :fid"),
                {"status": new_status, "fid": file_id}
            )
            await session.commit()

async def worker_loop():
    logger.info("Starting Ingestion Worker loop...")
    while True:
        try:
            await sync_google_drive()
        except Exception as e:
            logger.error("Error in ingestion sync loop", error=str(e))
            
        # Poll every 5 minutes (300 seconds)
        await asyncio.sleep(300)

if __name__ == "__main__":
    asyncio.run(worker_loop())

```

<!-- FILE_PATH: src/workers/llm_worker.py -->
##### 📄 `src/workers/llm_worker.py`

```py
import asyncio
import json
import structlog
from azure.servicebus.aio import ServiceBusClient, AutoLockRenewer
from src.shared.config import settings
from src.shared.telemetry import track_event, logger
from src.cache.debounce import drain_buffer
from src.shared.whatsapp_interactive import send_whatsapp_message, send_whatsapp_typing

# RAG pipeline with conversational memory
async def run_rag_pipeline(student_context: dict, phone: str, text: str) -> str:
    """Executes the LLM pipeline with Redis history and Qdrant RAG."""
    from src.llm.factory import get_provider
    from src.cache.session_history import OptimizedRedisChatMessageHistory
    from src.rag.retriever import retrieve_context
    from src.rag.prompt_builder import build_system_prompt
    from langchain_core.messages import HumanMessage, AIMessage
    
    llm = get_provider("gemini")
    
    # 1. Extract context variables
    teacher = student_context["teacher"]
    student = student_context["student"]
    course = student_context["course"]
    enrollment = student_context["enrollment"]
    
    # 2. Retrieve Document Context from Qdrant
    # For now, we assume the student only has access to their current lesson pointer
    # In a real app, this list would contain all lessons up to the pointer
    allowed_lesson_ids = [enrollment.lesson_id] 
    
    rag_context = await retrieve_context(
        teacher_id=teacher.id,
        course_id=course.id,
        allowed_lesson_ids=allowed_lesson_ids,
        query=text
    )
    
    # 3. Build the System Prompt
    system_message = await build_system_prompt(
        teacher=teacher,
        student=student,
        course_id=course.id,
        course_name=course.name,
        current_lesson_id=enrollment.lesson_id
    )
    
    # Append the RAG Context to the System Message
    if rag_context:
        system_message += f"\n\nCONTEXTO RECUPERADO DOS MATERIAIS DA AULA:\n{rag_context}"
    else:
        system_message += "\n\n(Nenhum contexto específico encontrado nos materiais para esta pergunta.)"
    
    # 4. Load conversation history from Redis (last 40 messages)
    history = OptimizedRedisChatMessageHistory(session_id=phone)
    previous_messages = await history.aget_messages()
    
    # Call LLM with full conversational context
    response = await llm.agenerate(
        prompt=text,
        system_message=system_message,
        history=previous_messages
    )
    
    # Persist both the student's question and the AI's answer to Redis
    await history.aadd_messages([
        HumanMessage(content=text),
        AIMessage(content=response)
    ])
    
    return response

async def get_student_context_by_phone(phone: str):
    """Fetches real DB data: Student, Enrollment, Course, and Teacher."""
    from src.db.database import AsyncSessionLocal
    from src.db.models import Student, StudentEnrollment, Course, Teacher
    from sqlalchemy import select
    
    async with AsyncSessionLocal() as session:
        # Join tables to get the full context in one query
        stmt = (
            select(Student, StudentEnrollment, Course, Teacher)
            .join(StudentEnrollment, Student.id == StudentEnrollment.student_id)
            .join(Course, StudentEnrollment.course_id == Course.id)
            .join(Teacher, Course.teacher_id == Teacher.id)
            .where(Student.phone == phone)
        )
        result = await session.execute(stmt)
        row = result.first()
        
        if not row:
            return None
            
        student, enrollment, course, teacher = row
        
        return {
            "student": student,
            "enrollment": enrollment,
            "course": course,
            "teacher": teacher
        }

async def send_whatsapp_typing(phone: str):
    """Sends typing indicator."""
    pass

async def handle_quiz_response(
    phone: str,
    student_id: str,
    reply_id: str,
    interactive_payload: dict,
) -> None:
    """Valida resposta do aluno a um quiz por comparação direta (if/else)."""
    # Parse do reply_id: "quiz_{quiz_id}_{answer_id}" → quiz_id, answer_id
    parts = reply_id.split("_", 2)
    if len(parts) < 3:
        await send_whatsapp_message(phone, "Resposta inválida.")
        return
    
    quiz_id = parts[1]
    answer_id = parts[2]

    # Busca o quiz pendente
    from src.db.database import AsyncSessionLocal
    from sqlalchemy import text
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
                SELECT quiz_id, correct_answer, explanation, question_text, status
                FROM PendingQuizzes
                WHERE quiz_id = :quiz_id AND student_id = :student_id
            """),
            {"quiz_id": quiz_id, "student_id": student_id}
        )
        quiz = result.fetchone()

        if not quiz:
            await send_whatsapp_message(phone, "Este quiz não foi encontrado ou já expirou.")
            return

        if quiz.status != "pending":
            await send_whatsapp_message(phone, "Você já respondeu este quiz.")
            return

        # ── VALIDAÇÃO DETERMINÍSTICA — if/else puro, sem LLM ──
        is_correct = (answer_id == quiz.correct_answer)

        # Atualiza o registro do quiz
        await session.execute(
            text("""
                UPDATE PendingQuizzes
                SET student_answer = :answer, is_correct = :correct,
                    status = 'answered', answered_at = CURRENT_TIMESTAMP
                WHERE quiz_id = :quiz_id
            """),
            {"answer": answer_id, "correct": is_correct, "quiz_id": quiz_id}
        )
        await session.commit()

    # Monta feedback com emoji visual
    if is_correct:
        feedback = f"✅ *Correto!*\n\n{quiz.explanation}"
    else:
        feedback = (
            f"❌ *Incorreto.* A resposta correta era: *{quiz.correct_answer}*\n\n"
            f"{quiz.explanation}"
        )

    await send_whatsapp_message(phone, feedback)

    # Telemetria
    track_event("quiz_answered", {
        "student_id": student_id,
        "quiz_id": quiz_id,
        "is_correct": is_correct,
        "answer": answer_id,
    })

async def process_inbound(receiver, message):
    try:
        body_bytes = b"".join(message.body)
        event = json.loads(body_bytes.decode('utf-8'))
    except Exception as e:
        await receiver.dead_letter_message(message, reason="Invalid JSON", error_description=str(e)[:500])
        return

    phone = event.get("phone")
    text = event.get("text")
    message_type = event.get("message_type", "text")
    interactive_payload = event.get("interactive_payload")
    correlation_id = event.get("correlation_id", "unknown")
    
    with structlog.contextvars.bound_contextvars(correlation_id=correlation_id, phone=phone):
        student_context = await get_student_context_by_phone(phone)

        # Guard clause: student may have been deactivated or not enrolled
        if not student_context:
            await send_whatsapp_message(phone, "Sua conta não está ativa ou você não está matriculado neste curso. Entre em contato com o suporte.")
            await receiver.complete_message(message)
            return

        # Fast-path: resposta interativa de quiz (validação determinística)
        if message_type == "interactive" and interactive_payload:
            reply_id = interactive_payload.get("id", "")
            if reply_id.startswith("quiz_"):
                await handle_quiz_response(phone, student_context["student"].id, reply_id, interactive_payload)
                await receiver.complete_message(message)
                return

        # Debouncing no worker (movido do webhook)
        from src.cache.redis_client import get_redis
        redis = await get_redis()
        
        buffer_key = f"buffer:{phone}"
        user_key = f"debounce:{phone}"

        await redis.rpush(buffer_key, text)
        await redis.expire(buffer_key, 10)

        is_first = await redis.set(user_key, "1", nx=True, ex=settings.DEBOUNCE_TTL_SECONDS)
        if not is_first:
            # Another message is currently counting down the debounce window
            # We don't complete this message, it gets rolled into the drain
            await receiver.complete_message(message)
            return

        # Wait the debounce window
        await asyncio.sleep(settings.DEBOUNCE_TTL_SECONDS)

        # Drain the buffer atomically
        messages = await drain_buffer(phone, cooldown=1)
        combined = " ".join(messages)

        if not combined or len(combined) > settings.MAX_BUFFER_CHARS:
            if combined:
                await send_whatsapp_message(phone, "Sua mensagem é muito longa. Por favor, seja mais conciso.")
            await receiver.complete_message(message)
            return

        # Background lock renewal
        async def renew_lock_periodically(rcv, msg, interval=60):
            while True:
                await asyncio.sleep(interval)
                try:
                    await rcv.renew_message_lock(msg)
                except Exception:
                    break

        lock_task = asyncio.create_task(renew_lock_periodically(receiver, message))

        try:
            # Typing indicator sent from Worker for precise timing
            await send_whatsapp_typing(phone)
            
            # Execute heavy LLM/RAG pipeline
            response = await run_rag_pipeline(student_context, phone, combined)
            
            await send_whatsapp_message(phone, response)
            await receiver.complete_message(message)
            track_event("llm_pipeline_success")
            
        except Exception as e:
            track_event("worker_error", {"error": str(e)[:500]})
            await send_whatsapp_message(phone, "Enfrentei um problema técnico para gerar sua resposta. Por favor, tente novamente.")
            await receiver.dead_letter_message(message, reason="Pipeline Error", error_description=str(e)[:500])
        finally:
            lock_task.cancel()

async def main_worker():
    """Main loop for the LLM worker consuming inbound_messages queue."""
    from src.shared.service_bus import service_bus
    from azure.servicebus import NEXT_AVAILABLE_SESSION
    from azure.servicebus.exceptions import OperationTimeoutError
    import asyncio
    import os
    
    is_dev = os.getenv("ENVIRONMENT", "development") in ("dev", "development")
    
    client = await service_bus.get_client()
        
    async with client:
        while True:
            try:
                if is_dev:
                    # Dev mode: plain receiver (emulator doesn't support sessions)
                    receiver = client.get_queue_receiver(
                        queue_name="inbound_messages",
                        max_wait_time=10
                    )
                else:
                    # Production: session-enabled FIFO per student
                    receiver = client.get_queue_receiver(
                        queue_name="inbound_messages", 
                        session_id=NEXT_AVAILABLE_SESSION,
                        max_wait_time=10
                    )
                async with receiver:
                    messages = await receiver.receive_messages(max_message_count=10, max_wait_time=5)
                    for msg in messages:
                        await process_inbound(receiver, msg)
            except OperationTimeoutError:
                # No active sessions available, just loop and wait
                await asyncio.sleep(2)
            except Exception as e:
                if "amqp:socket-error" in str(e).lower() or "connection closed" in str(e).lower():
                    print("[WAIT] Service Bus Emulator starting up... retrying in 10s...")
                    await asyncio.sleep(10)
                else:
                    print(f"Error in main worker loop: {e}")
                    await asyncio.sleep(5)

if __name__ == "__main__":
    import structlog
    asyncio.run(main_worker())

```

<!-- FILE_PATH: src/workers/micro_learning.py -->
##### 📄 `src/workers/micro_learning.py`

```py
import json
from uuid import uuid4
from datetime import datetime, timezone, timedelta
from src.db.database import AsyncSessionLocal
from sqlalchemy import text

from src.rag.quiz_generator import QuizOutput
from src.shared.whatsapp_interactive import send_reply_buttons, send_list_message

async def send_quiz_to_student(student: dict, quiz: QuizOutput) -> str:
    """Salva o quiz no banco e envia ao aluno via mensagem interativa.
    
    Retorna o quiz_id para rastreamento.
    """
    quiz_id = str(uuid4())[:8]  # ID curto para caber no reply_id do WhatsApp
    
    # Persiste no banco para validação futura
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""
                INSERT INTO PendingQuizzes
                    (quiz_id, student_id, course_id, lesson_id, question_text,
                     alternatives, correct_answer, explanation, expires_at)
                VALUES
                    (:quiz_id, :student_id, :course_id, :lesson_id, :question,
                     :alternatives, :correct_answer, :explanation, :expires_at)
            """),
            {
                "quiz_id": quiz_id,
                "student_id": student["id"],
                "course_id": student["course_id"],
                "lesson_id": student["lesson_pointer"],
                "question": quiz.question,
                "alternatives": json.dumps([{"id": a.id, "text": a.text} for a in quiz.alternatives]),
                "correct_answer": quiz.correct_answer,
                "explanation": quiz.explanation,
                "expires_at": datetime.now(timezone.utc) + timedelta(hours=24),
            }
        )
        await session.commit()

    phone = student["phone"]

    # Monta e envia mensagem interativa
    # Regra: <=3 alternativas -> Reply Buttons, >3 -> List Message
    if len(quiz.alternatives) <= 3:
        buttons = [
            {"id": f"quiz_{quiz_id}_{alt.id}", "title": f"{alt.id}) {alt.text[:17]}"}
            for alt in quiz.alternatives
        ]
        await send_reply_buttons(
            phone=phone,
            body_text=f"📝 *Quiz de Revisão*\n\n{quiz.question}",
            buttons=buttons,
            footer="Clique na resposta correta",
        )
    else:
        rows = [
            {
                "id": f"quiz_{quiz_id}_{alt.id}",
                "title": f"Opção {alt.id}",
                "description": alt.text[:72],  # máximo 72 chars na descrição
            }
            for alt in quiz.alternatives
        ]
        await send_list_message(
            phone=phone,
            body_text=f"📝 *Quiz de Revisão*\n\n{quiz.question}",
            button_text="Ver opções",
            sections=[{"title": "Alternativas", "rows": rows}],
            footer="Selecione a resposta correta",
        )

    return quiz_id

```

