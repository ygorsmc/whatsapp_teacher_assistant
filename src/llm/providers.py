from src.llm.interfaces import LLMProvider, EmbeddingProvider
from langchain_openai import ChatOpenAI, OpenAIEmbeddings, AzureOpenAIEmbeddings
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage
from src.shared.config import settings
from typing import List, Optional, Any, Type
from pydantic import BaseModel
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
    
    async def agenerate_structured(self, prompt: str, schema: Type[BaseModel], system_message: Optional[str] = None, history: Optional[List[BaseMessage]] = None) -> BaseModel:
        """
        Gera uma resposta estritamente tipada utilizando Function Calling (JSON Mode) da IA.
        O LangChain é utilizado aqui internamente apenas para envelopar a chamada.
        """
        messages = []
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
        messages = []
        if system_message:
            messages.append(SystemMessage(content=system_message))
        if history:
            messages.extend(history)
            
        messages.append(HumanMessage(content=prompt))
        
        response = await self.llm.ainvoke(messages)
        self._track_usage(response)
        
        content = response.content
        
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
            api_key=settings.OPENAI_API_KEY,
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
            api_key=settings.DEEPSEEK_API_KEY,
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
            api_key=settings.OPENAI_API_KEY,
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
            google_api_key=settings.GOOGLE_API_KEY
        )

    def get_dimensions(self) -> int:
        return self.DIMENSIONS

    async def aembed_query(self, text: str) -> List[float]:
        return await self.embeddings.aembed_query(text)

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        return await self.embeddings.aembed_documents(texts)

class LocalEmbeddingProvider(EmbeddingProvider):
    DIMENSIONS = 384

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.embeddings = HuggingFaceEmbeddings(model_name=model_name)

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
            openai_api_version=settings.AZURE_OPENAI_API_VERSION,
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
        )

    def get_dimensions(self) -> int:
        return self.DIMENSIONS

    async def aembed_query(self, text: str) -> List[float]:
        return await self.embeddings.aembed_query(text)

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        return await self.embeddings.aembed_documents(texts)


from src.llm.factory import register_provider, register_embedder
register_provider("openai", OpenAILLMProvider)
register_embedder("openai", OpenAIEmbeddingProvider)
register_provider("gemini", GeminiLLMProvider)
register_provider("deepseek", DeepSeekLLMProvider)
register_embedder("azure", AzureEmbeddingProvider)
