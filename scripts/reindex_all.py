"""
Reindexação Única (F8) — documents_v2 + cutover por alias.

F1 (chunks estruturais), F2 (contexto) e F3 (embedder/vetores esparsos) mudam
o ÍNDICE — por isso vivem atrás de flags e reindexam UMA única vez, aqui.
Janela recomendada: madrugada (03:00 BRT — regra da casa: mexer no índice em
horário de pico degrada a latência HNSW para todos os alunos).

Fluxo completo:

  1. `--create`   cria a collection nova (documents_v2) no layout HÍBRIDO
                  (named vectors: dense INT8 + sparse IDF) + payload indexes.
  2. (operador)   sobe o ingestion worker apontando para a v2 e com as flags
                  da nova era ligadas:
                      QDRANT_COLLECTION=documents_v2
                      CONTEXTUAL_RETRIEVAL_ENABLED=true
                      HYBRID_SEARCH_ENABLED=true
                      (DEFAULT_EMBEDDER_PROVIDER/EMBEDDING_DIMENSIONS conforme
                       a DECISÃO DO EVAL da F3a — ex.: bge-m3 / 1024)
  3. `--enqueue`  reseta o file_sync_state para 'pendente' (PRESERVANDO
                  dead_letter) e dispara a sincronização do Drive — os
                  arquivos reentram na ingestion_queue respeitando a
                  concorrência existente (máx. 2 chamadas DI por réplica).
  4. `--status`   acompanha o progresso (estados no PG + contagem nas duas
                  collections) e o custo logado por arquivo
                  (`contextual_enrichment_cost` no log do worker).
  5. `--cutover`  valida contagens, apaga a collection antiga e cria o ALIAS
                  documents → documents_v2 (o app continua lendo "documents"
                  sem redeploy). Exige --yes.

Uso:
    python scripts/reindex_all.py --create
    python scripts/reindex_all.py --enqueue --yes
    python scripts/reindex_all.py --status
    python scripts/reindex_all.py --cutover --yes
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OLD_COLLECTION = "documents"
NEW_COLLECTION = "documents_v2"


async def step_create():
    from src.rag.qdrant_client import create_hybrid_collection, get_qdrant, invalidate_mode_cache
    from src.shared.config import settings

    qdrant = await get_qdrant()
    if await qdrant.collection_exists(NEW_COLLECTION):
        print(f"ℹ️  '{NEW_COLLECTION}' já existe — nada a criar.")
        return
    await create_hybrid_collection(NEW_COLLECTION, dense_dimensions=settings.EMBEDDING_DIMENSIONS)
    invalidate_mode_cache(NEW_COLLECTION)
    print(f"✅ Collection '{NEW_COLLECTION}' criada (dense {settings.EMBEDDING_DIMENSIONS} dims INT8 + sparse IDF + payload indexes).")
    print("   Lembrete: a dimensão deve refletir a DECISÃO DO EVAL (bge-m3 → EMBEDDING_DIMENSIONS=1024).")


async def step_enqueue():
    from sqlalchemy import text
    from src.db.database import AsyncSessionLocal
    from src.workers.ingestion_worker import sync_google_drive

    async with AsyncSessionLocal() as session:
        res = await session.execute(
            text("""
                UPDATE file_sync_state
                SET status = 'pendente', attempts = 0
                WHERE status != 'dead_letter'
            """)
        )
        await session.commit()
        print(f"🔄 file_sync_state: {res.rowcount} arquivo(s) resetados para 'pendente' (dead_letter preservado).")

    print("📥 Disparando a sincronização do Drive (reenfileira na ingestion_queue)...")
    result = await sync_google_drive()
    print(f"✅ Sync disparada. {result if result else ''}")
    print("   O ingestion worker consome a fila com a concorrência de sempre.")
    print("   ⚠️  Confirme que o worker roda com QDRANT_COLLECTION=documents_v2 e as flags novas ligadas.")


async def step_status():
    from sqlalchemy import text
    from src.db.database import AsyncSessionLocal
    from src.rag.qdrant_client import get_qdrant

    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            text("SELECT status, count(*) FROM file_sync_state GROUP BY status ORDER BY status")
        )).fetchall()
    print("── file_sync_state ──")
    for status, count in rows:
        print(f"  {status:<12} {count}")

    qdrant = await get_qdrant()
    print("── Qdrant ──")
    for name in (OLD_COLLECTION, NEW_COLLECTION):
        if await qdrant.collection_exists(name):
            count = (await qdrant.count(collection_name=name)).count
            info = await qdrant.get_collection(name)
            sparse = bool(info.config.params.sparse_vectors)
            print(f"  {name:<14} {count} vetores  (sparse: {'sim' if sparse else 'não'})")
        else:
            print(f"  {name:<14} (não existe)")


async def step_cutover(assume_yes: bool):
    from qdrant_client.models import CreateAlias, CreateAliasOperation
    from sqlalchemy import text
    from src.db.database import AsyncSessionLocal
    from src.rag.qdrant_client import get_qdrant, invalidate_mode_cache

    qdrant = await get_qdrant()
    if not await qdrant.collection_exists(NEW_COLLECTION):
        print(f"❌ '{NEW_COLLECTION}' não existe — rode --create e a reindexação antes.")
        return

    new_count = (await qdrant.count(collection_name=NEW_COLLECTION)).count
    old_count = (await qdrant.count(collection_name=OLD_COLLECTION)).count if await qdrant.collection_exists(OLD_COLLECTION) else 0

    async with AsyncSessionLocal() as session:
        pending = (await session.execute(
            text("SELECT count(*) FROM file_sync_state WHERE status NOT IN ('concluído', 'dead_letter')")
        )).scalar()

    print(f"Validação: {NEW_COLLECTION}={new_count} vetores | {OLD_COLLECTION}={old_count} vetores | arquivos não-concluídos={pending}")
    if new_count == 0:
        print("❌ Collection nova vazia — abortando cutover.")
        return
    if pending:
        print(f"⚠️  Ainda há {pending} arquivo(s) não concluídos — o cutover deixaria material de fora.")
    # Chunks maiores (F1: 800→2000 chars) reduzem a contagem total — a régua
    # de sanidade aqui é "não-vazio e ingestão concluída", não paridade 1:1.

    if not assume_yes:
        answer = input(f"Apagar '{OLD_COLLECTION}' e criar o alias {OLD_COLLECTION} → {NEW_COLLECTION}? [digite SIM] ")
        if answer.strip().upper() != "SIM":
            print("Cutover cancelado.")
            return

    if await qdrant.collection_exists(OLD_COLLECTION):
        await qdrant.delete_collection(OLD_COLLECTION)
        print(f"🗑️  Collection antiga '{OLD_COLLECTION}' apagada ({old_count} vetores).")

    await qdrant.update_collection_aliases(
        change_aliases_operations=[
            CreateAliasOperation(create_alias=CreateAlias(collection_name=NEW_COLLECTION, alias_name=OLD_COLLECTION)),
        ]
    )
    invalidate_mode_cache()
    print(f"✅ Alias '{OLD_COLLECTION}' → '{NEW_COLLECTION}' criado. O app continua lendo '{OLD_COLLECTION}' sem redeploy.")
    print("   Rode a régua: python scripts/eval_rag.py --stage rerank (e registre em tests/golden/results.md).")


def main():
    parser = argparse.ArgumentParser(description="Reindexação única (F8): documents_v2 + cutover")
    parser.add_argument("--create", action="store_true", help="cria a collection híbrida documents_v2")
    parser.add_argument("--enqueue", action="store_true", help="reseta file_sync_state e reenfileira a ingestão")
    parser.add_argument("--status", action="store_true", help="mostra progresso (PG + Qdrant)")
    parser.add_argument("--cutover", action="store_true", help="valida, apaga a antiga e cria o alias")
    parser.add_argument("--yes", action="store_true", help="não pede confirmação (CI/scripts)")
    args = parser.parse_args()

    if not any((args.create, args.enqueue, args.status, args.cutover)):
        parser.print_help()
        return

    async def run():
        if args.create:
            await step_create()
        if args.enqueue:
            if not args.yes:
                answer = input("Resetar o file_sync_state e reenfileirar TODOS os arquivos? [digite SIM] ")
                if answer.strip().upper() != "SIM":
                    print("Enqueue cancelado.")
                    return
            await step_enqueue()
        if args.status:
            await step_status()
        if args.cutover:
            await step_cutover(args.yes)

    asyncio.run(run())


if __name__ == "__main__":
    main()
