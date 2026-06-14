"""
Detecção de "curso de idioma" PELO NOME do curso (vertical de idiomas, §1.4).

Convenção sobre configuração (mesmo espírito de `material_type` por nome de pasta):
o professor nomeia a pasta do curso e o bot decide, **pelo nome**, se deve se
comportar como tutor de idioma — sem nova coluna na planilha, sem painel, sem flag
por turma. Helper **puro** (sem I/O), coberto por teste unitário.

Precedência:
  1. Tag explícita `[idioma:<iso>]` na pasta → **autoritativa** (vence stoplist).
  2. Stoplist anti-falso-positivo (ex.: "História da Língua Inglesa", "Literatura
     Inglesa") → exige a tag; sem ela, NÃO é curso de idioma.
  3. Heurística por palavra-chave (inglês/english/espanhol/...) → infere o alvo.
  4. Sem match → None (curso normal, comportamento atual).

O alvo cai para `default_target` quando a tag é ambígua (código desconhecido).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class LanguageSpec:
    """Resultado da detecção. `target_language` em ISO 639-1; `detected_via`
    registra a origem (tag autoritativa vs. heurística) para telemetria/depuração."""
    target_language: str
    detected_via: str  # "tag" | "heuristic"


# ISO 639-1 — subconjunto de idiomas plausíveis de serem ensinados. Valida a tag
# (o professor pode marcar idiomas fora do mapa de heurística, ex.: [idioma:ko]).
_ISO_639_1 = frozenset({
    "en", "es", "pt", "fr", "de", "it", "ja", "zh", "ko", "ru", "ar",
    "nl", "sv", "no", "da", "fi", "pl", "tr", "el", "he", "hi", "id",
})

# Palavra-chave (acento removido) → ISO 639-1. Fallback heurístico.
_KEYWORD_TO_ISO = {
    "ingles": "en", "english": "en",
    "espanhol": "es", "espanol": "es", "spanish": "es",
    "frances": "fr", "french": "fr", "francais": "fr",
    "alemao": "de", "german": "de", "deutsch": "de",
    "italiano": "it", "italian": "it",
    "japones": "ja", "japanese": "ja", "nihongo": "ja",
    "mandarim": "zh", "chines": "zh", "chinese": "zh",
}

# Termos que indicam curso SOBRE a língua (não de aprendizado do idioma) — exigem
# a tag explícita para virar tutor. Comparados sobre o nome sem acento, minúsculo.
_STOPLIST = (
    "historia da lingua",
    "literatura",
    "linguistica",
)

# ISO 639-1 → nome do idioma em pt-BR (o System Prompt é ancorado em pt-BR).
_ISO_TO_NAME_PT = {
    "en": "inglês", "es": "espanhol", "pt": "português", "fr": "francês",
    "de": "alemão", "it": "italiano", "ja": "japonês", "zh": "mandarim",
    "ko": "coreano", "ru": "russo", "ar": "árabe", "nl": "holandês",
    "sv": "sueco", "no": "norueguês", "da": "dinamarquês", "fi": "finlandês",
    "pl": "polonês", "tr": "turco", "el": "grego", "he": "hebraico",
    "hi": "hindi", "id": "indonésio",
}

_TAG_RE = re.compile(r"\[\s*idioma\s*:\s*([a-zA-Z]{2})\s*\]")


def language_name(code: str) -> str:
    """ISO 639-1 (ou locale, ex.: 'pt-BR') → nome em pt-BR. Fallback = o próprio
    código. Puro."""
    base = code.split("-")[0].lower()
    return _ISO_TO_NAME_PT.get(base, code)


def _normalize(text: str) -> str:
    """Minúsculo + sem acentos (NFKD) — para casamento robusto. Puro."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in nfkd if not unicodedata.combining(ch))


# Nível de proficiência da turma (aba "Turmas" → coluna "Nível") → CEFR.
_LEVEL_CANONICAL = {
    "iniciante": "Iniciante",
    "intermediario": "Intermediário",
    "avancado": "Avançado",
    "fluente": "Fluente",
}
_LEVEL_TO_CEFR = {
    "Iniciante": "A2", "Intermediário": "B1", "Avançado": "C1", "Fluente": "C2",
}


@dataclass(frozen=True)
class LanguageDelivery:
    """Como entregar a revisão/quiz de uma turma de idioma, por nível.

    text_language: idioma do TEXTO exibido (Iniciante → L1; demais → alvo).
    audio_language: idioma do ÁUDIO (sempre o alvo).
    cefr_level: nível CEFR derivado do nível da planilha.
    is_beginner: True quando o texto fica em L1 (nível Iniciante ou não informado).
    """
    text_language: str
    audio_language: str
    cefr_level: str
    is_beginner: bool


def normalize_level(raw: str | None) -> str | None:
    """Normaliza o "Nível" da planilha (acento/caixa-insensível) para a forma
    canônica. '-'/vazio/desconhecido → None. Puro."""
    if not raw:
        return None
    key = _normalize(raw).strip()
    return _LEVEL_CANONICAL.get(key)


def level_to_cefr(level: str | None) -> str:
    """Nível canônico → faixa CEFR. None/desconhecido → 'A2' (conservador). Puro."""
    return _LEVEL_TO_CEFR.get(level or "", "A2")


# Escada CEFR (ordem crescente) — usada pelo nivelamento (F5).
CEFR_LADDER = ["A1", "A2", "B1", "B2", "C1", "C2"]


def classify_cefr(results: list[bool]) -> str:
    """Nivelamento (F5): `results` ordenado A1→C2 (acertou a questão daquele nível?).
    O nível é o ÚLTIMO degrau CONSECUTIVO acertado (proficiência demonstrada);
    a 1ª falha encerra a escada. Nenhum acerto inicial → 'A1' (piso). Puro."""
    level = CEFR_LADDER[0]
    for i, ok in enumerate(results):
        if not ok:
            break
        level = CEFR_LADDER[min(i, len(CEFR_LADDER) - 1)]
    return level


def language_delivery(
    is_language: bool,
    proficiency_level: str | None,
    target_language: str,
    native_language: str,
) -> LanguageDelivery | None:
    """Regra de entrega por nível (curso de idioma). None = não é turma de idioma
    ⇒ comportamento atual. Iniciante (ou nível não informado): TEXTO em L1, ÁUDIO
    no alvo. Intermediário/Avançado/Fluente: TEXTO e ÁUDIO no alvo. Puro."""
    if not is_language:
        return None
    level = normalize_level(proficiency_level)
    is_beginner = level is None or level == "Iniciante"
    return LanguageDelivery(
        text_language=native_language if is_beginner else target_language,
        audio_language=target_language,
        cefr_level=level_to_cefr(level),
        is_beginner=is_beginner,
    )


def review_audio_text(audio_script: str | None, summary: str, is_beginner: bool) -> str | None:
    """Texto a SINTETIZAR (TTS) na revisão de idioma — sempre na língua-alvo. Puro.

    `audio_script` é a versão íntegra na língua-alvo. Quando vazio:
    - nível avançado: o `summary` JÁ está na língua-alvo → narra o resumo;
    - Iniciante: o `summary` está em L1 (pt-BR) → **None** (NÃO narrar pt-BR com a
      voz/locale da língua-alvo = áudio na língua errada). O texto segue sempre."""
    if audio_script:
        return audio_script
    return None if is_beginner else summary


def detect_language_course(course_name: str, default_target: str | None = None) -> LanguageSpec | None:
    """Decide se `course_name` é um curso de idioma e qual o idioma-alvo.

    None ⇒ curso normal (comportamento atual). A tag `[idioma:xx]` é autoritativa
    e ignora a stoplist; sem tag, a stoplist bloqueia falsos positivos antes da
    heurística.
    """
    if not course_name:
        return None

    # 1. Tag explícita (autoritativa).
    tag = _TAG_RE.search(course_name)
    if tag:
        code = tag.group(1).lower()
        if code in _ISO_639_1:
            return LanguageSpec(target_language=code, detected_via="tag")
        # Tag presente mas código desconhecido → ambíguo: cai no default (se houver).
        if default_target:
            return LanguageSpec(target_language=default_target.lower(), detected_via="tag")
        return None

    normalized = _normalize(course_name)

    # 2. Stoplist (sem tag) → exige a tag; não é curso de idioma.
    if any(term in normalized for term in _STOPLIST):
        return None

    # 3. Heurística por palavra-chave (fronteira de palavra para evitar substrings).
    for keyword, iso in _KEYWORD_TO_ISO.items():
        if re.search(rf"\b{re.escape(keyword)}\b", normalized):
            return LanguageSpec(target_language=iso, detected_via="heuristic")

    # 4. Sem match.
    return None
