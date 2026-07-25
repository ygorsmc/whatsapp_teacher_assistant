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
        
        # Mapeia as variáveis sensíveis que devem vir do Key Vault.
        # Toda secret aqui deve existir no KV (Bicep) para que o app NÃO dependa
        # de env var em claro no Container App (vetor do vazamento F01/F02).
        keys_to_fetch = [
            "META-APP-SECRET", "META-VERIFY-TOKEN", "WHATSAPP-TOKEN",
            "DOCUMENT-INTELLIGENCE-KEY", "OPENAI-API-KEY", "GOOGLE-API-KEY", "DEEPSEEK-API-KEY",
            "AZURE-OPENAI-API-KEY", "AZURE-OPENAI-ENDPOINT", "SERVICEBUS-CONNECTION-STRING", "DATABASE-URL",
            "QDRANT-URL", "QDRANT-API-KEY", "REDIS-URL",
            "TELEGRAM-BOT-TOKEN", "TELEGRAM-WEBHOOK-SECRET",
            # Vertical de idiomas (fala) + Partner API (B2B): sem estarem aqui, esses
            # segredos viriam como env em claro no Container App ao ligar as flags de
            # TTS/STT/pronúncia/Partner (exatamente o vetor F01/F02).
            "AZURE-SPEECH-KEY", "ELEVENLABS-API-KEY",
            "GOOGLE-TTS-API-KEY", "GOOGLE-STT-API-KEY", "PARTNER-API-KEY",
            # Reranker managed (F4) — RERANKER_PROVIDER=cohere|voyage em produção.
            # Os dois ficam disponíveis para o eval decidir entre providers sem
            # exigir outra mudança de infra (regra da casa: nunca "no feeling").
            "COHERE-API-KEY", "VOYAGE-API-KEY",
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
