import asyncio
from src.rag.qdrant_client import get_qdrant
from qdrant_client.models import Filter, FieldCondition, MatchValue

async def check():
    q = await get_qdrant()
    # Scroll first 10 points
    res, _ = await q.scroll("documents", limit=10, with_payload=True)
    for p in res:
        print(f"ID: {p.id}, Payload: {p.payload}")
        break

asyncio.run(check())
