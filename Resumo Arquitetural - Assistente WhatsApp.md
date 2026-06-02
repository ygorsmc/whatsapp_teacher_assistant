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
└── Enfileira → Service Bus               ← NUNCA executa lógica de negócio
        │
        ▼
[Service Bus]
├── inbound_messages  ┐
└── command_messages  ┴→ [Worker LLM]       ← RAG + comandos + memória Redis
├── index_documents   → [Worker Ingestão]  ← Gotenberg + 2-Up FinOps + Azure DI + Qdrant
└── delete_vectors    → [Worker Exclusão]  ← hard delete agendado para 03:00 BRT
        │
        ▼
[Scheduler — Timer Trigger a cada 5min]
└── Envia mensagem de abertura com botão → aluno clica → janela de 24h aberta
    └── Dentro da janela: quiz + revisão + RAG livre (sem custo adicional)
```

---

## Decisões de Engenharia

### Webhook retorna 200 OK imediatamente (< 50ms)

A Meta requer resposta em menos de 200ms. Qualquer lógica de negócio executada no webhook — incluindo consultas ao banco ou chamadas ao LLM — quebraria esse prazo e causaria reenvios automáticos da mensagem (flood de retries), resultando em comandos executados 5 vezes.

**Regras de Sobrevivência do Webhook:** 
1. **Cache de Conexão:** A conexão TCP/AMQP do ServiceBus é mantida em cache de memória via Singleton (`ServiceBusWrapper`).
2. **Timeouts Estritos:** O cliente Redis (usado para idempotência e rate limit) possui um `socket_timeout=1.0`. Se a rede interna oscilar, o webhook abandona a tentativa, usa um fallback em memória e retorna 200 OK, garantindo que o webhook **nunca passe de 200ms**.
3. **Idempotência (Lock):** O `msg_id` da Meta é persistido por 5 minutos via NX Lock.

### Debouncing e Concorrência no Worker

O design anterior usava `asyncio.sleep()` no webhook para aguardar mensagens rápidas. Isso foi movido para o Worker LLM. Além disso, o worker de comandos (`command_messages`) foi integrado ao `Worker LLM` (usando `asyncio.gather`), permitindo consumir as duas filas simultaneamente e reduzindo a pegada de containers no Azure.

### Janela de serviço de 24h — estratégia de custo

A Meta cobra por **conversa**, não por mensagem individual. O objetivo é abrir uma única janela por aluno por dia, concentrando toda a interação dentro dela.

**A primeira mensagem do dia é sempre um botão simples de aceite** — não um quiz. Isso reduz a barreira de interação ao mínimo: o aluno toca num botão, a janela abre, e todo o conteúdo pedagógico do dia (quiz, revisão, respostas a dúvidas) é entregue gratuitamente dentro da janela.

```
[Scheduler]
    ↓
Envia: "Pronto para sua revisão de hoje?"
       [ Sim, vamos lá! ]  [ Agora não ]
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

### Isolamento de dados por professor

Toda query SQL e toda busca vetorial inclui filtro por `teacher_id` e `course_id`. Ausência desses filtros é tratada como bug de segurança — não como comportamento aceitável.

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