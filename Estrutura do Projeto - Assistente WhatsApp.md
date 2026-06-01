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
  workers/           ← LLM, Ingestão, Exclusão, Command, Scheduler
  rag/               ← pipeline, prompt_builder, retriever, review_generator, quiz_generator
  llm/               ← base, factory, providers (Gemini, DeepSeek)
  db/                ← models, repositories, migrations
  cache/             ← Redis abstractions (debounce, whitelist, session)
  drive/             ← Google Drive sync, Google Sheets sync (spreadsheet_sync.py), Service Account
  shared/            ← config, telemetry, whatsapp_interactive
    constants/       ← messages.py (UX Copy), prompts.py (System/Review prompts), commands.py (Teacher commands)
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

## **5\. Estrutura de Pastas no Google Drive e Planilha de Cadastro**

### **5.1 Organização de Materiais por Pastas**

Cada pasta de nível superior no Google Drive do professor representa um **curso**. Dentro de cada curso, as subpastas numeradas representam as **aulas**. Os arquivos de cada subpasta são os materiais didáticos daquela aula específica.

```
📁 Google Drive (raiz compartilhada)
├── 📁 Inteligência Artificial/
│   ├── 📁 1 - Introdução ao Python/
│   │   └── apostila_do_minicurso_de_machine_learning.pdf
│   ├── 📁 2 - Agentes IA/
│   │   └── AI_Agents_Apostila.pdf
│   └── 📁 3 - Desenvolvimento Ágil/
│       └── capitulo_03_desenvolvimentoAgilDeSoftware.pptx    ← convertido automaticamente
├── 📁 PIBEP/
│   ├── 📁 1 - Abertura/
│   └── 📁 2 - Tendências/
└── 📊 Cadastro de Alunos (Google Sheets)
```

O número no início do nome da subpasta (é extraído por regex `^(\d+)`) define o `lesson_number` do material. Formatos suportados: PDF, PPTX, DOCX, TXT, MD.

A integração é feita via **Service Account**, sem depender do login do professor. A sincronização de materiais é disparada via comando `/sinc_materiais` no WhatsApp.

### **5.2 Planilha de Cadastro (Google Sheets)**

A planilha funciona como **fonte de verdade** para alunos, professores, cursos e agendamentos. O sistema lê a planilha e atualiza o banco de dados local automaticamente via comando `/sinc_alunos`.

**Estrutura obrigatória das colunas (A até H):**

| Coluna A | Coluna B | Coluna C | Coluna D | Coluna E | Coluna F | Coluna G | Coluna H |
|----------|----------|----------|----------|----------|----------|----------|----------|
| Nome | Telefone | Role | Curso | Horário 1 | Horário 2 | Horário 3 | Status |

**Regras de preenchimento:**
- **Role:** `Professor` ou `Aluno`. Professores têm acesso aos comandos `/` no WhatsApp.
- **Curso:** Nome da pasta do Google Drive correspondente. Deixar vazio para professores.
- **Horários:** Formato `HH:MM` (ex: `07:00`, `12:00`, `18:00`). Até 3 triggers diários por aluno. Deixar vazio para professores.
- **Status:** `Ativo` ou `Inativo`. Alunos inativos não recebem revisões.
- **Mesmo aluno em múltiplos cursos:** Basta repetir a linha com o novo curso e horários.

**Comportamento da sincronização (`/sinc_alunos`):**
1. Lê a planilha via Google Sheets API (Conta de Serviço com escopo `spreadsheets.readonly`).
2. Upsert de Professores → tabela `Teachers`.
3. Upsert de Alunos → tabela `Students`.
4. Upsert de Cursos → tabela `Courses` (nome da pasta = ID do curso).
5. Upsert de Matrículas → tabela `StudentEnrollment`.
6. Upsert de Agendamentos → tabela `StudentSchedule`.
7. **Limpeza automática:** Remove matrículas e agendamentos que não constam mais na planilha.

### **5.3 Apps Script para Filtro de Pastas (opcional)**

Um script no Google Apps Script pode ser configurado na planilha para gerar uma lista dinâmica de pastas (cursos) disponíveis no Drive, criando um filtro (dropdown) na coluna "Curso" da planilha. Isso garante que o professor não cometa erros de digitação no nome do curso.

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

### **Filtro de contexto no RAG — Retriever de 2 Estágios**

A query ao Qdrant filtra por `lesson_number` até a posição atual do ponteiro (inclusive), e por `teacher_id` + `course_id`.

**Problema resolvido:** Queries genéricas como *"tópicos da aula 3"* retornavam conteúdo semanticamente similar mas de aulas erradas (ex: Aula 1 e 2), fazendo o LLM acionar "Fora do escopo" mesmo com conteúdo da Aula 3 indexado.

**Solução implementada — Retriever de 2 estágios (`src/rag/retriever.py`):**

1. **Estágio 1 (Pinpointed):** Se o aluno menciona uma aula específica (regex detecta "aula N", "lição N", "módulo N", "capítulo N"), busca **exclusivamente** com `lesson_number = N` (match exato, não range).
2. **Estágio 2 (Broad fallback):** Se o pinpointed retorna menos de 3 resultados, complementa com busca ampla (`lesson_number <= current_lesson_number`). Resultados do pinpointed são priorizados.

**Informação de aulas liberadas no System Prompt:** O System Prompt informa explicitamente ao LLM quantas aulas o aluno possui acesso: *"O aluno possui acesso aos materiais da Aula 1 até a Aula {current_lesson_number}"*. Essa informação vem diretamente do banco de dados (tabela `CourseClass.current_lesson_number`), sem depender do RAG.

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
### **Pipeline de Ingestão Multi-Formato**

O Worker de Ingestão (`src/workers/ingestion_worker.py`) implementa um roteador de formatos que converte qualquer arquivo suportado para texto antes de indexar no Qdrant:

| **Formato** | **Pipeline** | **Custo Azure DI** |
| PDF (texto/OCR) | Azure Document Intelligence (layout) | Sim |
| PPTX, DOCX | Gotenberg (LibreOffice headless) → PDF → Azure DI | Sim |
| TXT, MD | Extração direta de texto puro | Não |

**Conversão via Gotenberg:** O microserviço Gotenberg (Docker) converte PPTX e DOCX para PDF usando LibreOffice headless. O arquivo convertido é temporário — apenas o original é armazenado no Google Drive. A URL do Gotenberg é configurada via `GOTENBERG_URL` no `.env`.

**Compressão FinOps 2-Up (slides):** Quando o PDF convertido de uma apresentação tem formato paisagem, o sistema utiliza PyMuPDF para empilhar 2 slides verticalmente em uma única página A4 (sem redimensionamento). A Azure Document Intelligence cobra por página, portanto essa técnica **reduz o custo pela metade** sem degradar a qualidade do OCR. A compressão é aplicada automaticamente apenas para PDFs com páginas mais largas do que altas.

**Dependências adicionais:** `python-pptx`, `python-docx`, `PyMuPDF` (fitz), Gotenberg (Docker).

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

### **9.3.1 Roteamento de Comandos do Professor**

Os comandos do professor são definidos centralizadamente em `src/shared/constants/commands.py`, que mapeia aliases (variantes de digitação) para tipos de comando internos. Isso permite adicionar novos comandos sem alterar o webhook.

**Arquivo: `src/shared/constants/commands.py`**

```python
class CommandType:
    SYNC_STUDENTS = "sync_students"
    SYNC_MATERIALS = "sync_materials"
    HELP = "help"
    LIST_COURSES = "list_courses"
    LIST_MATERIALS = "list_materials"
    UPDATE_LESSON = "update_lesson"
    UNKNOWN = "unknown"

# Aliases suportados (aceita variantes e erros de digitação comuns)
COMMAND_ALIASES = {
    CommandType.SYNC_STUDENTS: [
        "/sinc_alunos", "/sync_alunos", "/sincronizar_alunos", "/atualizar_alunos"
    ],
    CommandType.SYNC_MATERIALS: [
        "/sinc_materiais", "/sync_materiais", "/sincronizar_materiais", "/atualizar_materiais",
        "/sinc_material", "/sync_material"
    ],
    CommandType.HELP: [
        "/ajuda", "/help", "/comandos"
    ],
    CommandType.LIST_COURSES: [
        "/listar_cursos", "/list_cursos", "/cursos"
    ],
    CommandType.LIST_MATERIALS: [
        "/listar_materiais", "/list_materiais", "/materiais"
    ],
    CommandType.UPDATE_LESSON: [
        "/aula"
    ]
}

def parse_command(text: str) -> tuple[str, str]:
    text_lower = text.strip().lower()
    for cmd_type, aliases in COMMAND_ALIASES.items():
        for alias in aliases:
            if text_lower.startswith(alias):
                args = text[len(alias):].strip()
                return cmd_type, args
    return CommandType.UNKNOWN, ""
```

**Textos de resposta (UX Copy)** — centralizados em `src/shared/constants/messages.py`:

```python
CMD_SYNC_ALUNOS_START = "⏳ Iniciando sincronização da planilha..."
CMD_SYNC_MATERIAIS_START = "⏳ Iniciando download e processamento. O robô vai ler os arquivos e criar os vetores..."
CMD_SYNC_MATERIAIS_SUCCESS = "✅ *Ingestão Concluída!* Documentos: {docs_before} ➡️ {docs_after}"
CMD_SYNC_SUCCESS = "✅ *Sincronização Concluída!* Professores: {teachers_before}→{teachers_after} | Alunos: {students_before}→{students_after} | Cursos: {courses_before}→{courses_after}"
CMD_SYNC_ERROR = "❌ Erro na sincronização: {error}"
```

**Fluxo no Webhook (`handlers.py`):**

```python
from src.shared.constants.commands import parse_command, CommandType

# Dentro do handler, após validar role == "teacher":
command, args = parse_command(text)
if command == CommandType.SYNC_STUDENTS:
    background_tasks.add_task(run_sync_alunos_command, phone)
    await send_whatsapp_message(phone, CMD_SYNC_ALUNOS_START)
    return Response(status_code=200)
elif command == CommandType.SYNC_MATERIALS:
    background_tasks.add_task(run_sync_materiais_command, phone)
    await send_whatsapp_message(phone, CMD_SYNC_MATERIAIS_START)
    return Response(status_code=200)
elif command == CommandType.LIST_COURSES:
    background_tasks.add_task(run_list_cursos_command, phone)
    return Response(status_code=200)
elif command == CommandType.UPDATE_LESSON:
    background_tasks.add_task(run_update_lesson_command, phone, args)
    return Response(status_code=200)
```

O `/sinc_alunos` e `/sinc_materiais` são executados via `BackgroundTasks` do FastAPI (não enfileirados no Service Bus), pois precisam responder diretamente ao professor com o relatório.

**Comandos futuros** devem ser adicionados apenas em `commands.py` (aliases) e no bloco `if/elif` do webhook — sem alterar nenhuma outra parte do sistema.

**Por que fila separada para outros comandos?** O webhook deve retornar HTTP 200 em menos de 200ms. O `/sync alunos` usa BackgroundTasks porque é uma operação síncrona rápida. Comandos mais pesados (como `/aula`) são enfileirados no Service Bus para processamento assíncrono.

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
| LLM (primário) | DeepSeek |  
| LLM (fallback) | Gemini |  
| Vision LLM (ingestão) | Gemini Vision ou GPT-4o |  
| Embedding | multilingual-e5 ou text-embedding-3-small (interface EmbeddingProvider separada) |  
| TTS | Azure Cognitive Services Speech |  
| Cache, locks, whitelist/role, debouncing, janela de serviço e histórico de chat | Azure Cache for Redis (redis.asyncio, persistência AOF) |  
| Conversão Office→PDF | Gotenberg (LibreOffice headless em Docker) |  
| Repositório de conteúdo | Google Drive (Service Account, estrutura hierárquica Curso/Aula/Arquivo) |  
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