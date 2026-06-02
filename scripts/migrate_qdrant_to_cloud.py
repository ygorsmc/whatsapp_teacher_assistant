import asyncio
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, VectorParams, ScalarQuantization, ScalarQuantizationConfig, ScalarType, PayloadSchemaType
from src.shared.config import settings

async def migrate():
    # 1. Connect to Local Qdrant
    local_client = AsyncQdrantClient(url="http://localhost:6333")
    
    # 2. Connect to Cloud Qdrant (uses credentials from .env)
    print(f"Connecting to Cloud Qdrant at: {settings.QDRANT_URL}")
    cloud_client = AsyncQdrantClient(
        url=settings.QDRANT_URL,
        api_key=settings.QDRANT_API_KEY
    )

    collection_name = "documents"

    # Verify local collection exists
    if not await local_client.collection_exists(collection_name):
        print("Erro: Coleção local 'documents' não existe. Não há nada para migrar.")
        return

    # 3. Drop and Recreate Cloud Collection with strict schema
    print("Recreating Cloud Collection with INT8 Quantization and Payload Indexes...")
    if await cloud_client.collection_exists(collection_name):
        await cloud_client.delete_collection(collection_name)
        
    await cloud_client.create_collection(
        collection_name=collection_name,
        vectors_config=VectorParams(size=1536, distance=Distance.COSINE),
        quantization_config=ScalarQuantization(
            scalar=ScalarQuantizationConfig(
                type=ScalarType.INT8,
                quantile=0.99,
                always_ram=True
            )
        )
    )
    
    # Create mandatory payload indexes for Qdrant Cloud (this fixes the 400 Bad Request error)
    await cloud_client.create_payload_index(collection_name, "teacher_id", PayloadSchemaType.KEYWORD)
    await cloud_client.create_payload_index(collection_name, "course_id", PayloadSchemaType.KEYWORD)
    await cloud_client.create_payload_index(collection_name, "lesson_number", PayloadSchemaType.INTEGER)
    
    print("Cloud collection ready.")

    # 4. Migrate Points in batches
    print("Fetching points from local Qdrant...")
    
    offset = None
    batch_size = 100
    total_migrated = 0

    while True:
        # Scroll through local points
        records, next_offset = await local_client.scroll(
            collection_name=collection_name,
            limit=batch_size,
            offset=offset,
            with_payload=True,
            with_vectors=True
        )

        if not records:
            break

        # Upsert to Cloud
        from qdrant_client.models import PointStruct
        points_to_upsert = [
            PointStruct(
                id=record.id,
                vector=record.vector,
                payload=record.payload
            )
            for record in records
        ]

        await cloud_client.upsert(
            collection_name=collection_name,
            points=points_to_upsert
        )

        total_migrated += len(points_to_upsert)
        print(f"Migrated {total_migrated} points so far...")

        if next_offset is None:
            break
        offset = next_offset

    print(f"✅ Migration complete! Successfully migrated {total_migrated} vectors to Qdrant Cloud.")

if __name__ == "__main__":
    asyncio.run(migrate())
