"""
Eval RAG (F0) — a régua que decide TODAS as fases do RAG de Alta Precisão.

Harness async com núcleo DETERMINÍSTICO (sem custo de LLM-juiz), no espírito
do RAG Triad, fragmentado por camada para diagnóstico (a falha foi na
recuperação, na fusão ou na geração?):

  - Context Recall@k ........ algum expected_substring apareceu no contexto?
  - Hit de fonte ............ algum expected_source entre os hits?
  - MRR ..................... posição do primeiro acerto
  - Taxa de abstenção correta  (categoria fora_do_escopo, estágio crag)
  - Falsos-recusados ........ abstenções em perguntas COM resposta
  - Latência p50/p95 ........ critério da decisão de embedder (F3a)

Uso (exige QDRANT_URL/API keys do embedder no ambiente — rodar com .env.prod
ou via `az containerapp exec` no worker, regra da casa):

    python scripts/eval_rag.py --stage dense                # baseline
    python scripts/eval_rag.py --stage hybrid               # + fusão RRF
    python scripts/eval_rag.py --stage rerank               # + juiz
    python scripts/eval_rag.py --stage crag                 # + Recusa Honesta
    python scripts/eval_rag.py --stage hybrid --embedder bge-m3
    python scripts/eval_rag.py --stage dense --json > out.json   # diff entre execuções

Faithfulness (OPCIONAL, LLM-juiz barato via Ragas): adicionar --ragas E
RAGAS_EVAL_ENABLED=true. Nunca roda em CI por padrão.
"""

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

GOLDEN_PATH_DEFAULT = "tests/golden/golden_rag.jsonl"

OUT_OF_SCOPE_CATEGORY = "fora_do_escopo"

# Estágios cumulativos — cada um mede a contribuição de UMA fase.
STAGES = {
    "dense":  {"hybrid": False, "rerank": False, "crag": False},
    "hybrid": {"hybrid": True,  "rerank": False, "crag": False},
    "rerank": {"hybrid": True,  "rerank": True,  "crag": False},
    "crag":   {"hybrid": True,  "rerank": True,  "crag": True},
}


# ==============================================================================
# Métricas puras (cobertas por testes unitários — sem I/O)
# ==============================================================================

def substring_hit(texts: list[str], expected_substrings: list[str]) -> bool:
    """Context Recall: algum expected_substring aparece em algum texto?"""
    if not expected_substrings:
        return False
    haystack = "\n".join(texts).casefold()
    return any(sub.casefold() in haystack for sub in expected_substrings if sub)


def source_hit(sources: list[str], expected_sources: list[str]) -> bool:
    """Hit de fonte: algum expected_source entre as fontes recuperadas
    (match por substring, case-insensitive — 'aula3' casa 'Aula3_v2.pdf')."""
    if not expected_sources:
        return False
    normalized = [s.casefold() for s in sources if s]
    return any(
        any(exp.casefold() in src for src in normalized)
        for exp in expected_sources if exp
    )


def first_hit_rank(texts: list[str], expected_substrings: list[str]) -> Optional[int]:
    """Posição (1-based) do primeiro chunk que contém um expected_substring."""
    for position, text in enumerate(texts, start=1):
        lowered = (text or "").casefold()
        if any(sub.casefold() in lowered for sub in expected_substrings if sub):
            return position
    return None


def mean_reciprocal_rank(ranks: list[Optional[int]]) -> float:
    """MRR sobre as posições do 1º acerto (None = sem acerto → 0)."""
    if not ranks:
        return 0.0
    return sum((1.0 / r) if r else 0.0 for r in ranks) / len(ranks)


def percentile(values: list[float], pct: float) -> float:
    """Percentil simples (interpolação nearest-rank)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


def summarize(records: list[dict]) -> dict:
    """Agrega os resultados por categoria e no geral (helper puro)."""
    in_scope = [r for r in records if r["category"] != OUT_OF_SCOPE_CATEGORY]
    out_scope = [r for r in records if r["category"] == OUT_OF_SCOPE_CATEGORY]

    def recall_of(rows):
        return (sum(1 for r in rows if r["recall_hit"]) / len(rows)) if rows else None

    by_category: dict[str, dict] = {}
    for record in records:
        bucket = by_category.setdefault(record["category"], {"total": 0, "hits": 0})
        bucket["total"] += 1
        bucket["hits"] += 1 if record["recall_hit"] else 0
    for bucket in by_category.values():
        bucket["recall"] = bucket["hits"] / bucket["total"]

    return {
        "total_queries": len(records),
        "recall_at_k": recall_of(in_scope),
        "source_hit_rate": (sum(1 for r in in_scope if r["source_hit"]) / len(in_scope)) if in_scope else None,
        "mrr": mean_reciprocal_rank([r["rank"] for r in in_scope]),
        "by_category": by_category,
        "correct_abstention_rate": (
            sum(1 for r in out_scope if r["abstained"]) / len(out_scope)
        ) if out_scope else None,
        "false_refusal_rate": (
            sum(1 for r in in_scope if r["abstained"]) / len(in_scope)
        ) if in_scope else None,
        "latency_ms_p50": percentile([r["latency_ms"] for r in records], 50),
        "latency_ms_p95": percentile([r["latency_ms"] for r in records], 95),
    }


def load_golden(path: str) -> list[dict]:
    """Carrega o dataset dourado (JSONL); linhas vazias/comentadas são puladas."""
    items = []
    with open(path, encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line or line.startswith("//") or line.startswith("#"):
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number} — JSON inválido: {exc}") from exc
    return items


# ==============================================================================
# Runner async
# ==============================================================================

async def _resolve_scope(item: dict, scope_cache: dict) -> Optional[dict]:
    """Resolve (teacher_id, course_id) do item: direto do JSONL ou via lookup
    do nome do curso no Postgres."""
    if item.get("teacher_id") and item.get("course_id"):
        return {"teacher_id": item["teacher_id"], "course_id": item["course_id"]}

    course_name = item.get("course")
    if not course_name:
        return None
    if course_name in scope_cache:
        return scope_cache[course_name]

    from sqlalchemy import text as sql_text
    from src.db.database import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        res = await session.execute(
            sql_text('SELECT id, teacher_id FROM "Courses" WHERE name = :name LIMIT 1'),
            {"name": course_name},
        )
        row = res.fetchone()
    scope = {"teacher_id": row[1], "course_id": row[0]} if row else None
    scope_cache[course_name] = scope
    return scope


async def run_eval(args) -> dict:
    from src.shared.config import settings

    stage_config = STAGES[args.stage]

    # Liga/desliga as camadas do funil para medir a contribuição de CADA fase.
    settings.HYBRID_SEARCH_ENABLED = stage_config["hybrid"]
    settings.CRAG_LITE_ENABLED = stage_config["crag"]
    if not stage_config["rerank"]:
        settings.RERANKER_PROVIDER = "none"
    elif settings.RERANKER_PROVIDER in ("none", ""):
        print("⚠️  Estágio com rerank, mas RERANKER_PROVIDER=none — configure cohere|voyage|open.", file=sys.stderr)

    if args.embedder:
        settings.DEFAULT_EMBEDDER_PROVIDER = args.embedder
        if args.embedder == "bge-m3":
            settings.EMBEDDING_DIMENSIONS = 1024

    import src.llm.providers  # noqa: F401 — registra os providers na factory
    from src.rag.retriever import retrieve_for_chat

    golden = load_golden(args.golden)
    drafts = sum(1 for item in golden if item.get("draft"))
    if drafts:
        print(
            f"⚠️  {drafts}/{len(golden)} linha(s) marcadas como draft — cure o dataset com "
            "perguntas/substrings do SEU corpus antes de tratar os números como baseline oficial.",
            file=sys.stderr,
        )

    records = []
    scope_cache: dict = {}
    top_k = args.top_k

    for item in golden:
        scope = await _resolve_scope(item, scope_cache)
        if not scope:
            print(f"⚠️  curso não resolvido, pulando: {item.get('course')!r} — {item['query'][:60]}", file=sys.stderr)
            continue

        started = time.perf_counter()
        result = await retrieve_for_chat(
            teacher_id=scope["teacher_id"],
            course_id=scope["course_id"],
            subject_name=item.get("subject"),
            current_lesson_number=int(item.get("lesson", 1)),
            query=item["query"],
            top_k=top_k,
            material_type=item.get("material_type"),
        )
        latency_ms = (time.perf_counter() - started) * 1000

        texts = [hit.payload.get("content", "") for hit in result.hits]
        sources = [hit.payload.get("source", "") for hit in result.hits]
        expected_subs = item.get("expected_substrings", [])

        records.append({
            "query": item["query"],
            "category": item.get("category", "conceitual"),
            "recall_hit": substring_hit(texts, expected_subs),
            "source_hit": source_hit(sources, item.get("expected_sources", [])),
            "rank": first_hit_rank(texts, expected_subs),
            "abstained": result.abstained,
            "evidence": result.evidence,
            "best_score": result.best_score,
            "used_rewrite": result.used_rewrite,
            "latency_ms": latency_ms,
            "hits": len(result.hits),
        })

    summary = summarize(records)
    summary["stage"] = args.stage
    summary["embedder"] = args.embedder or "default"
    summary["top_k"] = top_k
    summary["golden_drafts"] = drafts

    if args.ragas:
        summary["faithfulness"] = await run_faithfulness(records, args)

    return {"summary": summary, "records": records}


async def run_faithfulness(records: list[dict], args) -> Optional[float]:
    """Métrica de Faithfulness via Ragas (OPCIONAL — LLM-juiz custa).

    Exige `pip install ragas` E a flag RAGAS_EVAL_ENABLED=true. Mantida fora
    do caminho padrão de propósito: o núcleo do eval é determinístico."""
    from src.shared.config import settings
    if not settings.RAGAS_EVAL_ENABLED:
        print("⚠️  --ragas pedido mas RAGAS_EVAL_ENABLED=false — pulando faithfulness.", file=sys.stderr)
        return None
    try:
        import ragas  # noqa: F401
    except ImportError:
        print("⚠️  pacote `ragas` não instalado (pip install ragas) — pulando faithfulness.", file=sys.stderr)
        return None
    # Implementação plena requer gerar respostas finais por pergunta (custo de
    # geração) — roda apenas sob demanda em amostras pequenas.
    print("ℹ️  Faithfulness: rode com uma amostra pequena (--golden custom) — juiz: DEFAULT_LLM_PROVIDER.", file=sys.stderr)
    return None


def print_report(payload: dict):
    summary = payload["summary"]
    print("\n══════════ EVAL RAG — RESULTADO ══════════")
    print(f"estágio: {summary['stage']}   embedder: {summary['embedder']}   top_k: {summary['top_k']}")
    print(f"queries: {summary['total_queries']}   (drafts no dataset: {summary['golden_drafts']})")

    def fmt(value):
        return f"{value * 100:.1f}%" if value is not None else "n/a"

    print(f"\nContext Recall@k (em escopo): {fmt(summary['recall_at_k'])}")
    print(f"Hit de fonte:                 {fmt(summary['source_hit_rate'])}")
    print(f"MRR:                          {summary['mrr']:.3f}")
    print(f"Abstenção correta (fora):     {fmt(summary['correct_abstention_rate'])}")
    print(f"Falsos-recusados (escopo):    {fmt(summary['false_refusal_rate'])}")
    print(f"Latência p50/p95:             {summary['latency_ms_p50']:.0f}ms / {summary['latency_ms_p95']:.0f}ms")

    print("\nPor categoria:")
    for category, bucket in sorted(summary["by_category"].items()):
        print(f"  {category:<20} {bucket['hits']}/{bucket['total']}  ({bucket['recall'] * 100:.0f}%)")

    misses = [r for r in payload["records"] if not r["recall_hit"] and r["category"] != OUT_OF_SCOPE_CATEGORY]
    if misses:
        print("\nFalhas de recall (diagnóstico):")
        for record in misses[:10]:
            print(f"  ✗ [{record['category']}] {record['query'][:70]} (hits={record['hits']}, evidence={record['evidence']})")


def main():
    parser = argparse.ArgumentParser(description="Eval do pipeline RAG (F0)")
    parser.add_argument("--stage", choices=list(STAGES), default="dense",
                        help="camadas ativas: dense | hybrid | rerank | crag")
    parser.add_argument("--embedder", default=None, help="override do embedder (ex.: bge-m3, azure)")
    parser.add_argument("--golden", default=GOLDEN_PATH_DEFAULT, help="caminho do dataset dourado (jsonl)")
    parser.add_argument("--top-k", type=int, default=5, dest="top_k")
    parser.add_argument("--json", action="store_true", help="saída JSON (para diff entre execuções)")
    parser.add_argument("--ragas", action="store_true", help="faithfulness via Ragas (flag RAGAS_EVAL_ENABLED)")
    args = parser.parse_args()

    payload = asyncio.run(run_eval(args))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        print_report(payload)


if __name__ == "__main__":
    main()
