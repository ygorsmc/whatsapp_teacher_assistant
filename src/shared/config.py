from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
import structlog
from typing import Optional

logger = structlog.get_logger(__name__)

class Settings(BaseSettings):
    # App
    ENVIRONMENT: str = Field(default="development")
    LOG_LEVEL: str = Field(default="INFO")
    
    # Meta / WhatsApp Webhook
    META_APP_SECRET: str = Field(default="dev_secret_override_in_prod")
    META_VERIFY_TOKEN: str = Field(default="whatsapp_teacher_token")
    WHATSAPP_TOKEN: str = Field(default="dev_token")
    PHONE_NUMBER_ID: str = Field(default="YOUR_PHONE_ID")
    WHATSAPP_API_URL: str = Field(default="https://graph.facebook.com/v21.0")
    
    # Defaults and Limits
    DEFAULT_LANGUAGE: str = Field(default="pt_BR")
    META_TEMPLATE_REVIEW: str = Field(default="convite_revisao_quiz")
    TIMEZONE_OFFSET_HOURS: int = Field(default=-3)
    MAX_WHATSAPP_MESSAGE_LENGTH: int = Field(default=4000)
    
    # Messaging Channel
    CHANNEL_BACKEND: str = Field(default="whatsapp") # "whatsapp" ou "telegram"
    TELEGRAM_BOT_TOKEN: str = Field(default="YOUR_TELEGRAM_TOKEN")
    TELEGRAM_WEBHOOK_SECRET: str = Field(default="YOUR_TELEGRAM_SECRET")
    SCHEDULER_MODE: str = Field(default="template") # "template" (WhatsApp) ou "buttons" (Telegram)
    
    # Redis
    REDIS_URL: str = Field(default="redis://localhost:6379/0")
    DEBOUNCE_TTL_SECONDS: int = Field(default=2)
    MAX_BUFFER_CHARS: int = Field(default=4096)
    
    # Mensageria / Fila
    QUEUE_BACKEND: str = Field(default="redis")
    SERVICEBUS_FQDN: str = Field(default="")
    SERVICEBUS_CONNECTION_STRING: Optional[str] = Field(default=None) # Only for local dev with Azurite/Emulator
    
    # Qdrant
    QDRANT_URL: str = Field(default="http://localhost:6333")
    QDRANT_API_KEY: Optional[str] = Field(default=None)
    
    # Azure Document Intelligence / Parsing
    PARSER_BACKEND: str = Field(default="azure")
    DOCUMENT_INTELLIGENCE_ENDPOINT: str = Field(default="")
    DOCUMENT_INTELLIGENCE_KEY: Optional[str] = Field(default=None) # Used only if Managed Identity isn't available
    GOTENBERG_URL: str = Field(default="http://localhost:3000")
    
    # LLMs e IA
    DEFAULT_LLM_PROVIDER: str = Field(default="deepseek")
    DEFAULT_EMBEDDER_PROVIDER: str = Field(default="azure")
    EMBEDDING_DIMENSIONS: int = Field(default=1536)  # 1536=OpenAI/Azure, 768=Gemini, 384=MiniLM
    LLM_TEMPERATURE: float = Field(default=0.3)
    LLM_MAX_RETRIES: int = Field(default=3)
    DEEPSEEK_BASE_URL: str = Field(default="https://api.deepseek.com")
    
    # RAG (MLOps)
    RAG_CHUNK_SIZE: int = Field(default=800)
    RAG_CHUNK_OVERLAP: int = Field(default=150)
    RAG_SCORE_THRESHOLD: float = Field(default=0.3)
    RAG_TOP_K: int = Field(default=5)
    OPENAI_API_KEY: Optional[str] = Field(default=None)
    GOOGLE_API_KEY: Optional[str] = Field(default=None)
    DEEPSEEK_API_KEY: Optional[str] = Field(default=None)
    
    # Azure OpenAI
    AZURE_OPENAI_API_KEY: Optional[str] = Field(default=None)
    AZURE_OPENAI_ENDPOINT: Optional[str] = Field(default=None)
    AZURE_OPENAI_API_VERSION: str = Field(default="2024-02-01")
    
    # Google Drive & Sheets
    GOOGLE_SERVICE_ACCOUNT_FILE: str = Field(default="service_account.json")
    GOOGLE_DRIVE_FOLDER_ID: str = Field(default="")
    GOOGLE_SHEET_ID: str = Field(default="")
    
    # Database
    DATABASE_URL: str = Field(default="sqlite+aiosqlite:///./local_dev.db")

    # Azure Key Vault (Security)
    AZURE_KEY_VAULT_URL: Optional[str] = Field(default=None)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()

# Se AZURE_KEY_VAULT_URL estiver definido (produção via Bicep), busca segredos usando Managed Identity.
# Se for GCP ou AWS, as próprias nuvens já injetam os secrets como Variáveis de Ambiente, 
# tornando o fallback natural e seguro.
if settings.AZURE_KEY_VAULT_URL:
    try:
        from src.shared.azure_kv import load_azure_secrets
        load_azure_secrets(settings)
    except Exception as e:
        logger.error(f"Failed to load azure_kv module: {e}")
