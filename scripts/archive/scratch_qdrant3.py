import asyncio
from src.rag.qdrant_client import get_qdrant
from qdrant_client.models import Filter, FieldCondition, MatchValue

async def check():
    q = await get_qdrant()
    # Scroll points for Design Thinking lesson 1
    f = Filter(must=[FieldCondition(key="subject_name", match=MatchValue(value="Design Thinking")), FieldCondition(key="lesson_number", match=MatchValue(value=1))])
    res, _ = await q.scroll("documents", scroll_filter=f, limit=5, with_payload=True)
    for p in res:
        print(f"Source: {p.payload.get('source')}, Content preview: {p.payload.get('content')[:100]}")
        break

asyncio.run(check())
