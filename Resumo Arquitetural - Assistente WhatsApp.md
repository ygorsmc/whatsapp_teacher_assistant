# Resumo Arquitetural — Assistente de Ensino por WhatsApp

## Objetivo do Sistema

O assistente existe para combater a **curva do esquecimento**: quanto mais dias passam sem contato com o conteúdo, mais o aluno perde o que aprendeu. O sistema mantém o material presente na memória do aluno entre as aulas por meio de revisões e atividades diárias enviadas automaticamente pelo WhatsApp — sem exigir nenhum aplicativo adicional.

O professor, por sua vez, não precisa gerenciar uma plataforma: ele organiza os materiais no Google Drive e atualiza o progresso de cada aluno por comandos simples no WhatsApp.

---

## Visão Geral da Arquitetura

```
WhatsApp (Meta Cloud API)
        │
        ▼
[Webhook Receiver]                        ← retorna HTTP 200 em < 200ms
├── Validação de assinatura HMAC-SHA256   ← rejeita requests forjados
├── Whitelist + role (Redis → SQL)        ← "student" ou "teacher"
├── Rate limiting por telefone            ← proteção contra flood
├── Filtro de tipo (texto e botões)       ← outros tipos descartados
├── Abertura de janela de serviço         ← registra interação no Redis
├── Roteamento de comandos (/)            ← parse centralizado via commands.py
│   ├── /sinc_alunos     → BackgroundTask (lê Google Sheets → upsert no banco)
│   ├── /sinc_materiais  → BackgroundTask (Drive → Gotenberg → Azure DI → Qdrant)
│   ├── /listar_cursos   → lista cursos cadastrados do professor
│   ├── /listar_materiais → lista arquivos no Google Drive por curso
│   ├── /aula [turma] = N → atualiza ponteiro de aula da turma
│   └── /ajuda            → resposta imediata
├── Idempotência por message_id           ← evita processamento duplicado
└── Enfileira → IMessageQueue             ← NUNCA executa lógica de negócio
        │
        ▼
[Message Queue (Redis Streams / Service Bus)]
├── inbound_messages  ┐
└── command_messages  ┴→ [Worker LLM]       ← RAG + comandos + memória Redis
├── index_documents   → [Worker Ingestão]  ← Gotenberg + 2-Up FinOps + Azure DI + Qdrant
└── delete_vectors    → [Worker Exclusão]  ← hard delete agendado para 03:00 BRT
        │
        ▼
[Scheduler — Timer Trigger a cada 5min]
└── Janela Fechada (> 24h): Envia Template Message (Utility) com Payload Dinâmico → aluno clica → janela abre
└── Janela Aberta (< 24h): Envia Interactive Message (botões normais) direto com o Quiz (sem custo adicional)
    └── Dentro da janela: RAG livre, correção de exercícios e interação pedagógica contínua
```

---

## Decisões de Engenharia

### Webhook retorna 200 OK imediatamente (< 50ms)

A Meta requer resposta em menos de 200ms. Qualquer lógica de negócio executada no webhook — incluindo consultas ao banco ou chamadas ao LLM — quebraria esse prazo e causaria reenvios automáticos da mensagem (flood de retries), resultando em comandos executados 5 vezes.

**Regras de Sobrevivência do Webhook:** 
1. **Cache de Conexão:** A conexão com o provedor de fila (ex: Redis) é mantida em cache de memória via Singleton (`QueueFactory`).
2. **Timeouts Estritos:** O cliente Redis (usado para idempotência e rate limit) possui um `socket_timeout=1.0`. Se a rede interna oscilar, o webhook abandona a tentativa, usa um fallback em memória e retorna 200 OK, garantindo que o webhook **nunca passe de 200ms**.
3. **Idempotência (Lock):** O `msg_id` da Meta é persistido por 5 minutos via NX Lock.

### Debouncing e Concorrência no Worker

O design anterior usava `asyncio.sleep()` no webhook para aguardar mensagens rápidas. Isso foi movido para o Worker LLM. Além disso, o worker de comandos (`command_messages`) foi integrado ao `Worker LLM` (usando `asyncio.gather`), permitindo consumir as duas filas simultaneamente e reduzindo a pegada de containers no Azure.

### Telemetria e Janela de 24h no PostgreSQL

Para garantir resiliência e possibilitar futuros dashboards de Analytics, a verificação da Janela de 24h **não** usa mais o TTL do Redis. Toda interação (seja `message_received` ou `review_sent`) é registrada atomicamente na tabela `StudentInteractions` do PostgreSQL. O agendador consulta o `MAX(timestamp)` dessa tabela para decidir com precisão absoluta se a janela está aberta ou fechada. O Redis atua exclusivamente como Fast-Cache (Debounce e Rate Limit).

### A Arquitetura de Templates (Contorno do Bloqueio da Meta)

A API do WhatsApp (Graph API) bloqueia (HTTP 400) mensagens interativas (free-form) fora da janela de serviço. Para contornar isso:
1. O agendador utiliza `send_template_message` enviando um **Utility Template** oficial (pago).
2. Os botões de Quick Reply do template são injetados com um **Payload Dinâmico** (ex: `review_accept_crs_123`).
3. O Webhook foi expandido para reconhecer retornos do tipo `msg_type == "button"` (exclusivos de templates) e roteá-los para o Worker como se fossem cliques de mensagens interativas normais.

### Janela de serviço de 24h — estratégia de custo

A Meta cobra por **conversa**, não por mensagem individual. O objetivo é abrir uma única janela por aluno por dia, concentrando toda a interação dentro dela.

**A primeira mensagem do dia é sempre um botão simples de aceite** — não um quiz aberto. Isso reduz a barreira de interação ao mínimo: o aluno toca num botão (que no background aciona um payload rastreável para o backend), a janela abre, e todo o conteúdo pedagógico do dia (quiz, revisão, respostas a dúvidas) é entregue gratuitamente dentro da janela.

```
[Scheduler] (Tabela StudentInteractions)
    ↓
Janela Fechada? 
→ Envia Utility Template (Ex: "convite_revisao_quiz")
       [ Fazer Agora ]  [ Lembrar Mais Tarde ]
    ↓
Aluno toca em qualquer botão
    ↓
Janela de 24h aberta (gratuita)
    ↓
Quiz + revisão + RAG livre
```

**Impacto financeiro estimado (50 alunos):**

| Estratégia | Custo mensal estimado |
|---|---|
| Sem otimização (mensagem avulsa por dia) | ~R$ 375/mês |
| Com janela de 24h (Utility Template) | ~R$ 75/mês |
| **Economia** | **~80%** |

### Progresso do aluno é atualizado pelo professor

O avanço de aula não é automático por presença. O professor envia um comando pelo WhatsApp para atualizar a posição de qualquer turma:

```
/aula Turma 101 = 3
→ ✓ Turma atualizada para aula 3
```

O ponteiro da turma (`current_lesson_number`) controla quais aulas os alunos podem acessar. O System Prompt do assistente informa automaticamente: *"O aluno possui acesso aos materiais da Aula 1 até a Aula N"*.

### Google Drive como interface do professor

Não há painel de controle. O professor organiza tudo no Google Drive:

- Uma **planilha Google Sheets** com o cadastro de usuários (alunos e professores), incluindo nome, telefone, role, curso, horários de trigger e status
- **Pastas hierárquicas** com os materiais de cada curso, organizadas como: `Curso/ → Aula N/ → Arquivos`

```
📁 Google Drive (raiz compartilhada)
├── 📁 Inteligência Artificial/
│   ├── 📁 1 - Introdução/
│   │   └── apostila_ml.pdf
│   ├── 📁 2 - Agentes IA/
│   │   └── AI_Agents_Apostila.pdf
│   └── 📁 3 - Desenvolvimento Ágil/
│       └── capitulo_03.pptx          ← convertido automaticamente via Gotenberg
├── 📁 PIBEP/
│   ├── 📁 1 - Abertura/
│   └── 📁 2 - Tendências/
└── 📊 Cadastro de Alunos (Google Sheets)
```

O nome da pasta de nível superior é o identificador do curso. As subpastas numeradas (`N - Nome`) definem as aulas. O sistema sincroniza a planilha via `/sinc_alunos` e os materiais via `/sinc_materiais`.

### Pipeline de Ingestão Multi-Formato com FinOps

O Worker de Ingestão suporta múltiplos formatos de arquivo:

| Formato de entrada | Processamento |
|---|---|
| PDF (texto/OCR) | Direto para Azure Document Intelligence |
| PPTX, DOCX | Conversão para PDF via **Gotenberg** (LibreOffice headless) |
| TXT, MD | Extração de texto puro (sem Azure DI) |

**Compressão FinOps 2-Up (slides):** Quando o PDF convertido tem formato paisagem (slide), o sistema empilha 2 slides verticalmente em uma única página A4, sem redimensionamento. A Azure DI cobra por página, portanto essa técnica **reduz o custo pela metade** sem degradar a qualidade do OCR.

### Retriever de 2 Estágios

O retriever utiliza busca semântica com uma estratégia de dois estágios para lidar com queries genéricas:

1. **Pinpointed:** Se o aluno menciona uma aula específica ("aula 3", "lição 2", "capítulo 1"), busca **exclusivamente** naquela aula.
2. **Broad fallback:** Se os resultados pinpointed são insuficientes (<3), complementa com busca ampla em todas as aulas liberadas.

Isso resolve o problema de queries genéricas como *"tópicos da aula 3"* que, em busca semântica pura, retornavam conteúdo de outras aulas.

O sistema sincroniza automaticamente alunos novos, removidos ou com curso alterado.

### Separação de Interesses (Separation of Concerns)

Todos os textos de interface (UX Copy), prompts do LLM e comandos do professor são centralizados em arquivos dedicados:

| Arquivo | Responsabilidade |
|---|---|
| `src/shared/constants/messages.py` | Textos de interface do WhatsApp (convites, erros, feedbacks) |
| `src/shared/constants/prompts.py` | System Prompts e templates do LLM |
| `src/shared/constants/commands.py` | Mapeamento de comandos do professor (aliases → ações) |

Isso permite alterar o tom de voz do assistente, ajustar prompts ou adicionar novos comandos sem tocar na lógica de negócio.

### Validação de quiz sem LLM

O LLM gera a pergunta, as alternativas e a explicação em uma única chamada. O resultado é salvo no banco. Quando o aluno responde, a validação é uma comparação direta (`if/else`) — sem nova chamada ao LLM. Latência: < 50ms.

### Pipeline Two-Step de Geração de Quizzes (Variância)

Para evitar quizzes repetitivos e garantir a retenção do aluno (FinOps/LLMOps), a geração da revisão opera em dois passos:
1. **Extração de Tópicos (Agnóstica):** O sistema varre os chunks da aula usando uma chave genérica no Redis `{course_id}:{lesson_number}` (sem amarrar ao `teacher_id`, permitindo cache-hit cruzado entre turmas). A validade do cache é de 30 dias.
2. **Sorteio de Tópico e Lente Pedagógica:** Após extrair tópicos (sem limite rígido de quantidade), um deles é sorteado junto com uma "lente" (ex: caso prático, detalhe técnico, exceção à regra ou conceito central). O prompt final é injetado com essas diretrizes para forçar a IA a criar um quiz inédito a cada interação, maximizando o engajamento diário.

### Isolamento de dados por professor

Toda query SQL e toda busca vetorial inclui filtro por `teacher_id` e `course_id`. Ausência desses filtros é tratada como bug de segurança — não como comportamento aceitável.

### Estratégia de Atualização no Azure Container Apps (Deploy)

Nas instâncias Azure, a simples substituição da imagem por uma tag genérica (como `latest`) não forçará uma recriação da revisão pela plataforma. Para evitar falsos-positivos em publicações de código:
- Sempre injetar um "salt" de deploy nas env vars. No CI/CD, utiliza-se `--set-env-vars FORCE_UPDATE=$(date +%s)` no ACR update. Isso destrói o container anterior e reinicia com o novo digest físico da imagem `latest`.
- **Injeção de Variáveis (KEDA Bypass):** Variáveis sensíveis não encapsuladas no Key Vault (como `GOOGLE_SHEET_ID`) não devem depender de montagem local via `.env` em produção, pois falham na escalabilidade via KEDA. O script de deploy as injeta nativamente como ambiente do contêiner (`az containerapp update --set-env-vars`).

---

## Stack Tecnológica

| Camada | Tecnologia |
|---|---|
| Canal de mensagens | WhatsApp (Meta Cloud API) |
| API de recepção | FastAPI (async) |
| Orquestração RAG | LangChain |
| LLM | DeepSeek (primário), Gemini (fallback); abstração permite troca sem reindexação |
| Banco vetorial | Qdrant Cloud |
| Parse de documentos | Azure Document Intelligence |
| Conversão Office→PDF | Gotenberg (LibreOffice headless em Docker) |
| Fila de mensagens | Azure Service Bus (Standard — obrigatório para sessões FIFO) |
| Cache + memória | Container App Interno (`redis:7-alpine` Serverless) |
| Banco relacional | Azure SQL Database (tipo `DATETIME` via string ISO adaptada `%Y-%m-%dT%H:%M:%S`) |
| Armazenamento de arquivos | Google Drive (gerenciado pelo professor, estrutura hierárquica) |
| Triggers agendados | Azure Functions (Timer Trigger) |
| Segredos | Azure Key Vault |
| Logs e telemetria | Azure Application Insights |
| TTS (opcional) | Azure Cognitive Services Speech |

---

## Camada de Abstração de LLM

Toda chamada ao LLM passa por uma interface única (`LLMProvider`). Trocar o modelo ou o provedor é uma mudança de variável de ambiente — sem alteração de código e sem necessidade de reindexação, pois os providers de geração e de embeddings são interfaces separadas.

---

## Ambientes

| Ambiente | Banco Vetorial | Objetivo |
|---|---|---|
| Desenvolvimento local | Qdrant via Docker | Sem custo, sem risco de expiração |
| Testes de integração | Qdrant Cloud free tier | Valida conectividade com serviços Azure |
| Produção | Qdrant Cloud pago | Alta disponibilidade, mesma região |

---

## Regras que Não Podem Ser Violadas

- O webhook **nunca** chama o LLM nem executa lógica de negócio
- O debouncing fica no Worker, nunca no webhook
- Toda query deve filtrar por `teacher_id` **e** `course_id`
- O `lesson_id` é um identificador estável — nunca um índice posicional
- `ConversationBufferMemory` apenas — nunca `ConversationTokenBufferMemory`
- Segredos exclusivamente via Azure Key Vault — zero connection strings no código
- Hard delete de vetores no Qdrant somente às 03:00 BRT — nunca durante o horário de uso
- **PostgreSQL Strictness:** Tabelas sempre envoltas em aspas (`"Teachers"`), booleanos como `true`/`false` e datas usando instâncias de `datetime` puro (nunca strings).
- **Telegram Security:** O Webhook deve sempre validar o `X-Telegram-Bot-Api-Secret-Token` injetado via `/setWebhook`.