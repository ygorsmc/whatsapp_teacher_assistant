"""Conquistas persistentes — catálogo + avaliação DETERMINÍSTICA (pura).

Empilhadas em níveis (Bronze→Prata→Ouro→Lendária) para evitar o "monte de lixo
de medalhas". Premia MÚLTIPLAS dimensões de comportamento ligadas a aprendizado
(consistência, precisão, curiosidade, profundidade, pontualidade, resiliência,
esforço escrito, fala). Tudo aqui é contador → nível; sem LLM, sem I/O.

Badges NUNCA expiram. Os de descoberta (secretos) são por EVENTO e tratados no
worker (`SECRET_BADGES_ENABLED`), não por contador — não entram no `evaluate`.
"""

from __future__ import annotations

from dataclasses import dataclass

# Ordem de força dos níveis (rótulo persistido em StudentBadges.level).
LEVEL_ORDER = ("bronze", "prata", "ouro", "lendaria")
LEVEL_LABEL = {
    "bronze": "Bronze",
    "prata": "Prata",
    "ouro": "Ouro",
    "lendaria": "Lendária",
}


@dataclass(frozen=True)
class BadgeDef:
    code: str
    emoji: str
    name: str
    counter: str          # nome do contador que alimenta o badge
    # níveis: lista de (level, threshold) em ordem crescente de threshold.
    levels: tuple[tuple[str, int], ...]


# Catálogo inicial empilhado (§6). `counter` casa com as chaves do dict passado
# a `evaluate`. Badges comportamentais/meta/por-capítulo entram na Fase 1.
BADGE_CATALOG: tuple[BadgeDef, ...] = (
    BadgeDef("chama_viva", "🔥", "Chama Viva", "streak_best",
             (("bronze", 3), ("prata", 7), ("ouro", 30), ("lendaria", 100))),
    BadgeDef("na_mosca", "🎯", "Na Mosca", "best_correct_streak",
             (("bronze", 5), ("prata", 15), ("ouro", 40))),
    BadgeDef("mente_curiosa", "🔭", "Mente Curiosa", "rag_doubts",
             (("bronze", 10), ("prata", 30), ("ouro", 75))),
    BadgeDef("maratonista", "📚", "Maratonista da Leitura", "reading_sections",
             (("bronze", 5), ("prata", 20), ("ouro", 50))),
    BadgeDef("pontual", "⏰", "Pontual", "srs_ontime",
             (("bronze", 10), ("prata", 30), ("ouro", 80))),
    BadgeDef("volta_por_cima", "🌱", "Volta por Cima", "srs_recoveries",
             (("bronze", 5), ("prata", 15), ("ouro", 40))),
    BadgeDef("escriba", "✍️", "Escriba", "essays",
             (("bronze", 1), ("prata", 5), ("ouro", 15))),
    BadgeDef("boa_pronuncia", "🗣️", "Boa Pronúncia", "voice_above_threshold",
             (("bronze", 5), ("prata", 20), ("ouro", 50))),
)

BADGE_BY_CODE = {b.code: b for b in BADGE_CATALOG}


@dataclass(frozen=True)
class SecretBadgeDef:
    code: str
    emoji: str
    name: str
    hint: str   # frase revelada ao desbloquear (premia a descoberta)


# Badges de descoberta (Fase 1, `SECRET_BADGES_ENABLED`). Por EVENTO, não por
# contador — desbloqueados uma única vez pelos hooks do worker. Nível 'secreta'.
SECRET_BADGES: tuple[SecretBadgeDef, ...] = (
    SecretBadgeDef("coruja_filosofica", "🦉", "Coruja Filosófica",
                   "uma pergunta forte depois da meia-noite"),
    SecretBadgeDef("fenix", "🐦‍🔥", "Fênix",
                   "recuperou uma sequência de 30+ dias depois de perdê-la"),
    SecretBadgeDef("cacador_erratas", "🔍", "Caçador de Erratas",
                   "encarou um trecho que tinha uma errata viva"),
)
SECRET_BADGE_BY_CODE = {b.code: b for b in SECRET_BADGES}
SECRET_LEVEL = "secreta"


def level_for(badge: BadgeDef, value: int) -> str | None:
    """Nível MAIS ALTO atingido por `value` neste badge. None = ainda nada."""
    reached: str | None = None
    for level, threshold in badge.levels:
        if value >= threshold:
            reached = level
        else:
            break
    return reached


def level_rank(level: str | None) -> int:
    """Índice ordinal do nível (−1 = nenhum) — para comparar evolução."""
    if level is None:
        return -1
    return LEVEL_ORDER.index(level) if level in LEVEL_ORDER else -1


def next_threshold(badge: BadgeDef, value: int) -> tuple[str, int] | None:
    """(próximo nível, threshold) ainda não atingido, ou None se no topo."""
    for level, threshold in badge.levels:
        if value < threshold:
            return (level, threshold)
    return None


def evaluate(counters: dict[str, int]) -> list[tuple[str, str]]:
    """Dado um dict de contadores, devolve [(badge_code, level)] dos badges com
    ao menos o nível Bronze atingido. Determinístico; usado pelo worker para
    fazer o upsert (só sobe de nível, nunca regride)."""
    out: list[tuple[str, str]] = []
    for badge in BADGE_CATALOG:
        value = int(counters.get(badge.counter, 0) or 0)
        level = level_for(badge, value)
        if level is not None:
            out.append((badge.code, level))
    return out
