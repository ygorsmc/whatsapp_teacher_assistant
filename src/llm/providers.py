from src.llm.interfaces import LLMProvider, EmbeddingProvider
from langchain_openai import ChatOpenAI, OpenAIEmbeddings, AzureOpenAIEmbeddings
from langchain_google_genai import ChatGoogleGenerativeAI, GoogleGenerativeAIEmbeddings
from langchain_core.messages import SystemMessage, HumanMessage, BaseMessage
from src.shared.config import settings
from typing import List, Optional, Any
from src.shared.telemetry import track_event

class OpenAILLMProvider(LLMProvider):
    def __init__(self, model_name: str = "gpt-4o-mini"):
        # Placeholder implementation - can be swapped to Gemini/DeepSeek
        self.llm = ChatOpenAI(
            model=model_name, 
            temperature=0.3,
            api_key=settings.OPENAI_API_KEY,
            max_retries=3
        )

    async def agenerate(self, prompt: str, system_message: Optional[str] = None, history: Optional[List[BaseMessage]] = None) -> str:
        messages = []
        if system_message:
            messages.append(SystemMessage(content=system_message))
        if history:
            messages.extend(history)
            
        messages.append(HumanMessage(content=prompt))
        
        # In a real impl, we'd wrap this to extract usage_metadata
        response = await self.llm.ainvoke(messages)
        
        track_event("llm_generation", {
            "model": self.llm.model_name,
            "prompt_tokens": response.response_metadata.get("token_usage", {}).get("prompt_tokens", 0),
            "completion_tokens": response.response_metadata.get("token_usage", {}).get("completion_tokens", 0),
        })
        
        return response.content

class DeepSeekLLMProvider(LLMProvider):
    def __init__(self, model_name: str = "deepseek-v4-flash"):
        if not settings.DEEPSEEK_API_KEY:
            raise ValueError("DEEPSEEK_API_KEY is not set in environment.")
            
        self.llm = ChatOpenAI(
            model=model_name, 
            temperature=0.5,
            api_key=settings.DEEPSEEK_API_KEY,
            base_url="https://api.deepseek.com",
            max_retries=3
        )

    async def agenerate(self, prompt: str, system_message: Optional[str] = None, history: Optional[List[BaseMessage]] = None) -> str:
        messages = []
        if system_message:
            messages.append(SystemMessage(content=system_message))
        if history:
            messages.extend(history)
            
        messages.append(HumanMessage(content=prompt))
        
        response = await self.llm.ainvoke(messages)
        
        track_event("llm_generation", {
            "model": self.llm.model_name,
            "prompt_tokens": response.response_metadata.get("token_usage", {}).get("prompt_tokens", 0),
            "completion_tokens": response.response_metadata.get("token_usage", {}).get("completion_tokens", 0),
        })
        
        return response.content


class OpenAIEmbeddingProvider(EmbeddingProvider):
    def __init__(self, model_name: str = "text-embedding-3-small"):
        self.embeddings = OpenAIEmbeddings(
            model=model_name,
            api_key=settings.OPENAI_API_KEY,
            max_retries=3
        )

    async def aembed_query(self, text: str) -> List[float]:
        return await self.embeddings.aembed_query(text)

    async def aembed_documents(self, texts: List[str]) -> List[List[float]]:
        # Batching is mandatory as per MLOps patterns
        return await self.embeddings.aembed_documents(texts)


class GeminiLLMProvider(LLMProvider):
    def __init__(self, model_name: str = "gemini-3.1-flash-lite"):
        if not settings.GOOGLE_API_KEY:
            raise ValueError("GOOGLE_API_KEY is not set in environment.")
        
        self.llm = ChatGoogleGenerativeAI(
            model=model_name,
            temperature=0.3,
            google_api_key=settings.GOOGLE_API_KEY,
            max_retries=3
        )

    async def agenerate(self, prompt: str, system_message: Optional[str] = None, history: Optional[List[BaseMessage]] = None) -> str:
        messages = []
        if system_message:
            messages.append(SystemMessage(content=system_message))
        if history:
            messages.extend(history)
            
        messages.append(HumanMessage(content=prompt))
        
        response = await self.llm.ainvoke(messages)
        
        # Gemini usage metadata
        usage = response.response_metadata.get("token_usage", {}) if response.response_metadata else {}
        if isinstance(usage, dict):
            prompt_tokens = usage.get("prompt_token_count", 0)
            completion_tokens = usage.get("candidates_token_count", 0)
        else:
            prompt_tokens = getattr(usage, "prompt_token_count", 0)
            completion_tokens = getattr(usage, "candidates_token_count", 0)
        
        track_event("llm_generation", {
            "model": self.llm.model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
        })
        
        content = response.content
        
        # O Gemini as vezes retorna uma lista de dicionários ao invés de string
        if isinstance(content, list):
            text_parts = [part.get("text", "") for part in content if isinstance(part, dict) and "text" in part]
            # Fallback for plain strings inside list just in case
            text_parts.extend([str(part) for part in content if isinstance(part, str)])
            content = " ".join(text_parts)
            
        if not content or not content.strip():
            content = "Desculpe, a inteligência artificial bloqueou a resposta ou retornou vazio."
            
        return content

class GeminiEmbeddingProvider(EmbeddingProvider):
    def __init__(self, model_name: str = "models/text-embedding-004"):
        if not settings.GOOGLE_API_KEY:
            raise ValueError("GOOGLE_API_KEY is not set in environment.")
            
        self.embeddings = GoogleGenerativeAIEmbeddings(
            model=model_name,
            google_api_key=settings.GOOGLE_API_KEY
        )

    async def aembed_query(self, text: str) -> List[float]:
        return await self.embeddings.aembed_query(text)

class LocalEmbeddingProvider(EmbeddingProvider):
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.embeddings = HuggingFaceEmbeddings(model_name=model_name)

    async def aembed_query(self, text: str) -> List[float]:
        return await self.embeddings.aembed_query(text)

class AzureEmbeddingProvider(EmbeddingProvider):
    def __init__(self, deployment_name: str = "text-embedding-3-small"):
        if not settings.AZURE_OPENAI_API_KEY or not settings.AZURE_OPENAI_ENDPOINT:
            raise ValueError("AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT must be set.")
            
        self.embeddings = AzureOpenAIEmbeddings(
            azure_deployment=deployment_name,
            openai_api_version=settings.AZURE_OPENAI_API_VERSION,
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
        )

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
