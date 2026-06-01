import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.rag.qdrant_client import get_qdrant
from qdrant_client.models import Filter, FieldCondition, MatchValue

FILE_NAME = "ManualLadrilhoHidraulico.pdf"  # mesmo valor que está no payload "source"

async def delete_old_vectors():
    qdrant = await get_qdrant()

    # Conta quantos vetores existem para esse arquivo antes de deletar
    result = await qdrant.count(
        collection_name="documents",
        count_filter=Filter(
            must=[FieldCondition(key="source", match=MatchValue(value=FILE_NAME))]
        )
    )
    print(f"Vectors found for '{FILE_NAME}': {result.count}")

    if result.count == 0:
        print("Nothing to delete.")
        return

    # Deleta todos os vetores desse arquivo
    await qdrant.delete(
        collection_name="documents",
        points_selector=Filter(
            must=[FieldCondition(key="source", match=MatchValue(value=FILE_NAME))]
        )
    )
    print(f"Deleted {result.count} old vectors.")

    # Confirma que ficou zerado
    after = await qdrant.count(
        collection_name="documents",
        count_filter=Filter(
            must=[FieldCondition(key="source", match=MatchValue(value=FILE_NAME))]
        )
    )
    print(f"Vectors remaining: {after.count}")

asyncio.run(delete_old_vectors())