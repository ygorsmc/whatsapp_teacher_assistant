"""
Wipe Qdrant + reset do file_sync_state — preparação para re-ingestão com Disciplina.

A camada Disciplina adicionou o campo `subject_name` ao payload do Qdrant e um
índice novo. Os vetores antigos (sem subject_name) não casam mais o filtro do
retriever, então a forma limpa é apagar a collection "documents" (recriada com o
índice de subject_name no próximo /sinc_materiais) e zerar o file_sync_state —
senão os arquivos ficam marcados como 'concluído' e NÃO re-ingerem.

Uso (no ambiente com DATABASE_URL e QDRANT_* configurados):
    python scripts/wipe_qdrant.py            # pede confirmação
    python scripts/wipe_qdrant.py --yes      # sem confirmação (CI/scripts)

Depois rode /sinc_materiais (ou o ingestion worker) para reconstruir a base.
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from src.rag.qdrant_client import get_qdrant  # noqa: E402
from src.db.database import AsyncSessionLocal  # noqa: E402

COLLECTION = "documents"


async def wipe() -> None:
    qdrant = await get_qdrant()

    # 1. Qdrant: apaga a collection inteira (será recriada com o índice subject_name).
    exists = await qdrant.collection_exists(COLLECTION)
    if exists:
        count = (await qdrant.count(collection_name=COLLECTION)).count
        await qdrant.delete_collection(collection_name=COLLECTION)
        print(f"🗑️  Qdrant: collection '{COLLECTION}' apagada ({count} vetores).")
    else:
        print(f"ℹ️  Qdrant: collection '{COLLECTION}' não existe (nada a apagar).")

    # 2. file_sync_state: zera para forçar a re-ingestão de todos os arquivos.
    async with AsyncSessionLocal() as session:
        before = (await session.execute(text("SELECT count(*) FROM file_sync_state"))).scalar() or 0
        await session.execute(text("DELETE FROM file_sync_state"))
        await session.commit()
        print(f"🗑️  DB: file_sync_state zerado ({before} registros removidos).")

    print("\n✅ Pronto. Rode /sinc_materiais para reconstruir a base com a Disciplina.")


def main() -> None:
    if "--yes" not in sys.argv:
        resp = input(
            f"Isso vai APAGAR a collection Qdrant '{COLLECTION}' e zerar o file_sync_state. "
            "Digite 'sim' para continuar: "
        ).strip().lower()
        if resp not in ("sim", "s", "yes", "y"):
            print("Abortado.")
            return
    asyncio.run(wipe())


if __name__ == "__main__":
    main()
