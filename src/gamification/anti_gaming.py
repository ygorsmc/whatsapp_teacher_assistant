"""Anti-gaming — detecção DETERMINÍSTICA de burla (timestamps + contadores).

Ambientes de recompensa competitiva incentivam burlar regras. Tudo aqui é
puro (zero LLM, zero I/O): roda no hook de validação de quiz, que já é if/else.
Falso positivo NÃO pune — só retém o XP daquele evento e levanta `xp_suspect`
(o professor vê e pode desconsiderar). Limiares são calibráveis por env var e a
detecção combina sinais (não um só).
"""

from __future__ import annotations

from datetime import datetime, timedelta

from src.shared.config import settings


def is_flash_answer(
    sent_at: datetime | None,
    answered_at: datetime | None,
    *,
    threshold_seconds: float | None = None,
) -> bool:
    """Resposta-relâmpago: respondeu antes do tempo mínimo de leitura.

    `answered_at - sent_at < threshold` ⇒ suspeito. Sem um dos timestamps,
    NÃO acusa (best-effort: a ausência de sinal nunca penaliza o aluno).
    """
    if sent_at is None or answered_at is None:
        return False
    if threshold_seconds is None:
        threshold_seconds = settings.XP_FLASH_ANSWER_SECONDS
    delta = answered_at - sent_at
    # Defensivo contra relógios/ordens invertidas: negativo não é relâmpago.
    if delta < timedelta(0):
        return False
    return delta < timedelta(seconds=threshold_seconds)


def is_repetitive_pattern(
    recent_answers: list[str],
    recent_correct: list[bool],
    *,
    min_run: int = 4,
    max_accuracy: float = 0.5,
) -> bool:
    """Padrão repetitivo: mesma alternativa em sequência LONGA E baixa acurácia.

    Exige os DOIS sinais (sempre a mesma letra E acertando pouco) — quem de fato
    sabe a matéria e calha de marcar "C" várias vezes seguidas com alta acurácia
    NÃO é acusado. `recent_*` vêm em ordem cronológica (mais recentes ao final).
    """
    if len(recent_answers) < min_run:
        return False
    tail = recent_answers[-min_run:]
    if len(set(tail)) != 1:
        return False  # não é a mesma alternativa repetida
    if not recent_correct:
        return True
    window = recent_correct[-min_run:]
    accuracy = sum(1 for c in window if c) / len(window)
    return accuracy <= max_accuracy


def rag_doubt_over_cap(doubts_awarded_today: int, *, cap: int | None = None) -> bool:
    """Farming de RAG: acima do teto de perguntas-XP/dia, o excedente = 0 XP
    (a dúvida ainda é respondida — só não pontua)."""
    if cap is None:
        cap = settings.XP_RAG_DOUBT_DAILY_CAP
    return doubts_awarded_today >= cap
