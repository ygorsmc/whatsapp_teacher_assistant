import base64

from src.llm.interfaces import LLMProvider, EmbeddingProvider
from langchain_openai import ChatOpenAI, OpenAIEmbeddings, AzureOpenAIEmbeddings
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage
from src.shared.config import settings
from typing import List, Optional, Any, Type
from pydantic import BaseModel, SecretStr
from src.shared.telemetry import track_event

class BaseLangChainProvider(LLMProvider):
    """
    Classe base (Template Method) para provedores de LLM que utilizam LangChain.
    
    Esta classe encapsula toda a complexidade de integração com o LangChain (como 
    formatação de histórico, passagem de System Message e extração de saídas estruturadas).
    Desta forma, a camada de negócio do projeto (ex: RAG) interage apenas com a interface
    limpa do LLMProvider (entradas em string, saídas em Pydantic nativo), sem vazar
    dependências do LangChain para o resto do sistema.
    """
    
    llm: Any
    
    async def agenerate_structured(self, prompt: str, schema: Type[BaseModel], system_message: Optional[str] = None, history: Optional[List[BaseMessage]] = None) -> BaseModel:
        """
        Gera uma resposta estritamente tipada utilizando Function Calling (JSON Mode) da IA.
        O LangChain é utilizado aqui internamente apenas para envelopar a chamada.
        """
        messages: list[BaseMessage] = []
        if system_message:
            messages.append(SystemMessage(content=system_message))
        if history:
            messages.extend(history)
            
        messages.append(HumanMessage(content=prompt))
        
        structured_llm = self.llm.with_structured_output(schema)
        response = await structured_llm.ainvoke(messages)
        return response

    async def agenerate(self, prompt: str, system_message: Optional[str] = None, history: Optional[List[BaseMessage]] = None) -> str:
        """
        Gera uma resposta em texto livre.
        Aplica o padrão Template Method chamando `self._track_usage()` para que as classes filhas
        possam contabilizar tokens (telemetria/FinOps) de maneira agnóstica sem repetir a lógica principal.
        """
        messages: list[BaseMessage] = []
        if system_message:
            messages.append(SystemMessage(content=system_message))
        if history:
            messages.extend(history)
            
        messages.append(HumanMessage(content=prompt))
        
        response = await self.llm.ainvoke(messages)
        self._track_usage(response)
        return self._normalize_content(response.content)

    async def agenerate_vision(self, prompt: str, image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
        """Geração MULTIMODAL: prompt + imagem inline (data URI base64).

        Usado pela correção de redação por foto (/corrigir, Vertical de
        Idiomas). O LangChain traduz o bloco `image_url` para o formato nativo
        do provider (inline_data no Gemini, image_url na OpenAI)."""
        b64 = base64.b64encode(image_bytes).decode("ascii")
        message = HumanMessage(content=[
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
        ])
        response = await self.llm.ainvoke([message])
        self._track_usage(response)
        return self._normalize_content(response.content)

    @staticmethod
    def _normalize_content(content: Any) -> str:
        """Achata a resposta do LangChain (str ou lista de parts) em texto puro."""
        if isinstance(content, list):
            text_parts = [part.get("text", "") for part in content if isinstance(part, dict) and "text" in part]
            text_parts.extend([str(part) for part in content if isinstance(part, str)])
            content = " ".join(text_parts)

        if not content or not content.strip():
            content = "Desculpe, a inteligência artificial bloqueou a resposta ou retornou vazio."

        return content

    def _track_usage(self, response: Any):
        """
        Hook Method: Extrai e rastreia a telemetria (Uso de Tokens).
        Deve ser obrigatoriamente sobrescrito pelas classes filhas, pois cada provedor
        (OpenAI, Gemini, DeepSeek) retorna os metadados de tokens em um formato diferente.
        """
        pass

class OpenAILLMProvider(BaseLangChainProvider):
    """Implementação concreta para modelos da OpenAI (ex: gpt-4o-mini)."""
    
    def __init__(self, model_name: str = "gpt-4o-mini"):
        # Placeholder implementation - can be swapped to Gemini/DeepSeek
        self.llm = ChatOpenAI(
            model=model_name, 
            temperature=settings.LLM_TEMPERATURE,
            api_key=SecretStr(settings.OPENAI_API_KEY) if settings.OPENAI_API_KEY else None,
            max_retries=settings.LLM_MAX_RETRIES
        )

    def _track_usage(self, response: Any):
        track_event("llm_generation", {
            "model": self.llm.model_name,
            "prompt_tokens": response.response_metadata.get("token_usage", {}).get("prompt_tokens", 0),
            "completion_tokens": response.response_metadata.get("token_usage", {}).get("completion_tokens", 0),
        })

class DeepSeekLLMProvider(BaseLangChainProvider):
    """Implementação concreta para modelos DeepSeek (utiliza interface compatível com OpenAI)."""
    
    def __init__(self, model_name: str = "deepseek-v4-flash"):
        if not settings.DEEPSEEK_API_KEY:
            raise ValueError("DEEPSEEK_API_KEY is not set in environment.")
            
        self.llm = ChatOpenAI(
            model=model_name, 
            temperature=settings.LLM_TEMPERATURE,
            api_key=SecretStr(settings.DEEPSEEK_API_KEY),
            base_url=settings.DEEPSEEK_BASE_URL,
            max_retries=settings.LLM_MAX_RETRIES
        )

    def _track_usage(self, response: Any):
        track_event("llm_generation", {
            "model": self.llm.model_name,
            "prompt_tokens": response.response_metadata.get("token_usage", {}).get("prompt_tokens", 0),
            "completion_tokens": response.response_metadata.get("token_usage", {}).get("completion_tokens", 0),
        })



class OpenAIEmbeddingProvider(EmbeddingProvider):
    DIMENSIONS = 1536

    def __init__(self, model_name: str = "text-embedding-3-small"):
        self.embeddings = OpenAIEmbeddings(
            model=model_name,
            api_key=SecretStr(settings.OPENAI_API_KEY) if settings.OPENAI_API_KEY else None,
            max_retries=3
        )

    def get_dimensions(self) -> int:
        return self.DIMENSIONS

    async def aembed_query(self, text: str) -> List[float]:
        return await self.embeddings.aembed_query(text)

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        return await self.embeddings.aembed_documents(texts)


class GeminiLLMProvider(BaseLangChainProvider):
    """Implementação concreta para modelos do Google Gemini (ex: gemini-3.1-flash-lite)."""
    
    def __init__(self, model_name: str = "gemini-3.1-flash-lite"):
        if not settings.GOOGLE_API_KEY:
            raise ValueError("GOOGLE_API_KEY is not set in environment.")
        
        self.llm = ChatGoogleGenerativeAI(
            model=model_name,
            temperature=settings.LLM_TEMPERATURE,
            google_api_key=settings.GOOGLE_API_KEY,
            max_retries=settings.LLM_MAX_RETRIES
        )



    def _track_usage(self, response: Any):
        usage = response.response_metadata.get("token_usage", {}) if response.response_metadata else {}
        if isinstance(usage, dict):
            prompt_tokens = usage.get("prompt_token_count", 0)
            completion_tokens = usage.get("candidates_token_count", 0)
        else:
            prompt_tokens = getattr(usage, "prompt_token_count", 0)
            completion_tokens = getattr(usage, "candidates_token_count", 0)
        
        track_event("llm_generation", {
            "model": getattr(self.llm, "model", "unknown"),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        })

class GeminiEmbeddingProvider(EmbeddingProvider):
    DIMENSIONS = 768

    def __init__(self, model_name: str = "models/text-embedding-004"):
        if not settings.GOOGLE_API_KEY:
            raise ValueError("GOOGLE_API_KEY is not set in environment.")
            
        self.embeddings = GoogleGenerativeAIEmbeddings(
            model=model_name,
            api_key=SecretStr(settings.GOOGLE_API_KEY)
        )

    def get_dimensions(self) -> int:
        return self.DIMENSIONS

    async def aembed_query(self, text: str) -> List[float]:
        return await self.embeddings.aembed_query(text)

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        return await self.embeddings.aembed_documents(texts)

class LocalEmbeddingProvider(EmbeddingProvider):
    DIMENSIONS = 384

    embeddings: Any

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        # self.embeddings = HuggingFaceEmbeddings(model_name=model_name)
        raise NotImplementedError("Local embeddings require langchain-huggingface")

    def get_dimensions(self) -> int:
        return self.DIMENSIONS

    async def aembed_query(self, text: str) -> List[float]:
        return await self.embeddings.aembed_query(text)

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        return await self.embeddings.aembed_documents(texts)

class AzureEmbeddingProvider(EmbeddingProvider):
    DIMENSIONS = 1536

    def __init__(self, deployment_name: str = "text-embedding-3-small"):
        if not settings.AZURE_OPENAI_API_KEY or not settings.AZURE_OPENAI_ENDPOINT:
            raise ValueError("AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT must be set.")
            
        self.embeddings = AzureOpenAIEmbeddings(
            azure_deployment=deployment_name,
            api_version=settings.AZURE_OPENAI_API_VERSION,
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=SecretStr(settings.AZURE_OPENAI_API_KEY),
        )

    def get_dimensions(self) -> int:
        return self.DIMENSIONS

    async def aembed_query(self, text: str) -> List[float]:
        return await self.embeddings.aembed_query(text)

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        return await self.embeddings.aembed_documents(texts)


class OllamaEmbeddingProvider(EmbeddingProvider):
    """Embedder DENSO via Ollama local (`/api/embed`).

    Serve o MESMO modelo BGE-M3 do provider 'bge-m3', mas hospedado no Ollama
    local — útil enquanto não há um endpoint BGE-M3 hospedado. Diferença: o
    Ollama devolve apenas o vetor DENSO (1024d), sem o braço esparso; logo a
    busca híbrida (HYBRID_SEARCH_ENABLED) só teria o sparse via fallback BM25
    local (fastembed). Para o braço esparso real, migrar para 'bge-m3' apontando
    BGE_M3_ENDPOINT a um servidor que devolva dense+sparse (mesmo modelo →
    vetores densos compatíveis, sem reindex para o braço denso)."""

    def __init__(self):
        import httpx  # import tardio
        from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential

        self._httpx = httpx
        self._AsyncRetrying = AsyncRetrying
        self._stop = stop_after_attempt(3)
        self._wait = wait_exponential(multiplier=1, min=1, max=10)
        self.base_url = settings.OLLAMA_BASE_URL.rstrip("/")
        self.model = settings.OLLAMA_EMBED_MODEL
        self.dimensions = settings.OLLAMA_EMBED_DIMENSIONS
        self.timeout = settings.OLLAMA_TIMEOUT_SECONDS

    def get_dimensions(self) -> int:
        return self.dimensions

    async def _embed(self, texts: List[str]) -> List[List[float]]:
        async for attempt in self._AsyncRetrying(wait=self._wait, stop=self._stop, reraise=True):
            with attempt:
                async with self._httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(
                        f"{self.base_url}/api/embed",
                        json={"model": self.model, "input": texts},
                    )
                    resp.raise_for_status()
                    data = resp.json()
                embeddings = data.get("embeddings")
                if not embeddings or len(embeddings) != len(texts):
                    raise ValueError(
                        f"Ollama embed: esperado {len(texts)} vetores, recebido "
                        f"{len(embeddings) if embeddings else 0}"
                    )
                return [list(map(float, vec)) for vec in embeddings]
        raise RuntimeError("unreachable")  # pragma: no cover (reraise=True acima)

    async def aembed_query(self, text: str) -> List[float]:
        return (await self._embed([text]))[0]

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        return await self._embed(texts)


from src.llm.factory import register_provider, register_embedder  # noqa: E402
from src.llm.bge_m3 import BGEM3EmbeddingProvider  # noqa: E402
register_provider("openai", OpenAILLMProvider)
register_embedder("openai", OpenAIEmbeddingProvider)
register_provider("gemini", GeminiLLMProvider)
register_provider("deepseek", DeepSeekLLMProvider)
register_embedder("azure", AzureEmbeddingProvider)
# F3a (RAG Preciso): embedder híbrido (denso 1024 + esparso em 1 passo).
# Trocar exige EMBEDDING_DIMENSIONS=1024 + reindexação (janela única da F8).
register_embedder("bge-m3", BGEM3EmbeddingProvider)
# BGE-M3 denso via Ollama local (1024d) — mesmo modelo, sem braço esparso.
register_embedder("ollama", OllamaEmbeddingProvider)
