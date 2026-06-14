# Task — Roteiros de Execução

## Revisão de Segurança e Robustez (`PLANO_REVISAO.md`) — pass 1 (2026-06-13)

> Ledger completo: `_docs/docs/REVISAO_ACHADOS.md`. Gate: **241 passed**
> (226 baseline + 15 novos), ruff 12 / mypy 22 (pré-existentes, nenhum novo),
> bandit HIGH 0, migration up→down→up ✓ (PG 16 real).
> Infra confirmada LIVE via `az` (read-only): F02 (7 segredos em claro no app),
> S3 (Redis `allkeys-lru` — drift do Bicep, evicta fila/locks), S4/S5/S6, S9
> (`max_connections=50` vs pool 10/proc × 5 réplicas), S11 (sem probes).
> **CORRIGIDO:** resposta de quiz agora é CAS atômico (idempotente sob replay da PEL).

- 🔴 **F01 (AÇÃO HUMANA):** segredos reais (`QDRANT_API_KEY`, `AZURE_OPENAI_API_KEY`)
  commitados em claro em `infra/azure/worker-llm.yaml` (no histórico desde `6ca4c5a`).
  Aplicado: `.gitignore` para os exports. **Falta: rotacionar as chaves + purgar histórico + mover p/ Key Vault.**
- **Corrigido (gate verde):** webhook fail-open com Redis fora (handler global + rate-limit, S8);
  `print()` de PII → logger mascarado (S13); Telegram secret em tempo constante (S12);
  MD5 `usedforsecurity=False`. +14 testes (`test_edge_resilience.py`, `test_property_metamorphic.py`).
  Drift de venv corrigido (`starlette==0.37.2`) → baseline 226 restaurado.
- **Verificado/descartado:** SQL injection (35 hits B608 = falsos-positivos); escopo
  `teacher_id`+`course_id` em todas as ops Qdrant; PII→LLM (1º nome); Partner auth; S15 (code smell benigno).
- **Infra confirmada (proposta, decisão humana + what-if):** S3 Redis C0 🔴, S4 PG SPOF 🟠,
  S5 ACR admin 🟠, S6 firewall aberto 🟠, S7 imagens dummy 🟠, S11 sem probes 🟠, S10/S14/Dockerfile-root 🟡.
- **Bloqueado (precisa rede/creds/az):** pip-audit (CVEs), `az ... what-if`, `eval_rag.py`.
- **Pendente:** mutação/fuzzing (Onda 5), concorrência/PEL e pool de conexões (Onda 3 deep),
  fallbacks/FinOps RAG (Onda 4).


## Parte 3: RAG de Alta Precisão (`_docs/prompt_execucao_rag_preciso.md`) — **CÓDIGO CONCLUÍDO** (2026-06-12)

> Tudo atrás de flags (**todas OFF por padrão = sistema atual intocado**).
> Pendências OPERACIONAIS (exigem credenciais de prod): curadoria do golden
> set, execução do baseline (F0), decisão de embedder pelo eval (F3a) e a
> janela única de reindexação (F8). Ver `tests/golden/baseline.md`.

### Fase 0 — Baseline Mensurável (RAG Triad) ✅ código / ⏳ execução
- [x] **[NEW] `tests/golden/golden_rag.jsonl`** — formato fixado + 12 linhas seed (`draft: true`; curar para 40-80 com substrings do corpus real — guia em `tests/golden/README.md`). Categorias: termos_exatos, conceitual, excecao_regra, capitulo_livro, fora_do_escopo.
- [x] **[NEW] `scripts/eval_rag.py`** — harness async determinístico: Context Recall@k, hit de fonte, MRR, taxa de abstenção correta + falsos-recusados, latência p50/p95; `--stage dense|hybrid|rerank|crag` (contribuição de cada fase), `--embedder X`, `--json`. Faithfulness via Ragas OPCIONAL (`--ragas` + flag `RAGAS_EVAL_ENABLED`, import-guarded).
- [ ] **Rodar e commitar o baseline** em `tests/golden/baseline.md` (PENDENTE — precisa de Qdrant Cloud + chave do embedder; tabela e passo a passo prontos no arquivo).

### Fase 1 — Chunking Estrutural ✅
- [x] `RAG_CHUNK_SIZE` 800→**2000** / `RAG_CHUNK_OVERLAP` 150→**300** (vale na próxima ingestão/reindex).
- [x] **[MODIFY] `text_splitter.py`** — `should_glue_paragraph()` (puro, testado): conectivos de continuação (Exceção/Salvo/Porém/§/alíneas/itens) NUNCA abrem chunk novo (teto duro 1.5×); headings = fronteiras preferenciais → metadado `section_title`; `assemble_structural_chunks()` substitui o buffer plano da ingestão.
- [x] **[MODIFY] `azure_parser.py`** — parágrafos com role title/sectionHeading viram blocos `## ...` (antes o filtro de 30 chars os descartava).
- [x] **[MODIFY] payload** — `section_title` + `char_count`; contexto cita `[Fonte: ... | Seção: {section_title}]`.

### Fase 2 — Contextual Retrieval ✅ (flag `CONTEXTUAL_RETRIEVAL_ENABLED`)
- [x] **[NEW] `CONTEXTUAL_ENRICHMENT_PROMPT`** (str.format; `{document_excerpt}` como PREFIXO fixo → prompt caching do provider; cap `CONTEXTUAL_MAX_DOC_CHARS=6000`).
- [x] **[MODIFY] `ingestion_worker.process_file`** — `context_prefix` por chunk (best-effort: falha → chunk cru); embedding e braço esparso sobre `prefix + chunk` (Contextual BM25); payload guarda `content` CRU + `context_prefix`; custo logado por arquivo (`contextual_enrichment_cost`).

### Fase 3 — Embeddings + Busca Híbrida ✅ (flag `HYBRID_SEARCH_ENABLED`)
- [x] **[NEW] provider `bge-m3`** (`src/llm/bge_m3.py`, httpx+Tenacity, contrato da API documentado) — `HybridEmbeddingProvider` com `aembed_documents_hybrid()` → (dense 1024, sparse); Plano B implementado: `encode_sparse_fallback()` via fastembed local (import-guarded); Plano C (Qwen3+MRL) documentado em `results.md`, não implementado.
- [x] **[MODIFY] `qdrant_client.py`** — `create_hybrid_collection()` (named vectors dense INT8 + sparse IDF), `get_collection_mode()` detecta collection legada e degrada com aviso (sem quebrar). Upgrade `qdrant-client` 1.9.1→1.18.0 (Query API; `search()` migrado para `query_points` em todo o retriever).
- [x] **[MODIFY] `retriever.py`** — fusão **RRF server-side** (`prefetch` dense+sparse top `RERANK_CANDIDATES=50`, MESMO filtro de escopo nos 2 braços), 2 estágios (pinpoint→broad) mantidos por cima; flag off ou collection sem sparse → dense intocado.
- [ ] **Decisão de embedder PELO EVAL** (PENDENTE — critérios e tabela em `tests/golden/results.md`; nenhuma reindexação antes da decisão).

### Fase 4 — Reranking ✅ (config `RERANKER_PROVIDER=none|cohere|voyage|open`)
- [x] **[NEW] `src/llm/reranker.py`** — `IRerankerProvider.rerank(query, documents, top_n, instruction=None)`; providers Cohere Rerank v4 (Fast/Pro), Voyage rerank-2, `open` (TEI: BGE-Reranker-v2-m3/Qwen3-Reranker); `none` = passthrough; timeout 3s + **fallback silencioso** para a ordem da fusão.
- [x] **[MODIFY] `retriever.py`** — funil `fusão (50) → rerank → top 5`; corte por `RERANK_SCORE_THRESHOLD=0.3` (cosseno vira pré-filtro frouxo 0.2); score por hit logado; score alimenta a F5.

### Fase 5 — CRAG-lite + Recusa Honesta ✅ (flag `CRAG_LITE_ENABLED`)
- [x] **[NEW] `src/rag/crag.py`** — limiares 🟢🟡🔴 (`classify_evidence`, puro/testado; `CRAG_GREEN=0.5`, `CRAG_RED=0.2`); 🟡 → **1** reescrita (`QUERY_REWRITE_PROMPT`, anti-loop estrutural) → repete fusão+rerank; 🔴 → abstenção com `out_of_scope_message` ENRIQUECIDO **sem chamar o LLM gerador**. Sem score de rerank disponível → CRAG degrada para 'off' (nunca abstém no palpite).
- [x] **[NEW] HITL** — `StudentInteractions('unanswered_question')` + aviso ao professor na fila `outbound_messages` com agregação do dia e dedupe diário por turma (Redis).
- [x] **[MODIFY] System Prompt** — `GROUNDEDNESS_DIRECTIVE` (citar fonte/seção; nunca completar com conhecimento paramétrico) + `PARTIAL_EVIDENCE_DIRECTIVE` no 🟡 — anexados só com a flag ligada.

### Fase 6 — Recuperação por Escopo + Grafo-lite ✅
- [x] **[NEW] `src/rag/query_router.py`** — regex puros testados: exercício N.M, questão N, capítulo/seção N, aula N, siglas (2-6 maiúsculas c/ stoplist), normas/leis (NBR/ISO/Lei/Decreto...); referência → pinpoint obrigatório + reforço literal no braço esparso (`sparse_query_text`).
- [x] **[MODIFY] `retriever.py`** — expansão por vínculo `LessonChapterLinks` (aula N → cap M e vice-versa; JOIN no PG antes do Qdrant, best-effort).
- [x] **[NEW] `src/rag/verified_facts.py`** — AnswerKeys/Erratas também no fluxo de AULAS quando o roteador detecta exercício (fato verificado SEMPRE acima do contexto). `KnowledgeLinks` genérica NÃO criada (só se o eval mostrar lacuna).

### Fase 7 — Long-Context para Bases Pequenas ✅ (flag `LONG_CONTEXT_ENABLED`)
- [x] **[NEW] `src/rag/corpus.py`** — medidor por (curso, disciplina, material) atualizado na ingestão; `retrieve_or_full_context()`: corpus ≤ `LONG_CONTEXT_MAX_CHARS=150k` → corpus completo via `scroll` (filtros de sempre + teto anti-spoiler), cache Redis 24h invalidado pela ingestão; acima do limite → funil normal; conta de tokens logada.

### Fase 8 — Reindexação Única ✅ código / ⏳ execução
- [x] **[NEW] `scripts/reindex_all.py`** — `--create` (documents_v2 híbrida), `--enqueue` (reseta `file_sync_state` preservando dead_letter + reenfileira), `--status`, `--cutover` (valida, apaga a antiga, alias documents→documents_v2 sem redeploy). Janela 03:00 BRT documentada.
- [x] **[NEW] `tests/golden/results.md`** — template da tabela comparativa (5 configurações) + decisão de embedder + custos.
- [ ] Executar reindex + rodar a régua nas 5 configurações (PENDENTE — depende da decisão do eval).

### O que ficou FORA (por decisão do prompt, não por falta)
GraphRAG/Neo4j, Self-RAG, DSPy/GEPA, NeMo Guardrails/Presidio, troca do LLM orquestrador.

### Verificação ✅
- [x] `pytest` — **199 passed** (126 anteriores + 73 novos em `tests/unit/test_rag_precision.py`: glue/headings/chunking, regexes do router, limiares CRAG, fallbacks do reranker e do híbrido mockados, métricas do eval, decisão de bypass F7, ordem do prompt de cache).
- [x] Flags OFF = comportamento atual (busca dense, 2 estágios, mesmos filtros) — única mudança latente é o chunk size, que só vale em ingestão nova.
- [x] ruff/mypy limpos nos arquivos da tarefa (pendências pré-existentes em arquivos não tocados permanecem).

---

## Parte 2: Horizontes H1-H3 (`_docs/prompt_execucao_horizontes.md`) — **CONCLUÍDO** (2026-06-12)

### Horizonte 1 — Fechar o Ciclo do Hábito ✅
- [x] **H1.1 Desafio de Turma** — `/desafio_[turma]` (mesma questão p/ todos, janela de 6h) + `ClassChallenges` + fechamento no scheduler com placar ANÔNIMO por participante e resumo ao professor. LGPD: coorte < 3 → só feedback individual. (`src/workers/challenges.py`)
- [x] **H1.2 Sentinela Emocional** — 3 regras determinísticas (erros consecutivos, streak quebrado, aluno ativo silenciado), dedupe de 7 dias (`SentinelAlerts`), digest único diário (12h) por professor com ação sugerida. (`src/workers/sentinel.py`)
- [x] **H1.3 Pílulas de Áudio** — `src/shared/tts.py` (Azure Speech REST + Tenacity, flag `TTS_ENABLED`), `send_audio()` nos 2 canais, áudio da sugestão de leitura (best-effort, texto sempre vai).
- [x] **H1.4 Medalhas de Streak** — marcos 3/7/30/100 (🥉🥈🥇🏆) celebrados no portão e badge no `/meu_progresso`.

### Horizonte 2 — Produtos-Âncora ✅
- [x] **H2.1 Ativação por QR Code** — `scripts/generate_activation_keys.py` (CSV p/ aba "Chaves" + links wa.me), sync da aba (revogação por lote; nunca sobrescreve 'activated'), `ActivationKeys`/`KeyActivations`, fast-path no webhook (regex + pending no Redis, SLA 200ms), `activation_worker` (valida chave → nome + consentimento LGPD → Student on-the-fly + matrícula + ReadingPlan + auditoria), `/sair_livro` (anonimização LGPD), `/chaves_[turma]`. Leitor = Student (`registration_source='qr_activation'`) — zero fork no pipeline.
- [x] **H2.2 Correção Dissertativa** — `Rubrics` + `/rubrica_[turma]_[critérios]`; aluno envia `/corrigir` + foto (ou legenda "corrigir"); download da mídia nos 2 canais → OCR (Azure DI, parser da stack) → LLM ancorado na rubrica do professor (fallback: rubrica padrão com aviso); limite de 2/dia. (`src/workers/essay.py`)
- [x] **H2.3 Simulado Cronometrado** — `/simulado` agora cria `ClassChallenges` (kind='simulado', 24h); fechamento devolve percentil anônimo a cada aluno ("4/5 — à frente de 75% da turma") + resumo ao professor.
- [x] **H2.4 Relatório Editorial Mensal** — dia 1º às 08h: progresso médio, funil, ponto de abandono, mapa de dificuldade (30d) e ativações de chave, por turma com leitura ativa, direto ao professor/autor. (`src/workers/editorial_report.py`)

### Horizonte 3 — Plataforma ✅
- [x] **H3.1 Onboarding self-service** — chave `PROF-` cria o Teacher (mesmo fluxo de ativação) + guia com e-mail da service account; `/criar_turma_[curso]_[turma]` e `/adicionar_alunos_[turma]_[Nome:fone; ...]` (IDs determinísticos compatíveis com a sync). (`src/workers/onboarding_commands.py`)
- [x] **H3.2 SM-2 adaptativo** — `adaptive_next_interval` (flag `SRS_ADAPTIVE_ENABLED`, default escada fixa): intervalos individuais pelo `ease_factor`; + priorização de tópicos FRACOS na rotação do `review_generator` (flag `ADAPTIVE_TOPIC_PRIORITY`, acerto < 70%).
- [x] **H3.3 API B2B de Parceiros** — `POST /v1/partner/keys` (lote + links wa.me) e `GET /v1/partner/stats` (agregados LGPD), auth `X-API-Key` em tempo constante; desativada sem `PARTNER_API_KEY`. (`src/webhook/partner.py`)

### Verificação ✅
- [x] `pytest` — **126 passed** (88 anteriores + 38 novos em `tests/unit/test_horizons.py`).
- [x] Migration `f6a7b8c9d0e1` testada (upgrade + downgrade + re-upgrade) em Postgres 16 real.
- [x] Integração contra PG+Redis reais: ativação ponta a ponta (chave → nome+LGPD → Student/matrícula/plano/auditoria → anti-pirataria → /sair_livro) e fechamento de desafio (3 participantes, placar 67% anônimo, resumo ao professor).
- [x] Bugfix pré-existente: import quebrado `src.db.models.schedule` em `spreadsheet_sync.py` (quebraria o /sinc_alunos).

---

## Parte 1: Addon de Livros (`_docs/prompt_execucao_addon.md`) — **CONCLUÍDO** (2026-06-11).

## Fase 1: Modelagem de Dados — Tipos de Material ✅

- [x] **[MODIFY] `src/drive/client.py`** — detecção da pasta-pai (`Aulas` | `Livro` | `Apostila`) e injeção de `material_type` (`lesson` | `book` | `workbook`) no dict retornado. Compatibilidade total: pasta de aula solta direto na Disciplina continua valendo como `lesson`.
- [x] **[MODIFY] Metadados do Qdrant** (`ingestion_worker.py`) — `material_type` no payload de cada vetor; payload index keyword `material_type` criado idempotentemente em `init_qdrant_collection` (inclusive para coleções pré-existentes).
- [x] **[NEW] Tabela `ReadingPlans`** — todas as colunas do spec + colunas operacionais `next_trigger_at`, `last_suggestion_at`, `next_checkin_at` (exigidas pelo fluxo da Fase 2/3).
- [x] **[MODIFY] Tabela `QuizBank`** — coluna `material_type` (VARCHAR, default `"lesson"`); toda a camada de acesso (`src/rag/quiz_bank.py`) escopa por material.
- [x] **[NEW] Tabela `SpacedRepetitionQueue`** — com FK CASCADE para QuizBank (questão invalidada → revisão morre junto).
- [x] **[NEW] Migration Alembic** — `alembic/versions/e5f6a7b8c9d0_add_reading_addon.py` (upgrade + downgrade testados contra Postgres 16 real).

## Fase 2: Jornada de Leitura Guiada ✅

- [x] **[MODIFY] `scheduler_worker.py`** — `process_reading_plans()` (em `src/workers/reading_journey.py`): microdoses de 10-15 min por seção, replanejamento sem culpa (oferta de recálculo quando o aluno não interagiu — nunca acumula nem menciona "atraso"), progresso visível em toda mensagem.
- [x] **[NEW] Comandos do aluno** — `/pausar_leitura` (modo férias), `/retomar_leitura` (recalcula sem penalidades), `/meu_progresso` (% + streak + barra). Roteados pelo webhook via allowlist regex (SLA 200ms preservado).
- [x] **[NEW] Comando do professor** — `/plano_leitura_[turma]_[ritmo]` (`diario` | `semanal`); total de seções descoberto na árvore do Drive; upsert preserva o progresso de quem já tinha plano.

## Fase 3: Quiz de Compreensão Pós-Leitura (com Progressão) ✅

- [x] **[MODIFY] `review_generator.py`** — `material_type` em todo o pipeline; prompt dedicado `READING_REVIEW_PROMPT_TEMPLATE` (questão sobre o trecho específico da seção, nunca do livro inteiro). Check-in = 1 questão objetiva (dentro do limite de 2-3 do spec; taxa de conclusão > profundidade).
- [x] **[MODIFY] `scheduler_worker.py`** — `process_reading_checkins()`: quiz agendado +12h após a sugestão (07:00 → 19:00). Quiz como portão: acerto desbloqueia a próxima seção (+streak); erro oferece reler o trecho e repete a seção.

## Fase 4: Revisão Espaçada Automática ✅

- [x] **[MODIFY] scheduler** — `process_spaced_repetition()` (em `src/workers/spaced_repetition.py`): reentrega questões vencidas direto do QuizBank (cache hit puro, custo $0), com respeito à janela de 24h da Meta.
- [x] **[MODIFY] fluxo de resposta de quiz** (`llm_worker.handle_quiz_response`) — `register_quiz_answer()`: primeira resposta insere com D+3; acertos sobem a escada 3→10→30→`completed`; erro em qualquer ponto reseta para D+3. `ease_factor` SM-2 registrado para o futuro.

## Fase 5: Explicação Sob Demanda (Modo Socrático) ✅

- [x] **[MODIFY] System Prompt** — `SOCRATIC_MODE_DIRECTIVE` (dica → passo intermediário → solução só se pedida) anexado quando a pergunta é sobre o material denso E a turma tem livro/apostila (fluxo clássico de aulas intocado).
- [x] **[NEW] Tabela `AnswerKeys`** + comando `/gabarito_[turma]_[cap3_ex2]_[resposta]`; o `VERIFIED_ANSWER_KEY_DIRECTIVE` ancora a explicação na resposta oficial (anti-alucinação). Referências extraídas da pergunta ("exercício 3.2", "exercício 2 do capítulo 3").
- [x] **[MODIFY] `retriever.py`** — filtros por `material_type` e `allowed_sections` (metadado + similaridade semântica); padrões de capítulo/seção adicionados ao pinpoint; teto anti-spoiler pela seção atual do plano.

## Fase 6: Material Complementar e Engajamento ✅

- [x] **6.1 Resumos protegidos** — guia de leitura curto (máx. 500 chars), aponta de volta para o material, citações literais limitadas (regra no prompt).
- [x] **6.2 Flashcards** — `/flashcards` (aluno): pares P/R estilo Anki via `FLASHCARD_PERSPECTIVES`, salvos no QuizBank → entram automaticamente na revisão espaçada da Fase 4.
- [x] **6.3 Simulados** — `/simulado_[turma]_[até_capítulo]`: cruza questões de múltiplos capítulos/tópicos do QuizBank (DISTINCT por capítulo+tópico, máx. 5 questões), custo $0.
- [x] **6.4 Progresso visível e streak** — `READING_PROGRESS_LINE` em toda interação de leitura; `ReadingPlans.streak_days` com regra de dias locais consecutivos.
- [x] **6.5 Mapeamento Aula ↔ Capítulo** — `/vincular_aula_[turma]_[aula]_[capítulo]` + sugestão automática no feedback do quiz da aula.
- [x] **6.6 Errata Viva** — `/errata_[turma]_[seção]_[correção]`; injetada na sugestão de leitura, no check-in e nas respostas RAG da seção.
- [x] **6.7 Modo Professor Curador** — `/ativar_capitulos_[turma]_[1,3,5]` (e `_todos`); capítulos desativados são pulados pelo scheduler e filtrados no RAG.

## Fase 7: Telemetria Editorial ✅

- [x] `/mapa_dificuldade_[turma]` — heatmap textual (🟩🟨🟧🟥) do índice de erro por capítulo/seção (QuizBank × PendingQuizzes).
- [x] `/funil_conclusao_[turma]` — barra de progresso por capítulo + ponto de maior abandono.
- [x] `/corpus_duvidas_[turma]` — dúvidas de 30 dias agrupadas por capítulo via LLM (`CORPUS_DUVIDAS_PROMPT`).

## Restrições Adicionais ✅

- [x] **Proteção de conteúdo** — citações curtas obrigatórias (prompt) + rate limit de 3 consultas RAG/min por aluno sobre o material (`ragrl:{student}:{course}:material` no Redis, fail-open).
- [x] **LGPD** — telemetria editorial agregada e anonimizada; células com < 3 respostas suprimidas (`MIN_COHORT_SIZE`); prompt do corpus exige anonimização.
- [x] **Exatas** — prompts de check-in/socrático instruem valores numéricos verificados passo a passo e variação de parâmetros em exercícios complementares.

## Verificação ✅

- [x] `pytest` — **88 passed** (33 pré-existentes + 55 novos em `tests/unit/test_reading_addon.py`).
- [x] Migration testada (upgrade + downgrade + re-upgrade) contra Postgres 16 real.
- [x] Teste de integração manual: escada SRS completa, portão de leitura (erro/acerto/conclusão com auto-pausa), isolamento de material no QuizBank, pool do simulado.
- [x] `walkthrough.md` criado na raiz do projeto.
