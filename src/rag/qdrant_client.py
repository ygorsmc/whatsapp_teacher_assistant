from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, VectorParams, ScalarQuantization, ScalarQuantizationConfig, ScalarType
from src.shared.config import settings

qdrant_client = AsyncQdrantClient(
    url=settings.QDRANT_URL,
    api_key=settings.QDRANT_API_KEY
)

async def init_qdrant_collection(collection_name: str = "documents"):
    """
    Initializes the Qdrant collection with INT8 Scalar Quantization 
    to reduce RAM usage by ~75%, mandatory for the free tier.
    """
    exists = await qdrant_client.collection_exists(collection_name)
    if not exists:
        await qdrant_client.create_collection(
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

async def get_qdrant():
    """Provides the configured async Qdrant client."""
    return qdrant_client
