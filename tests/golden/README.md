# Golden Set do RAG (F0)

`golden_rag.jsonl` é o **dataset dourado** que mede o pipeline de recuperação
(`scripts/eval_rag.py`). Sem essa régua, "mais assertivo" é opinião.

## Formato (1 JSON por linha)

```json
{"query": "o que diz a NBR 9457?",
 "course": "Nome EXATO do curso no banco",
 "subject": "Nome da disciplina (ou null)",
 "lesson": 3,
 "material_type": null,
 "expected_substrings": ["NBR 9457", "ladrilho"],
 "expected_sources": ["ManualLadrilhoHidraulico"],
 "category": "termos_exatos"}
```

- `course`/`subject` — resolvidos por nome no Postgres (ou informe
  `teacher_id` + `course_id` direto para pular o lookup).
- `lesson` — ponteiro do aluno simulado (teto da busca broad).
- `expected_substrings` — trechos LITERAIS que devem aparecer no contexto
  recuperado (case-insensitive). Copie do PDF real, não de memória.
- `expected_sources` — nome (ou prefixo) do arquivo esperado no payload.
- `category` — uma de:
  - `termos_exatos` — siglas, "exercício 3.2", fórmulas, números de norma;
  - `conceitual` — perguntas abertas sobre o conteúdo;
  - `excecao_regra` — casos onde a resposta certa é a EXCEÇÃO da regra;
  - `capitulo_livro` — perguntas sobre o material denso (book/workbook);
  - `fora_do_escopo` — perguntas SEM resposta no material (medem a Recusa
    Honesta: o sistema deve abster-se, não inventar). `expected_substrings`
    fica vazio.

  Vertical de exatas (M0 — Cálculo/Física/Estatística…). Só fazem sentido com
  o `PARSER_BACKEND` ciente de matemática indexando LaTeX no chunk:
  - `formula_exata` — recuperar a EQUAÇÃO literal. `expected_substrings` deve
    trazer fragmentos LaTeX como saíram do parser (ex.: `\frac`, `\sqrt`,
    `\lim`) — o braço esparso (`HYBRID_SEARCH_ENABLED`) é quem casa o símbolo;
  - `conceito_quantitativo` — pergunta aberta sobre um conceito de exatas
    ("por que a força centrípeta aponta pro centro?"); mede a explicação;
  - `excecao_matematica` — a resposta certa é a CONDIÇÃO DE CONTORNO/hipótese
    da fórmula (o `excecao_regra` das exatas — protegido pelo guarda de chunk M3).

## Curadoria (obrigatória antes do baseline oficial)

As linhas atuais estão marcadas `"draft": true` (o harness avisa). Para virar
baseline: **40-80 linhas** com perguntas reais (use o `/duvidas_frequentes` e
o histórico de `StudentInteractions` como fonte) e substrings copiadas do
corpus indexado. Distribua entre as categorias — inclua 6-10 fora-de-escopo. As
linhas de exatas (`formula_exata`/`conceito_quantitativo`/`excecao_matematica`)
trazem `PREENCHER_LATEX_*` como lembrete: troque pela fórmula real indexada.

## Execução

Precisa de acesso ao Qdrant + chave do embedder (`.env.prod` ou
`az containerapp exec` no worker-llm — regra da casa):

```bash
python scripts/eval_rag.py --stage dense          # baseline (sistema atual)
python scripts/eval_rag.py --stage hybrid         # + busca híbrida RRF
python scripts/eval_rag.py --stage rerank         # + reranker
python scripts/eval_rag.py --stage crag           # + CRAG-lite/abstenção
python scripts/eval_rag.py --stage hybrid --embedder bge-m3
python scripts/eval_rag.py --stage dense --json > baseline.json
```

Registre os números em `baseline.md` (antes) e `results.md` (depois da F8).
