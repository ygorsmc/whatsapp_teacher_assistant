"""
Qdrant — cliente e ciclo de vida da collection.

F3b (RAG de Alta Precisão): a collection híbrida usa NAMED VECTORS
(`dense` INT8 + `sparse` com IDF modifier) para a fusão RRF server-side.
A collection legada (vetor único sem nome) continua funcionando intocada —
a troca de layout exige recriação e acontece UMA única vez na janela de
reindexação da F8 (`scripts/reindex_all.py`, estratégia documents_v2 + cutover).
"""

from dataclasses import dataclass

import structlog
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    Modifier,
    PayloadSchemaType,
    ScalarQuantization,
    ScalarQuantizationConfig,
    ScalarType,
    SparseVectorParams,
    VectorParams,
)

from src.shared.config import settings

logger = structlog.get_logger(__name__)

qdrant_client = AsyncQdrantClient(
    url=settings.QDRANT_URL,
    api_key=settings.QDRANT_API_KEY
)

# Nomes dos vetores na collection híbrida (F3).
DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "sparse"

# Índices de payload obrigatórios (escopo SEMPRE antes da similaridade).
PAYLOAD_INDEXES: list[tuple[str, PayloadSchemaType]] = [
    ("teacher_id", PayloadSchemaType.KEYWORD),
    ("course_id", PayloadSchemaType.KEYWORD),
    ("subject_name", PayloadSchemaType.KEYWORD),
    ("lesson_number", PayloadSchemaType.INTEGER),
    ("material_type", PayloadSchemaType.KEYWORD),
    # file_id do Drive: usado pelo /delete para apagar os chunks de um arquivo
    # com precisão (filtro indexado, à prova de homônimos).
    ("file_id", PayloadSchemaType.KEYWORD),
]


@dataclass
class CollectionMode:
    """Layout efetivo da collection — decide o caminho do retriever/ingestão."""
    named_dense: bool   # True = named vectors ("dense"); False = vetor legado
    has_sparse: bool    # True = braço esparso disponível (busca híbrida possível)


_mode_cache: dict[str, CollectionMode] = {}


def _quantization_config() -> ScalarQuantization:
    """INT8 Scalar Quantization (~-75% de RAM) — obrigatório no free tier e
    configurável apenas na CRIAÇÃO da collection."""
    return ScalarQuantization(
        scalar=ScalarQuantizationConfig(
            type=ScalarType.INT8,
            quantile=0.99,
            always_ram=True
        )
    )


async def create_payload_indexes(collection_name: str) -> None:
    """Cria os índices de payload (idempotente, best-effort por índice)."""
    for field_name, schema in PAYLOAD_INDEXES:
        try:
            await qdrant_client.create_payload_index(
                collection_name=collection_name,
                field_name=field_name,
                field_schema=schema,
            )
        except Exception:
            # Índice já existe (ou collection antiga) — nunca derruba o init.
            pass


async def create_hybrid_collection(collection_name: str, dense_dimensions: int | None = None) -> None:
    """Cria uma collection HÍBRIDA: named vectors dense (INT8) + sparse (IDF).

    Usada pela F8 (`reindex_all.py`) para o `documents_v2`. O IDF modifier faz
    o Qdrant ponderar os termos esparsos server-side (Contextual BM25).
    """
    await qdrant_client.create_collection(
        collection_name=collection_name,
        vectors_config={
            DENSE_VECTOR_NAME: VectorParams(
                size=dense_dimensions or settings.EMBEDDING_DIMENSIONS,
                distance=Distance.COSINE,
            ),
        },
        sparse_vectors_config={
            SPARSE_VECTOR_NAME: SparseVectorParams(modifier=Modifier.IDF),
        },
        quantization_config=_quantization_config(),
    )
    await create_payload_indexes(collection_name)


async def get_collection_mode(collection_name: str | None = None) -> CollectionMode:
    """Detecta (com cache) o layout da collection — legado ou híbrido."""
    collection_name = collection_name or settings.QDRANT_COLLECTION
    cached = _mode_cache.get(collection_name)
    if cached is not None:
        return cached

    info = await qdrant_client.get_collection(collection_name)
    vectors_config = info.config.params.vectors
    named_dense = isinstance(vectors_config, dict) and DENSE_VECTOR_NAME in vectors_config
    sparse_config = info.config.params.sparse_vectors or {}
    has_sparse = SPARSE_VECTOR_NAME in sparse_config

    mode = CollectionMode(named_dense=named_dense, has_sparse=has_sparse)
    _mode_cache[collection_name] = mode
    return mode


def invalidate_mode_cache(collection_name: str | None = None) -> None:
    """Invalida o cache de layout (usado após criar/cutover de collection)."""
    if collection_name:
        _mode_cache.pop(collection_name, None)
    else:
        _mode_cache.clear()


async def init_qdrant_collection(collection_name: str | None = None):
    """
    Initializes the Qdrant collection with INT8 Scalar Quantization
    to reduce RAM usage by ~75%, mandatory for the free tier.

    Compatibilidade: a collection NOVA criada aqui mantém o layout LEGADO
    (vetor único sem nome) — o layout híbrido só nasce na janela única de
    reindexação (F8). Se a flag HYBRID_SEARCH_ENABLED estiver ligada mas a
    collection não tiver braço esparso, loga aviso e o retriever degrada
    para dense-only (fallback sempre — nunca quebra).
    """
    collection_name = collection_name or settings.QDRANT_COLLECTION
    exists = await qdrant_client.collection_exists(collection_name)
    if not exists:
        await qdrant_client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(size=settings.EMBEDDING_DIMENSIONS, distance=Distance.COSINE),
            quantization_config=_quantization_config(),
        )
        await create_payload_indexes(collection_name)
        invalidate_mode_cache(collection_name)

    # Addon de Livros: o índice de material_type roda SEMPRE (fora do if) para
    # equipar coleções criadas antes do Addon — idempotente e best-effort.
    try:
        await qdrant_client.create_payload_index(
            collection_name=collection_name,
            field_name="material_type",
            field_schema=PayloadSchemaType.KEYWORD
        )
    except Exception:
        pass

    # /delete: índice de file_id também roda SEMPRE (fora do if) para equipar
    # coleções criadas antes desta feature — idempotente e best-effort.
    try:
        await qdrant_client.create_payload_index(
            collection_name=collection_name,
            field_name="file_id",
            field_schema=PayloadSchemaType.KEYWORD
        )
    except Exception:
        pass

    if settings.HYBRID_SEARCH_ENABLED:
        try:
            mode = await get_collection_mode(collection_name)
            if not mode.has_sparse:
                logger.warning(
                    "hybrid_search_flag_on_but_collection_is_legacy",
                    collection=collection_name,
                    hint="rode scripts/reindex_all.py (documents_v2 + cutover); até lá a busca degrada para dense-only",
                )
        except Exception as e:
            logger.warning("collection_mode_detection_failed", error=str(e)[:200])

async def get_qdrant():
    """Provides the configured async Qdrant client."""
    return qdrant_client
