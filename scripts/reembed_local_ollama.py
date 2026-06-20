"""Reindex LOCAL enxuto: re-embeda os chunks JÁ gravados no Qdrant com o
embedder atual (`settings.DEFAULT_EMBEDDER_PROVIDER`) numa coleção da dimensão
nova, preservando os IDs uuid5 (idempotência) e os payloads.

Diferente de `scripts/reindex_all.py` (que re-parseia do Drive e cria o layout
híbrido F8), este NÃO re-baixa nem re-parseia nada: lê o texto do payload
(`content`) da coleção de ORIGEM, re-embeda e grava numa coleção de DESTINO
NOVA, preservando os IDs uuid5. Uso pontual para trocar SÓ o embedder/dimensão
no ambiente LOCAL (ex.: azure 1536d → ollama/bge-m3 1024d).

NÃO-DESTRUTIVO de propósito: a coleção de origem fica intacta (rollback). Depois
de validar, aponte `QDRANT_COLLECTION` para o destino no `.env`.

    python scripts/reembed_local_ollama.py --dry-run                # só inspeciona
    python scripts/reembed_local_ollama.py --target documents_bge1024
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main(source: str, target: str, batch_size: int, dry_run: bool) -> None:
    from qdrant_client import models

    from src.llm.factory import get_embedder
    from src.rag.qdrant_client import (
        get_qdrant,
        init_qdrant_collection,
        invalidate_mode_cache,
    )
    from src.shared.config import settings

    source = source or settings.QDRANT_COLLECTION
    qdrant = await get_qdrant()
    embedder = get_embedder()
    print(
        f"Embedder: {settings.DEFAULT_EMBEDDER_PROVIDER} "
        f"({embedder.get_dimensions()}d) | origem: {source} → destino: {target}"
    )

    # 1. Lê todos os pontos da ORIGEM (id + payload), sem vetores.
    points = []
    offset = None
    while True:
        batch, offset = await qdrant.scroll(
            collection_name=source,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        points.extend(batch)
        if offset is None:
            break
    print(f"Lidos {len(points)} pontos da coleção de origem.")

    items = [(p.id, p.payload) for p in points if p.payload and p.payload.get("content")]
    missing = len(points) - len(items)
    if missing:
        print(f"AVISO: {missing} pontos sem 'content' no payload — serão pulados.")

    if dry_run:
        print("dry-run: nada alterado.")
        return
    if not items:
        print("Nada a reindexar.")
        return
    if await qdrant.collection_exists(target):
        existing = (await qdrant.count(collection_name=target, exact=True)).count
        if existing > 0:
            print(f"ERRO: destino '{target}' já tem {existing} pontos — escolha outro --target.")
            return
        print(f"Destino '{target}' já existe e está VAZIO — reaproveitando.")

    # 2. Cria/garante a coleção de DESTINO na dimensão NOVA (init_qdrant_collection
    #    usa settings.EMBEDDING_DIMENSIONS — layout legado, vetor único + INT8;
    #    idempotente). NÃO-DESTRUTIVO: a origem continua intacta.
    await init_qdrant_collection(target)
    invalidate_mode_cache(target)
    print(f"Coleção '{target}' pronta em {embedder.get_dimensions()}d (INT8 + payload indexes).")

    # 3. Re-embeda em lotes e faz upsert preservando id (uuid5) + payload.
    done = 0
    for i in range(0, len(items), batch_size):
        chunk = items[i : i + batch_size]
        vectors = await embedder.aembed_documents([pl["content"] for _, pl in chunk])
        pts = [
            models.PointStruct(id=pid, vector=vec, payload=pl)
            for (pid, pl), vec in zip(chunk, vectors)
        ]
        await qdrant.upsert(collection_name=target, points=pts)
        done += len(pts)
        print(f"  upsert {done}/{len(items)}")

    print(f"OK: {done} pontos reindexados em '{target}'.")
    print(f"Próximo passo: aponte QDRANT_COLLECTION={target} no .env e valide o retrieval.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="", help="coleção de origem (default: QDRANT_COLLECTION)")
    ap.add_argument("--target", default="documents_bge1024", help="coleção de destino (NOVA)")
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    asyncio.run(main(args.source, args.target, args.batch_size, args.dry_run))
