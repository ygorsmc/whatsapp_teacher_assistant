"""
TTS — Pílulas de Áudio (H1.3) · façade retrocompatível.

A implementação migrou para a porta `ITextToSpeech` em `src/speech/` (vertical de
idiomas, F0). Este módulo permanece como a API pública estável da feature H1.3: o
chamador (`reading_journey.py`) e os testes continuam importando daqui, com
**comportamento idêntico** quando `TTS_PROVIDER=azure` (voz `TTS_VOICE`, pt-BR).

`synthesize()` é best-effort: None se TTS desabilitado/sem provider/erro — o
chamador segue só com o texto. O áudio é SEMPRE camada extra.
"""

from __future__ import annotations

import structlog

from src.shared.config import settings
from src.speech.factory import get_tts
from src.speech.tts import prepare_tts_text  # noqa: F401 (re-export estável p/ H1.3 + testes)

logger = structlog.get_logger(__name__)


def is_tts_available() -> bool:
    """True se a feature está ligada E há um provider de TTS utilizável."""
    return bool(settings.TTS_ENABLED and get_tts() is not None)


async def synthesize(text: str) -> bytes | None:
    """Texto → MP3 (bytes), na voz/idioma das pílulas H1.3 (pt-BR). None se
    desabilitado/sem provider/erro — o chamador segue normalmente só com o texto."""
    if not settings.TTS_ENABLED:
        return None
    provider = get_tts()
    if provider is None:
        return None
    try:
        return await provider.synthesize(text, settings.TTS_VOICE, "pt-BR")
    except Exception as e:
        logger.warning("tts_failed", provider=getattr(provider, "name", "?"), error=str(e)[:200])
        return None
