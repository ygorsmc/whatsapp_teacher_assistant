from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
import os
import structlog
from typing import Optional

logger = structlog.get_logger(__name__)

# Seletor de ambiente: ENV_FILE=.env.prod make ... carrega outro arquivo.
# Default = .env (alternado por `make env-local` / `make env-prod`).
ENV_FILE = os.getenv("ENV_FILE", ".env")

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
    BOOTSTRAP_ADMIN_PHONE: Optional[str] = Field(default=None)
    
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
    BOT: str = Field(default="@pibep_bot")
    
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
    # F1 (RAG Preciso): chunks estruturais de ~500 tokens (2000 chars PT-BR) com
    # overlap de ~75 tokens — piso da faixa 512-1024 recomendada pelo relatório.
    # Só têm efeito na PRÓXIMA ingestão/reindexação (janela única da F8).
    RAG_CHUNK_SIZE: int = Field(default=2000)
    RAG_CHUNK_OVERLAP: int = Field(default=300)
    RAG_SCORE_THRESHOLD: float = Field(default=0.3)
    RAG_TOP_K: int = Field(default=5)

    # ── RAG de Alta Precisão (F0-F8) — TODAS as flags OFF = sistema atual ────
    # F0: métrica de Faithfulness via Ragas (LLM-juiz barato) — só sob demanda.
    RAGAS_EVAL_ENABLED: bool = Field(default=False)
    # F2: Contextual Retrieval (LLM situa o chunk antes de vetorizar).
    CONTEXTUAL_RETRIEVAL_ENABLED: bool = Field(default=False)
    CONTEXTUAL_MAX_DOC_CHARS: int = Field(default=6000)
    # F3: Busca Híbrida (dense + sparse com fusão RRF server-side no Qdrant).
    HYBRID_SEARCH_ENABLED: bool = Field(default=False)
    QDRANT_COLLECTION: str = Field(default="documents")
    # F3a: BGE-M3 via API de inferência hospedada (denso 1024 + esparso em 1 passo).
    BGE_M3_ENDPOINT: str = Field(default="")
    BGE_M3_API_KEY: Optional[str] = Field(default=None)
    BGE_M3_TIMEOUT_SECONDS: float = Field(default=15.0)
    # F4: Reranking cross-encoder ('none' = passthrough, caminho atual).
    RERANKER_PROVIDER: str = Field(default="none")  # none | cohere | voyage | open
    RERANK_CANDIDATES: int = Field(default=50)
    RERANK_TOP_N: int = Field(default=5)
    RERANK_SCORE_THRESHOLD: float = Field(default=0.3)
    RERANK_TIMEOUT_SECONDS: float = Field(default=3.0)
    # Com reranker ativo, o cosseno vira pré-filtro frouxo (o juiz é o rerank).
    RAG_PREFILTER_THRESHOLD: float = Field(default=0.2)
    COHERE_API_KEY: Optional[str] = Field(default=None)
    COHERE_RERANK_MODEL: str = Field(default="rerank-v4.0-fast")
    VOYAGE_API_KEY: Optional[str] = Field(default=None)
    VOYAGE_RERANK_MODEL: str = Field(default="rerank-2")
    OPEN_RERANKER_ENDPOINT: str = Field(default="")
    OPEN_RERANKER_API_KEY: Optional[str] = Field(default=None)
    OPEN_RERANKER_MODEL: str = Field(default="BAAI/bge-reranker-v2-m3")
    # F5: CRAG-lite (avaliador = score do reranker) + Recusa Honesta.
    CRAG_LITE_ENABLED: bool = Field(default=False)
    CRAG_GREEN: float = Field(default=0.5)   # evidência forte: fluxo normal
    CRAG_RED: float = Field(default=0.2)     # abaixo disso (pós-reescrita): abstém
    # F7: bypass do RAG para bases pequenas (corpus inteiro no prompt).
    LONG_CONTEXT_ENABLED: bool = Field(default=False)
    LONG_CONTEXT_MAX_CHARS: int = Field(default=150_000)

    # ── LGPD (Plano P0-P1 — _docs/docs/PLANO_LGPD.md) ────────────────────────
    # P0.1: só o PRIMEIRO NOME do aluno vai a prompts de LLM (minimização).
    LLM_PII_MINIMIZATION: bool = Field(default=True)
    # P1.1: gate de consentimento do responsável p/ crianças (Art. 14) —
    # política por contrato da escola controladora; default OFF.
    GUARDIAN_CONSENT_ENABLED: bool = Field(default=False)
    # P1.3: expurgo programado — ligar após validação jurídica dos prazos.
    DATA_RETENTION_ENABLED: bool = Field(default=False)
    RETENTION_INTERACTION_MONTHS: int = Field(default=12)   # conteúdo de StudentInteractions
    RETENTION_INACTIVE_STUDENT_MONTHS: int = Field(default=12)  # anonimização de inativos
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
    
    # ── Horizontes H1-H3 (plano de diferenciação) ────────────────────────────
    # H1.1 Desafio de turma / H2.3 Simulado cronometrado
    CHALLENGE_WINDOW_HOURS: int = Field(default=6)
    SIMULADO_WINDOW_HOURS: int = Field(default=24)
    # H1.3 Pílulas de áudio (TTS) — desligado por padrão; Azure Speech via REST.
    TTS_ENABLED: bool = Field(default=False)
    TTS_VOICE: str = Field(default="pt-BR-FranciscaNeural")
    AZURE_SPEECH_KEY: Optional[str] = Field(default=None)
    AZURE_SPEECH_REGION: Optional[str] = Field(default=None)
    # H2.2 Correção dissertativa
    ESSAY_DAILY_LIMIT: int = Field(default=2)
    # H3.2 Hiperpersonalização
    SRS_ADAPTIVE_ENABLED: bool = Field(default=False)   # escada fixa D+3/10/30 por padrão
    ADAPTIVE_TOPIC_PRIORITY: bool = Field(default=True)  # prioriza tópicos fracos na rotação
    # H3.3 API B2B de parceiros (None = endpoints desativados)
    PARTNER_API_KEY: Optional[str] = Field(default=None)

    # ── Vertical de Idiomas (F0+) — TUDO OFF por padrão; ligar UMA fase por vez ─
    # Spec: _docs/prompt_execucao_idiomas.md · Doc: _docs/docs/IDIOMAS_OPERATIONS.md
    # Modular/provider-agnóstico: trocar de fornecedor = trocar a env var (sem
    # Azure-lock). `none` é sempre um provider válido (degrada com graça).
    # Mestre da vertical: off ⇒ comportamento atual bit a bit intocado.
    LANGUAGE_MODE_ENABLED: bool = Field(default=False)
    # Áudio de SAÍDA (listening / TTS) — ITextToSpeech. TTS_ENABLED/TTS_VOICE/
    # AZURE_SPEECH_* já declarados no bloco H1.3 (reaproveitados).
    TTS_PROVIDER: str = Field(default="azure")  # azure | elevenlabs | google | none
    ELEVENLABS_API_KEY: Optional[str] = Field(default=None)
    ELEVENLABS_VOICE_ID: str = Field(default="21m00Tcm4TlvDq8ikWAM")  # "Rachel" (default ElevenLabs)
    GOOGLE_TTS_API_KEY: Optional[str] = Field(default=None)
    # Áudio de ENTRADA (speaking / STT) — ISpeechToText.
    SPEECH_INPUT_ENABLED: bool = Field(default=False)
    STT_PROVIDER: str = Field(default="azure")  # azure | whisper_openai | whisper_local | google | none
    WHISPER_OPENAI_MODEL: str = Field(default="whisper-1")  # usa OPENAI_API_KEY (já existente)
    WHISPER_ENDPOINT: str = Field(default="")  # faster-whisper / endpoint compatível (auto-hospedado)
    WHISPER_API_KEY: Optional[str] = Field(default=None)
    GOOGLE_STT_API_KEY: Optional[str] = Field(default=None)
    SPEECH_TIMEOUT_SECONDS: float = Field(default=30.0)
    # Avaliação de pronúncia (matriz acústica) — IPronunciationAssessor.
    PRONUNCIATION_PROVIDER: str = Field(default="none")  # azure | none (none = só transcreve)
    # LGPD: voz é dado sensível → descartar áudio após análise (0 = imediato).
    SPEECH_AUDIO_RETENTION_SECONDS: int = Field(default=0)
    # Submodos da vertical (ligar aos poucos).
    VOCABULARY_SRS_ENABLED: bool = Field(default=False)
    ROLEPLAY_ENABLED: bool = Field(default=False)
    PLACEMENT_TEST_ENABLED: bool = Field(default=False)
    PROFICIENCY_REPORT_ENABLED: bool = Field(default=False)
    L1_SAFETY_NET_ENABLED: bool = Field(default=False)
    EXAM_PREP_ENABLED: bool = Field(default=False)
    # Idiomas padrão (sobrescritos por curso/turma no banco em F5).
    DEFAULT_TARGET_LANGUAGE: str = Field(default="en")   # idioma-alvo (ISO 639-1)
    DEFAULT_NATIVE_LANGUAGE: str = Field(default="pt-BR")  # L1 do aluno (rede de segurança)
    DEFAULT_CEFR_LEVEL: str = Field(default="A2")

    # Database — Postgres em todo lugar (dev e nuvem) para paridade de ambiente.
    # Default = Postgres local do docker-compose; .env e a nuvem injetam via env var.
    DATABASE_URL: str = Field(default="postgresql+asyncpg://whatsapp:whatsapp_dev_2026@localhost:5432/whatsapp_assistant")

    # Azure Key Vault (Security)
    AZURE_KEY_VAULT_URL: Optional[str] = Field(default=None)

    model_config = SettingsConfigDict(env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore")

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
