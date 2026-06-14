"""
Adapter de avaliação de pronúncia (IPronunciationAssessor).

Hoje só a Azure expõe Pronunciation Assessment (matriz acústica: accuracy,
fluency, completeness + score por palavra/fonema), no mesmo recurso REST do STT.
`PRONUNCIATION_PROVIDER=none` ⇒ o factory devolve None e o chamador degrada para
só transcrição. O áudio é descartado após a análise (LGPD) — só os scores ficam.
"""

from __future__ import annotations

import base64
import json

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from src.shared.config import settings
from src.speech.interfaces import IPronunciationAssessor, PronunciationScore, WordScore
from src.speech.stt import to_locale

logger = structlog.get_logger(__name__)


class AzurePronunciation(IPronunciationAssessor):
    """Azure Pronunciation Assessment via header `Pronunciation-Assessment`."""

    name = "azure"

    def __init__(self) -> None:
        if not (settings.AZURE_SPEECH_KEY and settings.AZURE_SPEECH_REGION):
            raise ValueError("AZURE_SPEECH_KEY/AZURE_SPEECH_REGION não configurados.")

    @retry(wait=wait_exponential(multiplier=1, min=1, max=10), stop=stop_after_attempt(3), reraise=True)
    async def assess(self, audio: bytes, reference: str, language: str) -> PronunciationScore:
        assessment_cfg = base64.b64encode(
            json.dumps({
                "ReferenceText": reference,
                "GradingSystem": "HundredMark",
                "Granularity": "Phoneme",
                "Dimension": "Comprehensive",
            }).encode("utf-8")
        ).decode("ascii")
        url = (
            f"https://{settings.AZURE_SPEECH_REGION}.stt.speech.microsoft.com"
            "/speech/recognition/conversation/cognitiveservices/v1"
        )
        headers = {
            "Ocp-Apim-Subscription-Key": settings.AZURE_SPEECH_KEY or "",
            "Content-Type": "audio/ogg; codecs=opus",
            "Accept": "application/json",
            "Pronunciation-Assessment": assessment_cfg,
        }
        async with httpx.AsyncClient(timeout=settings.SPEECH_TIMEOUT_SECONDS) as client:
            resp = await client.post(url, headers=headers, params={"language": to_locale(language)}, content=audio)
            resp.raise_for_status()
            data = resp.json()
        return _parse_azure(data)


def _parse_azure(data: dict) -> PronunciationScore:
    """Extrai a matriz da resposta Azure. Puro — coberto por teste unitário."""
    nbest = (data.get("NBest") or [{}])[0]
    pa = nbest.get("PronunciationAssessment", {})
    words = [
        WordScore(
            word=w.get("Word", ""),
            score=float(w.get("PronunciationAssessment", {}).get("AccuracyScore", 0.0)),
            phonemes=[p.get("Phoneme", "") for p in (w.get("Phonemes") or [])],
        )
        for w in (nbest.get("Words") or [])
    ]
    return PronunciationScore(
        accuracy=float(pa.get("AccuracyScore", 0.0)),
        fluency=float(pa.get("FluencyScore", 0.0)),
        completeness=float(pa.get("CompletenessScore", 0.0)),
        words=words,
    )


_PRONUNCIATION_PROVIDERS: dict[str, type[IPronunciationAssessor]] = {
    "azure": AzurePronunciation,
}
