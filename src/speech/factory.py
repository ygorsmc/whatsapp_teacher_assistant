"""
Factory da camada de fala — seleção de provider por env var, com cache por tipo
(singleton), igual `channel/factory.py` e `llm/reranker.get_reranker`.

`get_stt()` / `get_pronunciation()` / `get_tts()` devolvem **None** quando o
provider é `none`, desconhecido, ou não está configurado (chave ausente). None é
o contrato de degradação: STT none → pede texto; pronúncia none → só transcreve;
TTS none → texto. Nenhuma falha de seleção derruba o fluxo do aluno.
"""

from __future__ import annotations

import structlog

from src.shared.config import settings
from src.speech.interfaces import (
    IPronunciationAssessor,
    ISpeechToText,
    ITextToSpeech,
)
from src.speech.pronunciation import _PRONUNCIATION_PROVIDERS
from src.speech.stt import _STT_PROVIDERS
from src.speech.tts import _TTS_PROVIDERS

logger = structlog.get_logger(__name__)

_stt_cache: dict[str, ISpeechToText] = {}
_pron_cache: dict[str, IPronunciationAssessor] = {}
_tts_cache: dict[str, ITextToSpeech] = {}


def _build(kind: str, registry, cache, provider_name: str | None, default: str):
    name = (provider_name or default or "none").lower()
    if name in ("none", ""):
        return None
    if name in cache:
        return cache[name]
    cls = registry.get(name)
    if cls is None:
        logger.warning("unknown_speech_provider", kind=kind, provider=name)
        return None
    try:
        instance = cls()
    except Exception as e:  # chave ausente / config inválida → degrada com graça
        logger.warning("speech_provider_unavailable", kind=kind, provider=name, error=str(e)[:200])
        return None
    cache[name] = instance
    return instance


def get_stt(name: str | None = None) -> ISpeechToText | None:
    """STT selecionado por `STT_PROVIDER`. None = sem STT (pede texto)."""
    return _build("stt", _STT_PROVIDERS, _stt_cache, name, settings.STT_PROVIDER)


def get_pronunciation(name: str | None = None) -> IPronunciationAssessor | None:
    """Avaliador de pronúncia por `PRONUNCIATION_PROVIDER`. None = só transcreve."""
    return _build("pronunciation", _PRONUNCIATION_PROVIDERS, _pron_cache, name, settings.PRONUNCIATION_PROVIDER)


def get_tts(name: str | None = None) -> ITextToSpeech | None:
    """TTS por `TTS_PROVIDER`. None = sem áudio de saída (segue só com texto)."""
    return _build("tts", _TTS_PROVIDERS, _tts_cache, name, settings.TTS_PROVIDER)
