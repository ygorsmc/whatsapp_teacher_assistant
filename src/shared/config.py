from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
import os
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
    
    # Redis
    REDIS_URL: str = Field(default="redis://localhost:6379/0")
    DEBOUNCE_TTL_SECONDS: int = Field(default=2)
    MAX_BUFFER_CHARS: int = Field(default=4096)
    
    # Service Bus
    SERVICEBUS_FQDN: str = Field(default="")
    SERVICEBUS_CONNECTION_STRING: Optional[str] = Field(default=None) # Only for local dev with Azurite/Emulator
    
    # Qdrant
    QDRANT_URL: str = Field(default="http://localhost:6333")
    QDRANT_API_KEY: Optional[str] = Field(default=None)
    
    # Azure Document Intelligence
    DOCUMENT_INTELLIGENCE_ENDPOINT: str = Field(default="")
    DOCUMENT_INTELLIGENCE_KEY: Optional[str] = Field(default=None) # Used only if Managed Identity isn't available
    GOTENBERG_URL: str = Field(default="http://localhost:3000")
    
    # OpenAI / Embeddings (Placeholder for Gemini/DeepSeek depending on implementation)
    DEFAULT_LLM_PROVIDER: str = Field(default="deepseek")
    DEFAULT_EMBEDDER_PROVIDER: str = Field(default="azure")
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

# Se AZURE_KEY_VAULT_URL estiver definido (produção), busca segredos usando Managed Identity
if settings.AZURE_KEY_VAULT_URL:
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
        
        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=settings.AZURE_KEY_VAULT_URL, credential=credential)
        
        # Mapeia as variáveis sensíveis que devem vir do Key Vault
        keys_to_fetch = [
            "META-APP-SECRET", "META-VERIFY-TOKEN", "WHATSAPP-TOKEN",
            "DOCUMENT-INTELLIGENCE-KEY", "OPENAI-API-KEY", "GOOGLE-API-KEY", "DEEPSEEK-API-KEY",
            "AZURE-OPENAI-API-KEY", "SERVICEBUS-CONNECTION-STRING", "DATABASE-URL"
        ]
        
        for kv_key in keys_to_fetch:
            try:
                secret = client.get_secret(kv_key)
                # Converte o padrão do Key Vault (hífen) para o padrão do Python (underscore)
                env_key = kv_key.replace("-", "_")
                
                # SQLAlchemy expects a URL format, not an ODBC string
                if env_key == "DATABASE_URL" and "Driver=" in secret.value:
                    import re
                    import urllib.parse
                    val = secret.value
                    server = re.search(r"Server=tcp:([^,;]+)", val).group(1)
                    database = re.search(r"Database=([^;]+)", val).group(1)
                    uid = re.search(r"Uid=([^;]+)", val).group(1)
                    pwd = re.search(r"Pwd=([^;]+)", val).group(1)
                    pwd_encoded = urllib.parse.quote_plus(pwd)
                    sa_url = f"mssql+aioodbc://{uid}:{pwd_encoded}@{server}:1433/{database}?driver=ODBC+Driver+18+for+SQL+Server"
                    setattr(settings, env_key, sa_url)
                else:
                    setattr(settings, env_key, secret.value)
            except Exception as e:
                logger.warning(f"KeyVault secret '{kv_key}' not found or could not be loaded. Fallback to .env")
                
        logger.info("Secrets successfully loaded from Azure Key Vault via Managed Identity")
    except ImportError:
        logger.error("azure-identity or azure-keyvault-secrets not installed. Cannot load from Key Vault.")
    except Exception as e:
        logger.error(f"Failed to connect to Azure Key Vault: {e}")
