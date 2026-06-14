"""
Portas e DTOs da camada de fala (F0 — fundação).

Os DTOs são **puros** (dataclasses, sem I/O) — testáveis sem rede. As três portas
seguem o padrão do projeto (ABC + atributo `name` + métodos async com Tenacity nas
implementações). Mantê-las pequenas: a lógica de provider vive nos adapters
(`stt.py`/`pronunciation.py`/`tts.py`); a seleção, no `factory.py`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List


# ──────────────────────────────────────────────────────────────────────────────
# DTOs puros (sem I/O)
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class Transcription:
    """Resultado de STT. `confidence` é best-effort (None se o provider não expõe)."""
    text: str
    language: str
    confidence: float | None = None


@dataclass
class WordScore:
    """Score de pronúncia de uma palavra, com fonemas opcionais (Azure expõe;
    outros providers deixam a lista vazia)."""
    word: str
    score: float
    phonemes: List[str] = field(default_factory=list)


@dataclass
class PronunciationScore:
    """Matriz acústica devolvida por `IPronunciationAssessor`. Só números — o áudio
    NUNCA é persistido (LGPD). `words` detalha por palavra para o feedback socrático."""
    accuracy: float
    fluency: float
    completeness: float
    words: List[WordScore] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# Portas
# ──────────────────────────────────────────────────────────────────────────────
class ISpeechToText(ABC):
    name: str = "abstract"

    @abstractmethod
    async def transcribe(self, audio: bytes, language: str) -> Transcription:
        """Transcreve `audio` no `language` (ISO 639-1). Levanta em falha — o
        chamador aplica o fallback (pedir texto)."""
        ...


class IPronunciationAssessor(ABC):
    name: str = "abstract"

    @abstractmethod
    async def assess(self, audio: bytes, reference: str, language: str) -> PronunciationScore:
        """Avalia a pronúncia de `audio` contra o texto `reference`. Levanta em
        falha — o chamador degrada para correção só textual."""
        ...


class ITextToSpeech(ABC):
    name: str = "abstract"

    @abstractmethod
    async def synthesize(self, text: str, voice: str, language: str) -> bytes:
        """Sintetiza `text` em áudio (bytes). Levanta em falha — o chamador segue
        só com o texto (o áudio é SEMPRE camada extra, nunca substitui)."""
        ...
