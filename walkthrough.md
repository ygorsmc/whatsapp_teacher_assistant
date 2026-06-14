# Walkthrough — Addon de Livros + Horizontes H1-H3 + RAG de Alta Precisão + Vertical de Idiomas

> **Atualização 2026-06-14:** a "Vertical de Ensino de Idiomas"
> (`_docs/prompt_execucao_idiomas.md`) foi implementada de ponta a ponta (F0-F9
> + perfil de idioma por turma/revisão por nível) — ver a seção "Vertical de
> Idiomas (F0-F9)" no final. Tudo atrás de flags OFF (mestre
> `LANGUAGE_MODE_ENABLED`); manual de operações dedicado em
> `_docs/docs/IDIOMAS_OPERATIONS.md`.
>
> **Atualização 2026-06-12 (2):** o roteiro "RAG de Alta Precisão"
> (`_docs/prompt_execucao_rag_preciso.md`) foi implementado — ver a seção
> "RAG de Alta Precisão (F0-F8)" no final. Tudo atrás de flags OFF.
>
> **Atualização 2026-06-12:** os Horizontes do plano de diferenciação foram
> implementados sobre o Addon. A seção "Horizontes H1-H3" no final deste
> documento descreve as camadas novas; a arquitetura base do Addon segue
> válida e inalterada.

Este documento explica a arquitetura do Addon que transforma o assistente de um
"tutor de aulas" em uma **plataforma de aprendizagem ativa para materiais
densos** (livros, apostilas), e como cada `material_type` se comporta de forma
diferente em cada estágio do pipeline.

---

## 1. Visão geral

```
Google Drive                Ingestão                 Qdrant
Curso/                      ┌──────────────┐         payload por vetor:
 └─ Disciplina/             │ ingestion_   │         { teacher_id, course_id,
     ├─ Aulas/    ── lesson─▶ worker       ├────────▶  subject_name,
     ├─ Livro/    ── book  ─▶ (fila +      │           lesson_number,   ◀── aula OU capítulo
     └─ Apostila/ ─ workbook▶  retry)      │           material_type }  ◀── NOVO
                            └──────┬───────┘
                                   │ invalida QuizBank + tópicos (escopado por material)
                                   ▼
                  ┌─────────────────────────────────────┐
                  │ QuizBank (cache semântico, agora     │
                  │ com material_type)                   │
                  └──┬─────────────┬─────────────┬──────┘
        check-in da  │   revisão   │   simulado  │   quiz clássico
        leitura (F3) │   espaçada  │   (F6.3)    │   de aula (inalterado)
                     ▼   (F4)      ▼             ▼
              ┌──────────────────────────────────────────┐
              │ scheduler_worker (loop de 5 min)          │
              │  + process_reading_plans()      (F2)      │
              │  + process_reading_checkins()   (F3)      │
              │  + process_spaced_repetition()  (F4)      │
              └──────────────────────────────────────────┘
```

**Módulos novos** (a lógica do Addon vive isolada; os workers existentes só
ganham hooks):

| Módulo | Responsabilidade |
|---|---|
| `src/workers/reading_journey.py` | Jornada de Leitura: sugestões, check-ins, portão, streak, replanejamento, RAG socrático |
| `src/workers/spaced_repetition.py` | Escada D+3/D+10/D+30 e reentrega das revisões |
| `src/workers/reading_commands.py` | Handlers dos comandos novos (professor + aluno) |
| `src/db/models/reading.py` | `ReadingPlans`, `SpacedRepetitionQueue`, `AnswerKeys`, `LessonChapterLinks`, `Erratas` |
| `alembic/versions/e5f6a7b8c9d0_*` | Migration de tudo acima (+3 colunas em tabelas existentes) |

---

## 2. Como cada `material_type` se comporta no pipeline

A coluna/campo `material_type` (`lesson` | `book` | `workbook`) atravessa o
sistema inteiro. Em cada estágio:

### 2.1 Drive → Ingestão

- **`lesson`** — duas formas equivalentes: pasta de aula solta direto na
  Disciplina (estrutura legada, ex. `1 - Introdução/`) OU dentro de uma pasta
  `Aulas/`. Nada muda para quem já usa o sistema.
- **`book` / `workbook`** — pastas `Livro/` e `Apostila/` dentro da
  Disciplina; cada subpasta é uma seção (`Cap 1`, `Módulo 2`...). O número é
  extraído pelo mesmo helper (`extract_section_number`), que aceita tanto o
  formato legado `"1 - Nome"` quanto `"Cap 3"`.
- `lesson_number` no payload é **sobrecondicionado pela semântica do
  material**: nº da aula para `lesson`, nº do capítulo/seção para os demais.

### 2.2 Qdrant (RAG)

- Vetores **legados** de aulas não têm `material_type` no payload. Por isso o
  fluxo de aulas busca **sem** filtro de material (`material_type=None` no
  `retrieve_context`) — nenhum vetor antigo desaparece.
- Vetores de **livro/apostila** são sempre filtrados por
  `material_type=book|workbook` + os filtros invioláveis (`teacher_id`,
  `course_id`, `subject_name`).
- Dois filtros novos no `retrieve_context`:
  - `allowed_sections` (Modo Professor Curador): só capítulos ativos da turma;
  - teto de seção (anti-spoiler): para livros, o aluno enxerga até
    `current_section + 1` do plano dele.

### 2.3 QuizBank (cache semântico)

- Toda a camada (`find_unseen_quiz`, `get_used_lenses`, `save_quiz_package`,
  `delete_lesson_quizzes`) agora escopa por `material_type`: o capítulo 3 do
  livro **nunca colide** com a aula 3 da mesma disciplina.
- O cache de tópicos no Redis ganhou o mesmo isolamento — a chave legada de
  `lesson` fica **inalterada** (cache existente preservado); livros usam o
  sufixo `:book` / `:workbook`.
- Invalidação: reprocessar o PDF do Cap. 3 apaga só as questões
  `(curso, disciplina, seção 3, book)` — e as revisões espaçadas agendadas
  dessas questões morrem junto (FK `ON DELETE CASCADE`).

### 2.4 Geração de quiz (`review_generator`)

- **`lesson`** — prompt clássico (`REVIEW_PROMPT_TEMPLATE`), resumo de até 800
  chars, comportamento idêntico ao anterior.
- **`book`/`workbook`** — prompt de check-in (`READING_REVIEW_PROMPT_TEMPLATE`):
  - guia de leitura **curto** (máx. 500 chars) que aponta de volta para a obra
    (proteção de direitos autorais — nunca substitui a leitura);
  - quiz sobre **o trecho específico da seção**, nunca o livro inteiro;
  - lentes pedagógicas próprias (`READING_PERSPECTIVES`), e
    `FLASHCARD_PERSPECTIVES` quando o aluno pede `/flashcards`;
  - instrução de exatas: valores numéricos verificados passo a passo.

---

## 3. Jornada de Leitura Guiada (Fase 2 + 3)

Máquina de estados por plano (`ReadingPlans`, 1 linha por aluno × material):

```
                       07:00 (next_trigger_at)
                              │
              aluno interagiu desde a última sugestão?
                  │ sim                       │ não
                  ▼                           ▼
        envia microdose da             oferece recálculo SEM CULPA
        seção current+1                ("Quer que eu ajuste seu plano?")
        (10-15 min, com               (botões reading_recalc_/reading_keep_;
        progresso + errata)            nunca diz "você está atrasado")
                  │
        19:00 (next_checkin_at = sugestão + 12h)
                  ▼
        quiz de check-in da seção (PendingQuizzes.reading_plan_id)
                  │
        ┌─────────┴──────────┐
        ▼ acertou            ▼ errou
   current_section+1     seção NÃO avança;
   streak atualiza       convite a reler o trecho;
   progresso visível     próxima sugestão repete a seção
        │
        └─ 100%? → 🏆 parabéns + is_paused=true (revisões espaçadas continuam)
```

Pontos-chave de design:

- **`current_section` = seções CONCLUÍDAS.** O scheduler nunca avança o
  ponteiro — só o portão (quiz) avança. É isso que materializa o "quiz como
  check-in que desbloqueia a próxima fatia".
- **Replanejamento sem culpa**: o detector é `last_read_at < last_suggestion_at`
  (a sugestão anterior ficou sem interação). A oferta de recálculo zera
  `last_suggestion_at`, então os ciclos alternam sugestão/oferta sem jamais
  acumular leituras.
- **Modo férias**: `/pausar_leitura` seta `is_paused`; `/retomar_leitura`
  recalcula `next_trigger_at` a partir de hoje, preservando seção e streak.
- **Streak** é por dias locais (BRT) consecutivos com check-in aprovado: mesmo
  dia não duplica, ontem incrementa, buraco recomeça em 1 (e a mensagem nunca
  expõe a quebra).
- **Janela da Meta**: como os templates proativos estão desativados, sugestões
  e check-ins de WhatsApp só saem com janela de 24h aberta (mesma política do
  scheduler clássico); no Telegram saem sempre. O plano se reagenda mesmo sem
  entrega — nunca trava.

## 4. Revisão Espaçada (Fase 4)

- `register_quiz_answer()` é chamado em **toda** resposta de quiz com
  `quiz_bank_id` (aula, livro, flashcard, simulado — tudo entra no ciclo):
  - 1ª resposta → INSERT com D+3;
  - revisão correta → 3→10→30→`completed`;
  - erro em qualquer ponto (até em `completed`) → reset para D+3.
- `process_spaced_repetition()` reentrega a questão **direto do QuizBank**
  (`get_quiz_package` + reshuffle anti-cola das alternativas): zero LLM, zero
  RAG. Sem resposta do aluno, reentrega +1 dia; quem fecha o degrau é sempre a
  resposta.

## 5. Modo Socrático + Gabarito Verificado (Fase 5)

No `run_rag_pipeline` (conversa livre), um regex barato detecta perguntas
"sobre o material" (`livro|capítulo|apostila|exercício|seção|página|questão`).
Quando detecta **e a turma tem material denso** (plano ou vetores de livro):

1. **Rate limit de proteção de conteúdo**: 3 consultas/min por aluno sobre o
   material (Redis, fail-open). Estourou → resposta amigável sem LLM.
2. Contexto do livro recuperado com filtro de material + capítulos ativos +
   teto anti-spoiler.
3. `SOCRATIC_MODE_DIRECTIVE` no System Prompt: dica → passo intermediário →
   solução completa só se pedida explicitamente; citações literais limitadas a
   frases curtas com indicação de página.
4. **Gabarito Verificado**: se a pergunta referencia um exercício
   ("exercício 3.2", "exercício 2 do capítulo 3") e existe `AnswerKeys`
   (`/gabarito_Turma_cap3_ex2_42J`), a resposta oficial entra como âncora — a
   IA guia em direção a ela sem revelá-la, eliminando alucinação.
5. **Errata Viva** da seção citada (ou em leitura) é injetada na diretriz.

Turma **sem** material denso: o Addon fica inerte e o comportamento clássico
das aulas não muda em nada.

## 6. Engajamento (Fase 6)

| Recurso | Mecânica |
|---|---|
| Flashcards (`/flashcards`) | Pacote P/R curto (lente Anki) da última seção lida; entra no QuizBank → herda a revisão espaçada de graça |
| Simulado (`/simulado_T_5`) | Até 5 questões `DISTINCT ON (capítulo, tópico)` do QuizBank, capítulos 1..N — custo $0 |
| Aula ↔ Capítulo (`/vincular_aula_T_2_3`) | Ao responder o quiz da Aula 2, o feedback ganha "complementa o Capítulo 3, que tal revisar?" |
| Errata (`/errata_T_cap3_...`) | Aparece na sugestão de leitura, no check-in e nas respostas RAG da seção |
| Curador (`/ativar_capitulos_T_1,3,5`) | `Classes.active_chapters` (JSON); o scheduler PULA capítulos desativados e o RAG os filtra (`MatchAny`) |
| Progresso | `📖 X% do livro \| 🔥 N dias` em toda interação de leitura + `/meu_progresso` com barra |

## 7. Telemetria Editorial (Fase 7) — LGPD by design

- `/mapa_dificuldade_T` — % de erro por capítulo/seção com heatmap 🟩🟨🟧🟥;
  células com **menos de 3 respostas são suprimidas** (`MIN_COHORT_SIZE`).
- `/funil_conclusao_T` — barras de "quantos chegaram ao cap. k" + maior ponto
  de abandono. Apenas contagens, nunca nomes.
- `/corpus_duvidas_T` — 30 dias de mensagens agrupadas por capítulo via LLM; o
  prompt (`CORPUS_DUVIDAS_PROMPT`) **exige anonimização** e fecha com um
  insight editorial para a próxima edição.

## 8. Invariantes arquiteturais respeitados

- **SLA 200ms**: o webhook só ganhou um `match` de regex pré-compilado
  (allowlist de comandos de aluno) + enqueue. Zero I/O novo na borda.
- **Filtros obrigatórios**: toda query nova em PG/Qdrant filtra por
  `teacher_id`/`course_id` (e disciplina com `IS NULL` explícito para legado).
- **Async everywhere** + `FOR UPDATE SKIP LOCKED` nos novos pollers.
- **Tenacity**: chamadas externas continuam atrás dos wrappers existentes
  (Drive/parser/embedding); o Addon não adicionou chamadas externas novas fora
  deles.
- **str.format() em todos os prompts** novos; nenhum segredo hardcoded.
- **Falha isolada**: cada etapa nova do scheduler roda em try/except próprio;
  hooks no `handle_quiz_response` são best-effort — o Addon nunca derruba o
  fluxo clássico.

## 9. Deploy / Operação

1. `alembic upgrade head` (testado com upgrade + downgrade em Postgres 16).
2. Reorganizar o Drive (`Curso > Disciplina > Livro > Cap 1...`) e rodar
   `/sinc_materiais` — o índice `material_type` do Qdrant é criado
   automaticamente, inclusive em coleções antigas.
3. `/plano_leitura_Turma_diario` para ativar a jornada.
4. Comandos novos documentados no `/ajuda`.

**Verificação executada**: `pytest` 88/88 ✅ · migration up/down ✅ · teste de
integração da escada SRS e do portão de leitura contra Postgres real ✅.

---

# Horizontes H1-H3 (plano de diferenciação)

## H1 — Fechar o Ciclo do Hábito

| Feature | Mecânica | Módulo |
|---|---|---|
| **Desafio de Turma** (`/desafio_T`) | MESMA questão para a turma (placar comparável, cache hit preferido); fecha em 6h; cada participante recebe placar ANÔNIMO ("67% acertou — você foi um deles 🏅") e o professor o resumo. Coorte < 3 → sem placar coletivo (LGPD). | `workers/challenges.py` + tabela `ClassChallenges` + `PendingQuizzes.challenge_id` |
| **Sentinela Emocional** | 3 regras sem LLM: 3+ erros consecutivos · streak ≥7 quebrado há 48h+ · aluno ativo silenciado (5-14 dias). Digest ÚNICO diário (12h local) por professor, com ação sugerida; dedupe de 7 dias (`SentinelAlerts`). | `workers/sentinel.py` |
| **Pílulas de Áudio** | Azure Speech REST (flag `TTS_ENABLED`, voz `TTS_VOICE`); sugestão de leitura também em áudio de até ~90s; texto SEMPRE vai (áudio é camada). `send_audio()`/`download_media()` nos 2 canais. | `shared/tts.py` |
| **Medalhas de Streak** | 3/7/30/100 dias → 🥉🥈🥇🏆 no portão (marco exato) e badge no `/meu_progresso`. | `reading_journey.py` |

## H2 — Produtos-Âncora

### H2.1 Ativação por QR Code (Livro Vivo self-service)

```
gerar chaves (script) → colar na aba "Chaves" → /sinc_alunos → QR impresso
   │                                                              │
   │   leitor escaneia → wa.me abre com "…LIVRO-XXXX-XXXX" → envia│
   ▼                                                              ▼
webhook (regex SLA 200ms; desconhecido passa se: tem chave OU pending no Redis)
   → fila command_messages role="activation" → activation_worker:
     chave válida → pede NOME (+termo LGPD; estado no Redis TTL 1h)
     nome → Student(registration_source='qr_activation', consent_at=NOW)
            + matrícula na turma-coorte + ReadingPlan diário 07:00
            + chave consumida + auditoria (KeyActivations)
```

- **Leitor = Student.** Nenhuma entidade nova: herda jornada de leitura,
  quizzes, revisão espaçada, janela de 24h e telemetria, sem fork.
- **Anti-pirataria:** chave de uso único (`max_activations`), revogação por
  lote pela planilha (`Ativa=FALSE`), auditoria por ativação. Status
  `activated` nunca é sobrescrito pela sync.
- **LGPD:** consentimento ANTES de criar o registro; `/sair_livro` anonimiza
  e desativa (exclusivo de leitores auto-cadastrados — aluno de planilha é
  orientado a falar com o professor).
- `/chaves_[turma]` dá ao autor o funil de ativações.

### H2.2 Correção Dissertativa com a Rubrica do Professor

`/rubrica_T_critérios` grava os critérios oficiais (`Rubrics`). O aluno envia
`/corrigir` + foto (ou foto com legenda "corrigir"): download da mídia no
worker → OCR via Azure Document Intelligence (o MESMO parser da ingestão) →
LLM corrige ancorado na rubrica (nota por critério + 2 melhorias, modo
socrático — nunca reescreve por ele). Sem rubrica: padrão + aviso. Limite
`ESSAY_DAILY_LIMIT` (2/dia). Telemetria: `essay_corrected`.

### H2.3 Simulado Cronometrado
`/simulado_T_5` agora abre um `ClassChallenges` (kind='simulado', 24h). No
fechamento cada aluno recebe **percentil anônimo** ("4/5 — à frente de 75% da
turma") e os erros já estão na revisão espaçada.

### H2.4 Relatório Editorial Mensal
Dia 1º, 08h local: para cada turma com leitura ativa → progresso médio, funil
+ ponto de maior abandono, mapa de dificuldade (30 dias, coorte ≥ 3) e
ativações de chave do mês, direto ao professor/autor. (`editorial_report.py`)

## H3 — Plataforma

- **H3.1 Onboarding self-service:** chave `PROF-XXXX-XXXX` (mesmo fluxo de
  ativação) cria o Teacher e envia o guia (e-mail da service account p/
  compartilhar o Drive). `/criar_turma_Curso_Turma` e
  `/adicionar_alunos_Turma_Maria:fone; João:fone` completam o setup sem
  humano no loop — IDs determinísticos idênticos aos da sync (migrar para a
  planilha depois não duplica nada).
- **H3.2 Hiperpersonalização:** `SRS_ADAPTIVE_ENABLED` (default OFF) troca a
  escada fixa por intervalos individuais via `ease_factor` (3→8→20→30 p/ quem
  domina; 3→4→5→… p/ quem patina). `ADAPTIVE_TOPIC_PRIORITY` (default ON)
  prioriza tópicos com acerto < 70% na rotação de quizzes — sem nunca repetir
  tópico dentro do ciclo.
- **H3.3 API B2B:** `POST /v1/partner/keys` (gera lote + links wa.me prontos
  p/ QR) e `GET /v1/partner/stats` (agregados LGPD). Auth `X-API-Key`
  (comparação em tempo constante); sem `PARTNER_API_KEY` → 503 (opt-in).

## Flags novas (config)

| Env var | Default | Efeito |
|---|---|---|
| `TTS_ENABLED` / `TTS_VOICE` / `AZURE_SPEECH_KEY` / `AZURE_SPEECH_REGION` | off | Pílulas de áudio |
| `CHALLENGE_WINDOW_HOURS` / `SIMULADO_WINDOW_HOURS` | 6 / 24 | Janelas de fechamento |
| `ESSAY_DAILY_LIMIT` | 2 | Correções de redação por aluno/dia |
| `SRS_ADAPTIVE_ENABLED` | false | SM-2 individual (vs. escada fixa D+3/10/30) |
| `ADAPTIVE_TOPIC_PRIORITY` | true | Tópicos fracos primeiro na rotação |
| `PARTNER_API_KEY` | — | Liga a Partner API |

**Verificação (Horizontes):** `pytest` 126/126 ✅ · migration `f6a7b8c9d0e1`
up/down em PG 16 real ✅ · integração real: ativação QR ponta a ponta (nome +
LGPD + matrícula + plano + anti-pirataria + /sair_livro) e fechamento de
desafio com placar anônimo ✅ · bugfix do import quebrado em
`spreadsheet_sync.py` (Pass 3) ✅.

---

# RAG de Alta Precisão (F0-F8) — 2026-06-12

> **Manual de operações dedicado:** `_docs/docs/RAG_OPERATIONS.md` (stack,
> pipeline, configuração, eval, calibração de limiares, troubleshooting,
> custos e recomendações de fine-tuning).

Elevação da recuperação de "busca vetorial ingênua" para uma arquitetura de
certeza assintótica. A precisão vem do **invólucro de engenharia**: evidência
ancorada e rastreável + **recusa explícita quando a evidência é insuficiente**
(o bot que diz "não encontrei no material, avisei seu professor" é mais
confiável que o que inventa).

## Pipeline final (cada camada atrás de flag; tudo OFF = sistema clássico)

```
pergunta do aluno
   │
   ▼
query_router (F6) ── regex puros: "exercício 3.2", "aula 4", siglas, NBR/Lei
   │                 → pinpoint obrigatório + reforço literal no braço esparso
   ▼
retrieve_or_full_context (F7) ── corpus do escopo ≤ 150k chars?
   │            └─ SIM → corpus INTEIRO no prompt (scroll + cache Redis 24h)
   ▼ NÃO
busca (F3) ── HYBRID_SEARCH_ENABLED + collection com sparse?
   │   ├─ SIM → Query API: prefetch [dense top50 | sparse top50] → fusão RRF
   │   │        server-side (MESMO filtro de escopo nos 2 braços)
   │   └─ NÃO → dense puro (query_points) — caminho clássico
   │   (2 estágios preservados: pinpoint da aula citada → broad ≤ ponteiro;
   │    + expansão por vínculo LessonChapterLinks: aula N ↔ capítulo M)
   ▼
reranker (F4) ── RERANKER_PROVIDER: cohere | voyage | open | none
   │             funil 50 → top 5; corte RERANK_SCORE_THRESHOLD=0.3;
   │             timeout 3s + fallback silencioso p/ ordem da fusão
   ▼
CRAG-lite (F5) ── avaliador = score do reranker (custo zero):
   │   🟢 ≥0.5 fluxo normal
   │   🟡 0.2-0.5 → 1 reescrita de query (LLM barato) → repete; melhor dos 2
   │   🔴 <0.2 → ABSTENÇÃO: out_of_scope_message enriquecido, SEM chamar o
   │            LLM gerador + HITL: professor avisado (dedupe diário, agregado)
   ▼
LLM gerador (inalterado, provider-agnóstico) com groundedness directive
```

Na **ingestão** (`process_file`): chunking estrutural (F1: headings →
`section_title`; pares regra↔exceção colados até 1.5× o limite) →
enriquecimento contextual (F2: `context_prefix` por chunk, prompt com o
excerto do documento como PREFIXO fixo → context caching do provider) →
embedding híbrido (F3: dense+sparse em 1 passo no BGE-M3, ou fastembed local
como Plano B) → payload com `content` cru + `context_prefix` + `section_title`
+ `char_count` → medidor de corpus (F7). uuid5 determinístico preservado.

## Módulos novos

| Módulo | Responsabilidade |
|---|---|
| `src/rag/query_router.py` | Referências exatas (regex puros) → pinpoint + reforço esparso |
| `src/rag/crag.py` | Limiares 🟢🟡🔴, reescrita única, abstenção + HITL ao professor |
| `src/rag/corpus.py` | Medidor de corpus + corpus completo cacheado (bypass F7) |
| `src/rag/verified_facts.py` | Gabarito/Errata no fluxo de aulas (fato acima do contexto) |
| `src/llm/reranker.py` | `IRerankerProvider` multi-provider + fallback silencioso |
| `src/llm/bge_m3.py` | Embedder híbrido via API (contrato documentado) + Plano B fastembed |
| `scripts/eval_rag.py` | A régua: recall@k, MRR, abstenção correta, latência, `--stage/--embedder/--json` |
| `scripts/reindex_all.py` | Janela única F8: documents_v2 → ingestão → cutover por alias |
| `tests/golden/` | golden_rag.jsonl (curar!), README, baseline.md, results.md |

## Flags novas (todas OFF = sistema atual intocado)

| Env var | Default | Efeito |
|---|---|---|
| `CONTEXTUAL_RETRIEVAL_ENABLED` | false | Enriquecimento contextual na ingestão (F2) |
| `HYBRID_SEARCH_ENABLED` | false | Fusão RRF dense+sparse (exige collection híbrida, F8) |
| `RERANKER_PROVIDER` | none | cohere \| voyage \| open (TEI) |
| `CRAG_LITE_ENABLED` | false | Limiar de evidência + Recusa Honesta (exige reranker) |
| `LONG_CONTEXT_ENABLED` | false | Bypass do RAG p/ corpus ≤ `LONG_CONTEXT_MAX_CHARS` |
| `RAGAS_EVAL_ENABLED` | false | Faithfulness opcional no eval (LLM-juiz) |
| `QDRANT_COLLECTION` | documents | Permite apontar o worker p/ documents_v2 na reindexação |
| `BGE_M3_ENDPOINT` / `BGE_M3_API_KEY` | — | API de inferência do embedder híbrido |
| `COHERE_API_KEY` / `VOYAGE_API_KEY` / `OPEN_RERANKER_ENDPOINT` | — | Credenciais do juiz |
| `CRAG_GREEN` / `CRAG_RED` | 0.5 / 0.2 | Limiares 🟢🟡🔴 (calibrar pelo golden set) |
| `RERANK_CANDIDATES` / `RERANK_TOP_N` / `RERANK_SCORE_THRESHOLD` | 50 / 5 / 0.3 | Funil |

## Custos (onde cada real é gasto)

- **Ingestão (1x por documento):** enriquecimento F2 = 1 chamada LLM barata
  por chunk, com o excerto fixo cacheado pelo provider (DeepSeek context
  caching / Gemini implicit caching — sem SDK novo; -69% segundo o relatório);
  custo logado por arquivo (`contextual_enrichment_cost`).
- **Por conversa:** no máximo 1 rerank barato + (raramente, só no 🟡) 1
  reescrita de query. Abstenção 🔴 NÃO chama o LLM gerador (custo zero).
  Nada de LLM-juiz por mensagem.
- **Long-context (F7):** ~37k tokens por conversa num corpus de 150k chars —
  ligar POR DISCIPLINA depois de fazer a conta (log `full_context_built`).

## Sequência operacional pendente (exige credenciais de prod)

1. **Curar o golden set** (40-80 linhas; guia em `tests/golden/README.md`).
2. **Baseline (F0):** `python scripts/eval_rag.py --stage dense` → commitar
   em `baseline.md`. *Nenhuma fase liga antes disso.*
3. **Decisão de embedder (F3a):** `--embedder bge-m3 --stage hybrid` vs atual
   (recall em "termos exatos", custo/1k chunks, latência p95) → `results.md`.
4. **Reindexação única (F8, 03:00 BRT):** `reindex_all.py --create` → worker
   com `QDRANT_COLLECTION=documents_v2` + flags F1/F2/F3 ligadas →
   `--enqueue` → `--status` até concluir → `--cutover`.
5. **Ligar por etapas, medindo:** `HYBRID_SEARCH_ENABLED` → `RERANKER_PROVIDER`
   → `CRAG_LITE_ENABLED` (só depois do reranker validado — o score dele é o
   avaliador) → tabela das 5 configurações em `results.md`.
6. Calibrar `CRAG_GREEN`/`CRAG_RED` pelo golden set (falsos-recusados ≤ 5%);
   rollback instantâneo = desligar a flag.

**Verificação (RAG Preciso):** `pytest` 199/199 ✅ (73 novos) · imports e CLIs
sanity-checked ✅ · ruff/mypy limpos nos arquivos da tarefa ✅ · qdrant-client
1.9.1 → 1.18.0 (Query API; `search()` migrado para `query_points`).

---

# Vertical de Idiomas (F0-F9) — 2026-06-14

> **Manual de operações dedicado:** `_docs/docs/IDIOMAS_OPERATIONS.md` (portas
> de fala, flags por fase, FinOps por operação, LGPD do áudio, ADRs e
> troubleshooting). Esta seção é o **mapa arquitetural** da vertical.

Transforma o mesmo assistente, **sem fork**, em um **tutor de idiomas** quando o
curso é de idioma — reusando jornada de leitura, SRS, QuizBank, funil RAG (com
Recusa Honesta), janela de 24h e omnichannel. A capacidade nova é uma **camada
de fala provider-agnóstica** (entrada e saída de áudio) e um punhado de fluxos
gated. **Tudo OFF por padrão** ⇒ produção bit a bit inalterada (invariante
provado por teste).

## 1. Gatilho por NOME do curso (convenção sobre configuração)

Nenhuma coluna, painel ou flag por-turma decide "isto é idioma": o
`detect_language_course(course_name)` (puro, `src/shared/language.py`) decide em
runtime, na ordem:

1. **Tag autoritativa** na pasta do Drive: `Inglês Business [idioma:en]`,
   `Espanhol A2 [idioma:es]` — vence a stoplist e aceita idiomas fora da
   heurística (`[idioma:ko]`).
2. **Stoplist** (exige tag): `História da Língua Inglesa`, `Literatura
   Inglesa`, `Linguística Aplicada` **não** viram tutor sem a tag.
3. **Heurística** por palavra-chave: `Inglês Business`, `Curso de Francês`.
4. Senão → `None` (curso normal, comportamento clássico).

O nome já flui ingestão → `Courses.name` → `build_system_prompt`, então o switch
é grátis. A aba **Turmas** da planilha pode **sobrescrever** (coluna `Idioma`
Sim/Não autoritativa + `Nível` → CEFR por turma).

## 2. Camada de fala (Ports & Adapters, igual reranker/parser)

```
WhatsApp/Telegram (voz in)  ─► webhook (regex + media_id, SLA 200ms)
   │                                       │ enfileira (worker faz o trabalho)
   ▼                                       ▼
download_media (canal) ─► ISpeechToText.transcribe ─► [IPronunciationAssessor.assess]
   │ (áudio DESCARTADO após a análise — LGPD)                     │
   ▼                                                              ▼
LLM tutor (RAG + CRAG, provider-agnóstico) ─► feedback ─► ITextToSpeech.synthesize ─► áudio out
```

| Porta | Arquivo | Providers (env var) | `none` |
|---|---|---|---|
| `ISpeechToText` | `src/speech/stt.py` | `azure` · `whisper_openai` · `whisper_local` · `google` | pede texto |
| `IPronunciationAssessor` | `src/speech/pronunciation.py` | `azure` | só transcreve |
| `ITextToSpeech` | `src/speech/tts.py` | `azure` · `elevenlabs` · `google` | entrega só texto |

`src/speech/factory.py` (`get_stt`/`get_pronunciation`/`get_tts`) cacheia por
nome e devolve **None** em `none`/desconhecido/sem-chave — toda a vertical
degrada com graça. `src/shared/tts.py` (pílulas de áudio H1.3) virou **façade**
sobre `get_tts()` — comportamento idêntico com `TTS_PROVIDER=azure`.

> **Azure é o default, não o dono:** o mesmo recurso/chave Azure Speech faz
> STT + Pronunciation Assessment + TTS (menor atrito), mas `whisper_*` existem
> para não depender da Azure. Trocar = trocar a env var.

## 3. O que cada fase entregou

| Fase | Flag(s) (+ `LANGUAGE_MODE_ENABLED`) | Entrega | Módulo central |
|---|---|---|---|
| **F0** Fundação | — | portas de fala + DTOs + detecção por nome + persona de tutor (hook no `prompt_builder`) | `src/speech/*`, `shared/language.py` |
| **F1** Listening | `TTS_ENABLED` | check-in da leitura vira "áudio do dia": diálogo na língua-alvo + quiz de compreensão | `review_generator.py`, `reading_journey.py` |
| **Perfil/nível** (parte do F5) | (`TTS_ENABLED` p/ áudio) | revisão da matéria respeita a **língua por nível** + áudio na língua-alvo (Iniciante → texto pt-BR + áudio-alvo; demais → tudo no alvo) | `generate_language_review`, `llm_worker.handle_review_request` |
| **F2** Vocabulário SRS | `VOCABULARY_SRS_ENABLED` | flashcard palavra-alvo + significado L1 + áudio; entra no SRS; reentrega `$0` | `workers/vocabulary.py` |
| **F3** Speaking/Pronúncia | `SPEECH_INPUT_ENABLED` (+ `PRONUNCIATION_PROVIDER`) | voz do aluno → STT → [pronúncia] → feedback socrático; **só scores persistem** | `workers/speaking.py` |
| **F5** Nivelamento CEFR | `PLACEMENT_TEST_ENABLED` | `/nivelamento`: 6 questões A1→C2, classifica e grava `cefr_level` por matrícula | `workers/placement.py` |
| **F6** Relatório proficiência | `PROFICIENCY_REPORT_ENABLED` | mensal: inventário ao aluno + agregado à escola (coorte ≥ 3) | `workers/proficiency_report.py` |
| **F7** Rede L1 | `L1_SAFETY_NET_ENABLED` | diretiva emocional: destrava em L1 quando o aluno trava, devolve ao alvo | `prompt_builder.py` |
| **F4** Roleplay | `ROLEPLAY_ENABLED` | dramatização por turno na língua-alvo; estado no Redis (TTL 30 min) | `query_router.py`, `workers/roleplay.py` |
| **F8** Exam prep | `EXAM_PREP_ENABLED` | `/simulado` cronometrado sobre banco estilo-prova (`material_type='exam'`) | `workers/exam_prep.py` |
| **F9** White-Label/B2B | `PARTNER_API_KEY` | Partner API (provisiona turma de idioma + proficiência agregada) + **opt-out** SAIR/VOLTAR | `webhook/partner.py`, `workers/optout.py` |

## 4. Fluxo do speaking (F3) — espelha o `essay.py` (mídia → análise → feedback)

```
voz → webhook (media_id, regex/Redis; NUNCA baixa na borda — SLA 200ms)
   → llm_worker fast-path (message_type=='audio', ANTES do debounce)
   → speaking.handle_audio_message (gate + idempotência CAS por media_id)
   → analyze_speaking: download_media → get_stt().transcribe
        → [PRONUNCIATION_PROVIDER!=none] get_pronunciation().assess (matriz acústica)
        → PRONUNCIATION_FEEDBACK_PROMPT → feedback socrático (não reescreve a fala)
        → persiste SÓ os scores (PronunciationAttempts) ; `del audio_bytes`
```

## 5. Decisões de arquitetura (ADR resumido)

- **Convenção sobre configuração** (gatilho por nome) — zero painel, switch em
  runtime atrás de uma flag; banco (CEFR/alvo por turma) sobrescreve.
- **Áudio efêmero** (LGPD — voz é dado sensível): `del audio_bytes` ao fim da
  análise (`SPEECH_AUDIO_RETENTION_SECONDS=0`); persistimos só números
  (`PronunciationAttempts`, sem coluna de áudio). Providers de voz =
  suboperadores no RIPD.
- **Reuso do SRS+QuizBank para vocabulário** (em vez de tabela paralela) →
  herda cache `$0` e o motor de repetição espaçada de graça.
- **`generate_language_review` SEM cache de propósito:** o QuizBank não é
  escopado por nível; cachear a revisão por-nível colidiria entre turmas
  Iniciante×Avançado. Vocab/listening/exam permanecem **cacheáveis** (formato
  independente de nível).
- **`ReviewOutput.audio_script`** (versão completa na língua-alvo) persiste no
  QuizBank → sobrevive ao Cache Hit e à reentrega SRS (o iniciante lê pt-BR e
  ouve a tradução íntegra).
- **`/quiz_[turma]`:** áudio sintetizado **1×** e reaproveitado por todos os
  alunos da turma (evita N chamadas de TTS).

## 6. Invariantes (os mesmos do projeto, respeitados)

- **Flag OFF = intocado:** teste prova que `LANGUAGE_MODE_ENABLED=false` **ou**
  curso normal/stoplist ⇒ System Prompt byte a byte igual.
- **SLA 200ms:** a voz entra só como `media_id` (regex/Redis na borda); download
  e STT acontecem no worker.
- **Fallback sempre:** sem provider/erro em qualquer camada de fala ⇒ degrada
  para texto; o aluno nunca cai.
- **Filtros obrigatórios** `teacher_id`/`course_id` em toda query nova; LGPD com
  coorte ≥ 3 nos agregados; `str.format()` em todos os prompts; segredos via
  Key Vault.
- **Opt-out NÃO é flag-gated** (direito do aluno; coluna default `false` =
  seguro por padrão).

## 7. Modelo de dados (migrações novas, todas reversíveis em PG16)

| Migração | Mudança |
|---|---|
| `c9d0e1f2a3b4` | `Classes.is_language` (bool) + `proficiency_level` |
| `d0e1f2a3b4c5` | `QuizBank.audio_script` |
| `e1f2a3b4c5d6` | tabela `PronunciationAttempts` (só scores, FK CASCADE, **sem áudio**) |
| `f2a3b4c5d6e7` | `StudentEnrollment.cefr_level` (nível por aluno × turma) |
| `a3b4c5d6e7f8` | `Students.messaging_opted_out` (opt-out B2B) |

**Verificação (Idiomas):** `pytest` 309/309 ✅ (+110 desde o RAG Preciso) ·
ruff limpo · mypy Success (0 erros, 98 arquivos) · as 5 migrações up→down→up em
Postgres 16 real ✅. Sequência de piloto: ligar **uma flag por vez** em 1 turma,
medir custo por operação (STT/min, pronúncia/chamada, TTS/char, LLM/turno) e
ampliar.
