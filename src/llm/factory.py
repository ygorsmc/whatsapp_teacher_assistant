from typing import Dict, Type
from src.llm.interfaces import LLMProvider, EmbeddingProvider
import structlog

_logger = structlog.get_logger(__name__)

_providers: Dict[str, Type[LLMProvider]] = {}
_embedders: Dict[str, Type[EmbeddingProvider]] = {}

def register_provider(name: str, cls: Type[LLMProvider]):
    _providers[name] = cls

def register_embedder(name: str, cls: Type[EmbeddingProvider]):
    _embedders[name] = cls

def get_provider(name: str = None) -> LLMProvider:
    from src.shared.config import settings
    provider_name = name or settings.DEFAULT_LLM_PROVIDER
    cls = _providers.get(provider_name)
    if cls is None:
        raise ValueError(f"Provider '{provider_name}' not registered.")
    return cls()

def get_embedder(name: str = None) -> EmbeddingProvider:
    from src.shared.config import settings
    embedder_name = name or settings.DEFAULT_EMBEDDER_PROVIDER
    cls = _embedders.get(embedder_name)
    if cls is None:
        raise ValueError(f"Embedder '{embedder_name}' not registered.")
    instance = cls()

    # Fail-fast: validate EMBEDDING_DIMENSIONS matches the provider's actual output
    provider_dims = instance.get_dimensions()
    configured_dims = settings.EMBEDDING_DIMENSIONS
    if provider_dims != configured_dims:
        _logger.warning(
            "EMBEDDING_DIMENSIONS mismatch! Qdrant collection may be incompatible.",
            configured=configured_dims,
            provider=embedder_name,
            provider_dimensions=provider_dims,
        )

    return instance

