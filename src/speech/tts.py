"""
Adapters de Text-to-Speech (ITextToSpeech).

Generaliza o TTS atual (Azure REST, antes em `shared/tts.py`) para a porta
`ITextToSpeech`, mantendo comportamento **idêntico** com `TTS_PROVIDER=azure`
(mesma SSML, mesma voz, mesma limpeza de texto). Adapters alternativos
(`elevenlabs`, `google`) existem para NÃO depender da Azure — trocar = env var.

Todos via httpx + Tenacity (sem SDK novo). `none`/erro → o chamador segue só com
texto (o áudio é SEMPRE camada extra). A limpeza (`prepare_tts_text`) é pura e
coberta por teste unitário.
"""

from __future__ import annotations

import base64
import re

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from src.shared.config import settings
from src.speech.interfaces import ITextToSpeech

logger = structlog.get_logger(__name__)

# Limite duro do texto sintetizado (~90s de fala).
MAX_TTS_CHARS = 900

_MARKDOWN_NOISE = re.compile(r"[*_`~]|\[|\]")
_EMOJI_NOISE = re.compile(
    "["
    "\U0001F000-\U0001FAFF"
    "\U00002600-\U000027BF"
    "\U0001F1E6-\U0001F1FF"
    "⬀-⯿←-⇿"
    "]+"
)


def prepare_tts_text(text: str, max_chars: int = MAX_TTS_CHARS) -> str:
    """Limpa markdown/emojis (que viram ruído falado) e corta no limite em uma
    fronteira de frase. Puro — coberto por teste unitário."""
    clean = _MARKDOWN_NOISE.sub("", text)
    clean = _EMOJI_NOISE.sub("", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) <= max_chars:
        return clean
    cut = clean[:max_chars]
    last_sentence = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    if last_sentence > max_chars // 2:
        cut = cut[: last_sentence + 1]
    return cut.strip()


# Voz neural Azure padrão por idioma (ISO 639-1) — usada pelo "áudio do dia" (F1)
# para falar na língua-alvo. Providers não-Azure interpretam `voice` à sua maneira.
_DEFAULT_VOICE_BY_LANG = {
    "en": "en-US-JennyNeural", "es": "es-ES-ElviraNeural", "pt": "pt-BR-FranciscaNeural",
    "fr": "fr-FR-DeniseNeural", "de": "de-DE-KatjaNeural", "it": "it-IT-ElsaNeural",
    "ja": "ja-JP-NanamiNeural", "zh": "zh-CN-XiaoxiaoNeural",
}


def default_voice_for(language: str) -> str:
    """Voz neural padrão para o idioma (ISO 639-1 ou locale). Fallback = inglês. Puro."""
    base = language.split("-")[0].lower()
    return _DEFAULT_VOICE_BY_LANG.get(base, "en-US-JennyNeural")


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&apos;")
    )


def _timeout() -> float:
    return settings.SPEECH_TIMEOUT_SECONDS


class AzureTTS(ITextToSpeech):
    """Azure Speech via REST — mesmo recurso/chave do STT e da pronúncia. Mantém
    a SSML exata do TTS legado (xml:lang + prosody +5%)."""

    name = "azure"

    def __init__(self) -> None:
        if not (settings.AZURE_SPEECH_KEY and settings.AZURE_SPEECH_REGION):
            raise ValueError("AZURE_SPEECH_KEY/AZURE_SPEECH_REGION não configurados.")

    @retry(wait=wait_exponential(multiplier=1, min=1, max=10), stop=stop_after_attempt(3), reraise=True)
    async def _post(self, ssml: str) -> bytes:
        url = f"https://{settings.AZURE_SPEECH_REGION}.tts.speech.microsoft.com/cognitiveservices/v1"
        headers = {
            "Ocp-Apim-Subscription-Key": settings.AZURE_SPEECH_KEY or "",
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
            "User-Agent": "whatsapp-teacher-assistant",
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=headers, content=ssml.encode("utf-8"), timeout=30.0)
            resp.raise_for_status()
            return resp.content

    async def synthesize(self, text: str, voice: str, language: str) -> bytes:
        clean = prepare_tts_text(text)
        if not clean:
            raise ValueError("texto vazio após limpeza")
        ssml = (
            f"<speak version='1.0' xml:lang='{language}'>"
            f"<voice name='{voice}'>"
            f"<prosody rate='+5%'>{_xml_escape(clean)}</prosody>"
            f"</voice></speak>"
        )
        audio = await self._post(ssml)
        logger.info("tts_synthesized", provider=self.name, chars=len(clean), bytes=len(audio))
        return audio


class ElevenLabsTTS(ITextToSpeech):
    """ElevenLabs — alternativa premium para sair da Azure. `voice` = voice_id."""

    name = "elevenlabs"

    def __init__(self) -> None:
        if not settings.ELEVENLABS_API_KEY:
            raise ValueError("ELEVENLABS_API_KEY não configurada.")

    @retry(wait=wait_exponential(multiplier=1, min=1, max=10), stop=stop_after_attempt(3), reraise=True)
    async def synthesize(self, text: str, voice: str, language: str) -> bytes:
        clean = prepare_tts_text(text)
        if not clean:
            raise ValueError("texto vazio após limpeza")
        voice_id = voice or settings.ELEVENLABS_VOICE_ID
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                headers={"xi-api-key": settings.ELEVENLABS_API_KEY or "", "Accept": "audio/mpeg"},
                json={"text": clean, "model_id": "eleven_multilingual_v2"},
            )
            resp.raise_for_status()
            audio = resp.content
        logger.info("tts_synthesized", provider=self.name, chars=len(clean), bytes=len(audio))
        return audio


class GoogleTTS(ITextToSpeech):
    """Google Cloud Text-to-Speech (REST por API key). `voice` = nome da voz Google."""

    name = "google"

    def __init__(self) -> None:
        if not settings.GOOGLE_TTS_API_KEY:
            raise ValueError("GOOGLE_TTS_API_KEY não configurada.")

    @retry(wait=wait_exponential(multiplier=1, min=1, max=10), stop=stop_after_attempt(3), reraise=True)
    async def synthesize(self, text: str, voice: str, language: str) -> bytes:
        clean = prepare_tts_text(text)
        if not clean:
            raise ValueError("texto vazio após limpeza")
        async with httpx.AsyncClient(timeout=_timeout()) as client:
            resp = await client.post(
                "https://texttospeech.googleapis.com/v1/text:synthesize",
                params={"key": settings.GOOGLE_TTS_API_KEY or ""},
                json={
                    "input": {"text": clean},
                    "voice": {"languageCode": language, "name": voice} if voice else {"languageCode": language},
                    "audioConfig": {"audioEncoding": "MP3"},
                },
            )
            resp.raise_for_status()
            data = resp.json()
        audio = base64.b64decode(data["audioContent"])
        logger.info("tts_synthesized", provider=self.name, chars=len(clean), bytes=len(audio))
        return audio


_TTS_PROVIDERS: dict[str, type[ITextToSpeech]] = {
    "azure": AzureTTS,
    "elevenlabs": ElevenLabsTTS,
    "google": GoogleTTS,
}
