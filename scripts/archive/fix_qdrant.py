import asyncio
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import PayloadSchemaType
from src.shared.config import settings

async def fix():
    print("Connecting to Qdrant Cloud...")
    client = AsyncQdrantClient(url=settings.QDRANT_URL, api_key=settings.QDRANT_API_KEY)
    
    await client.create_payload_index(
        collection_name="documents",
        field_name="subject_name",
        field_schema=PayloadSchemaType.KEYWORD
    )
    print("subject_name index created!")

asyncio.run(fix())
