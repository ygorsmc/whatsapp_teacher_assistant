import asyncio
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchAny

async def test():
    client = AsyncQdrantClient("http://localhost:6333")
    
    # Fake query vector
    vector = [0.1] * 1536  # Azure standard size
    
    search_filter = Filter(
        must=[
            FieldCondition(key="teacher_id", match=MatchValue(value="tea_5bc0faab")),
            FieldCondition(key="course_id",  match=MatchValue(value="crs_ee570174")),
            FieldCondition(key="lesson_id", match=MatchAny(any=["lesson_1"]))
        ]
    )
    
    print("Sending filter:", search_filter)
    try:
        results = await client.search(
            collection_name="documents",
            query_vector=vector,
            query_filter=search_filter,
            limit=2
        )
        print("Results found:", len(results))
    except Exception as e:
        print("Error:", e)

asyncio.run(test())
