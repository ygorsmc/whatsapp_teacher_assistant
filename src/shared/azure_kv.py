import structlog

logger = structlog.get_logger(__name__)

def load_azure_secrets(settings) -> None:
    """
    Tenta carregar os segredos do Azure Key Vault usando a Managed Identity.
    Este módulo é carregado dinamicamente no config.py para não quebrar a aplicação
    caso as dependências da Azure não estejam instaladas (ex: GCP/AWS/Local).
    """
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
        
        credential = DefaultAzureCredential()
        client = SecretClient(vault_url=settings.AZURE_KEY_VAULT_URL, credential=credential)
        
        # Mapeia as variáveis sensíveis que devem vir do Key Vault
        keys_to_fetch = [
            "META-APP-SECRET", "META-VERIFY-TOKEN", "WHATSAPP-TOKEN",
            "DOCUMENT-INTELLIGENCE-KEY", "OPENAI-API-KEY", "GOOGLE-API-KEY", "DEEPSEEK-API-KEY",
            "AZURE-OPENAI-API-KEY", "AZURE-OPENAI-ENDPOINT", "SERVICEBUS-CONNECTION-STRING", "DATABASE-URL",
            "QDRANT-URL", "QDRANT-API-KEY", "REDIS-URL"
        ]
        
        for kv_key in keys_to_fetch:
            try:
                secret = client.get_secret(kv_key)
                # Converte o padrão do Key Vault (hífen) para o padrão do Python (underscore)
                env_key = kv_key.replace("-", "_")
                
                # O banco de dados agora é PostgreSQL, a Connection String no KeyVault/Secret Manager
                # já deve estar no formato nativo do SQLAlchemy: postgresql+asyncpg://...
                setattr(settings, env_key, secret.value)
            except Exception:
                logger.warning(f"KeyVault secret '{kv_key}' not found or could not be loaded. Fallback to .env")
                
        logger.info("Secrets successfully loaded from Azure Key Vault via Managed Identity")
    except ImportError:
        logger.error("azure-identity or azure-keyvault-secrets not installed. Cannot load from Key Vault.")
    except Exception as e:
        logger.error(f"Failed to connect to Azure Key Vault: {e}")
