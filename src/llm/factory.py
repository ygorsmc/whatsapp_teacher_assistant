from typing import Dict, Type
from src.llm.interfaces import LLMProvider, EmbeddingProvider

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
    return cls()
