from abc import ABC, abstractmethod
from typing import List, Any, Optional, Type
from pydantic import BaseModel

class LLMProvider(ABC):
    @abstractmethod
    async def agenerate(self, prompt: str, system_message: Optional[str] = None, history: Optional[List[Any]] = None) -> str:
        """Generates a text response from the model asynchronously."""
        pass

    @abstractmethod
    async def agenerate_structured(self, prompt: str, schema: Type[BaseModel], system_message: Optional[str] = None, history: Optional[List[Any]] = None) -> BaseModel:
        """Generates a structured Pydantic response from the model asynchronously."""
        pass

class EmbeddingProvider(ABC):
    @abstractmethod
    async def aembed_query(self, text: str) -> List[float]:
        """Embeds a single query string asynchronously."""
        pass

    @abstractmethod
    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        """Embeds a list of document strings asynchronously."""
        pass

    @abstractmethod
    def get_dimensions(self) -> int:
        """Returns the vector dimensions produced by this embedding model."""
        pass


class HybridEmbeddingProvider(EmbeddingProvider):
    """Embedder que produz, num ÚNICO forward pass, o vetor denso E os pesos
    esparsos lexicais (F3 — Busca Híbrida Unificada; candidato nº 1: BGE-M3).

    O vetor esparso é um dict {"indices": list[int], "values": list[float]}
    — o formato que o Qdrant consome em named sparse vectors.
    """

    @abstractmethod
    async def aembed_documents_hybrid(self, texts: List[str]) -> tuple[List[List[float]], List[dict]]:
        """Retorna (dense_vectors, sparse_vectors) para uma lista de documentos."""
        pass

    @abstractmethod
    async def aembed_query_hybrid(self, text: str) -> tuple[List[float], dict]:
        """Retorna (dense_vector, sparse_vector) para uma query."""
        pass

