"""
Adapters de Speech-to-Text (ISpeechToText).

Providers (httpx + Tenacity, sem SDK novo):
  - `azure`         → Azure Speech REST (mesmo recurso do TTS/pronúncia)
  - `whisper_openai`→ OpenAI `/audio/transcriptions` (usa OPENAI_API_KEY)
  - `whisper_local` → endpoint compatível com OpenAI (faster-whisper auto-hospedado)
  - `google`        → Google Cloud Speech-to-Text (REST por API key)

`none` não tem adapter: o factory devolve None e o chamador pede texto. O áudio
é descartado após a transcrição (LGPD) — isso é responsabilidade do worker (F3).
"""

from __future__ import annotations

import base64

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from src.shared.config import settings
from src.speech.interfaces import ISpeechToText, Transcription

logger = structlog.get_logger(__name__)

# ISO 639-1 → locale BCP-47 (Azure/Google exigem locale). Pass-through se já vier
# com região (ex.: "en-US"). Puro.
_LOCALE_MAP = {
    "en": "en-US", "es": "es-ES", "pt": "pt-BR", "fr": "fr-FR",
    "de": "de-DE", "it": "it-IT", "ja": "ja-JP", "zh": "zh-CN",
}


def to_locale(language: str) -> str:
    if "-" in language:
        return language
    return _LOCALE_MAP.get(language.lower(), f"{language.lower()}-{language.upper()}")


def _timeout() -> float:
    return settings.SPEECH_TIMEOUT_SECONDS


def _retry():
    return retry(wait=wait_exponential(multiplier=1, min=1, max=10), stop=stop_after_attempt(3), reraise=True)


class AzureSTT(ISpeechToText):
    """Azure Speech REST (short audio). Espera OGG/Opus (voice note do WhatsApp)."""

    name = "azure"

    def __init__(self) -> None:
        if not (settings.AZURE_SPEECH_KEY and settings.AZURE_SPEECH_REGION):
            raise ValueError("AZURE_SPEECH_KEY/AZURE_SPEECH_REGION não configurados.")

    @_retry()
    async def transcribe(self, audio: bytes, language: str) -> Transcription:
        url = (
            f"https://{settings.AZURE_SPEECH_REGION}.stt.speech.microsoft.com"
            "/speech/recognition/conversation/cognitiveservices/v1"
        )
        headers = {
            "Ocp-Apim-Subscription-Key": settings.AZURE_SPEECH_KEY or "",
            "Content-Type": "audio/ogg; codecs=opus",
            "Accept": "application/json",
        }
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.post(url, headers=headers, params={"language": to_locale(language)}, content=audio)
            resp.raise_for_status()
            data = resp.json()
        text = data.get("DisplayText", "") or data.get("NBest", [{}])[0].get("Display", "")
        conf = (data.get("NBest") or [{}])[0].get("Confidence")
        logger.info("stt_transcribed", provider=self.name, chars=len(text))
        return Transcription(text=text, language=language, confidence=conf)


class _WhisperCompatSTT(ISpeechToText):
    """Base para endpoints compatíveis com a API `/audio/transcriptions` da OpenAI."""

    base_url: str = ""
    api_key: str | None = None
    model: str = "whisper-1"

    @_retry()
    async def transcribe(self, audio: bytes, language: str) -> Transcription:
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        files = {"file": ("audio.ogg", audio, "audio/ogg")}
        data = {"model": self.model, "language": language}
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.post(
                f"{self.base_url.rstrip('/')}/audio/transcriptions",
                headers=headers, files=files, data=data,
            )
            resp.raise_for_status()
            payload = resp.json()
        text = payload.get("text", "")
        logger.info("stt_transcribed", provider=self.name, chars=len(text))
        return Transcription(text=text, language=language, confidence=None)


class WhisperOpenAISTT(_WhisperCompatSTT):
    """OpenAI Whisper (`whisper-1`). Reusa OPENAI_API_KEY (já existente)."""

    name = "whisper_openai"

    def __init__(self) -> None:
        if not settings.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY não configurada.")
        self.base_url = "https://api.openai.com/v1"
        self.api_key = settings.OPENAI_API_KEY
        self.model = settings.WHISPER_OPENAI_MODEL


class WhisperLocalSTT(_WhisperCompatSTT):
    """Endpoint compatível com OpenAI auto-hospedado (faster-whisper) — fora da Azure."""

    name = "whisper_local"

    def __init__(self) -> None:
        if not settings.WHISPER_ENDPOINT:
            raise ValueError("WHISPER_ENDPOINT não configurado.")
        self.base_url = settings.WHISPER_ENDPOINT
        self.api_key = settings.WHISPER_API_KEY
        self.model = settings.WHISPER_OPENAI_MODEL


class GoogleSTT(ISpeechToText):
    """Google Cloud Speech-to-Text (REST por API key)."""

    name = "google"

    def __init__(self) -> None:
        if not settings.GOOGLE_STT_API_KEY:
            raise ValueError("GOOGLE_STT_API_KEY não configurada.")

    @_retry()
    async def transcribe(self, audio: bytes, language: str) -> Transcription:
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.post(
                "https://speech.googleapis.com/v1/speech:recognize",
                params={"key": settings.GOOGLE_STT_API_KEY or ""},
                json={
                    "config": {"languageCode": to_locale(language), "encoding": "OGG_OPUS"},
                    "audio": {"content": base64.b64encode(audio).decode("ascii")},
                },
            )
            resp.raise_for_status()
            data = resp.json()
        results = data.get("results") or [{}]
        alt = (results[0].get("alternatives") or [{}])[0]
        text = alt.get("transcript", "")
        logger.info("stt_transcribed", provider=self.name, chars=len(text))
        return Transcription(text=text, language=language, confidence=alt.get("confidence"))


_STT_PROVIDERS: dict[str, type[ISpeechToText]] = {
    "azure": AzureSTT,
    "whisper_openai": WhisperOpenAISTT,
    "whisper_local": WhisperLocalSTT,
    "google": GoogleSTT,
}
