"""Streak de engajamento — máquina de estado PURA, inclusiva e gentil.

Bar baixo: QUALQUER ato que gere XP conta o dia. O diferencial é a PROTEÇÃO —
o aluno GANHA escudos (não compra: +1 a cada marco de 7 dias, máx. 2). Um dia
perdido consome automaticamente um escudo e PRESERVA o streak, sem culpa. Sem
escudo, o streak zera — mas o RECORDE persiste e NUNCA há penalidade de XP.

Marcos unificados com as medalhas de leitura: 3 · 7 · 14 · 30 · 100 dias.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# Marcos que viram nível do badge 🔥 Chama Viva e (nos múltiplos de 7) rendem escudo.
STREAK_MILESTONES = (3, 7, 14, 30, 100)
MAX_SHIELDS = 2


@dataclass
class StreakState:
    streak: int = 0
    best: int = 0
    shields: int = 0
    last_active_date: date | None = None


@dataclass
class StreakOutcome:
    state: StreakState
    incremented: bool = False
    already_active_today: bool = False
    shield_consumed: int = 0           # quantos escudos foram gastos para emendar
    shield_earned: int = 0             # escudo ganho ao bater múltiplo de 7
    reset: bool = False                # quebrou (sem escudo p/ emendar)
    milestone: int | None = None       # marco batido HOJE (3/7/14/30/100) ou None
    events: list[str] = field(default_factory=list)


def apply_activity(
    state: StreakState,
    today: date,
    *,
    shields_enabled: bool = True,
    max_shields: int = MAX_SHIELDS,
) -> StreakOutcome:
    """Aplica UM ato válido do dia ao streak e devolve o novo estado + eventos.

    Idempotente no mesmo dia: se já houve atividade hoje, não conta de novo.
    """
    cur = StreakState(state.streak, state.best, state.shields, state.last_active_date)

    # Já contou hoje → no-op (idempotência diária).
    if cur.last_active_date == today:
        out = StreakOutcome(state=cur, already_active_today=True)
        return out

    if cur.last_active_date is None:
        # Primeira atividade de sempre.
        cur.streak = 1
        cur.last_active_date = today
        return _finish(cur, max_shields, incremented=True)

    gap_days = (today - cur.last_active_date).days

    if gap_days <= 0:
        # Data fora de ordem (relógio) — trata como mesmo dia, sem mexer.
        out = StreakOutcome(state=cur, already_active_today=True)
        return out

    if gap_days == 1:
        # Dia consecutivo: emenda natural.
        cur.streak += 1
        cur.last_active_date = today
        return _finish(cur, max_shields, incremented=True)

    # gap_days >= 2 ⇒ faltaram (gap_days - 1) dias.
    missed = gap_days - 1
    if shields_enabled and cur.shields >= missed and missed >= 1:
        # Escudo(s) emendam a(s) falta(s): preserva o streak e conta hoje.
        cur.shields -= missed
        cur.streak += 1
        cur.last_active_date = today
        out = _finish(cur, max_shields, incremented=True)
        out.shield_consumed = missed
        out.events.append("shield_consumed")
        return out

    # Sem escudo p/ emendar: quebra. Recorde persiste; hoje recomeça em 1.
    out = StreakOutcome(state=cur, reset=True)
    out.events.append("streak_reset")
    cur.streak = 1
    cur.last_active_date = today
    finished = _finish(cur, max_shields, incremented=True)
    # Preserva o flag de reset + recorde já consolidado no _finish.
    finished.reset = True
    finished.events = ["streak_reset"] + finished.events
    return finished


def _finish(state: StreakState, max_shields: int, *, incremented: bool) -> StreakOutcome:
    """Consolida recorde, marco e escudo de marco (múltiplo de 7)."""
    out = StreakOutcome(state=state, incremented=incremented)

    if state.streak in STREAK_MILESTONES:
        out.milestone = state.streak
        out.events.append(f"milestone_{state.streak}")

    # Escudo ganho ao BATER um múltiplo de 7 (máx. guardado = max_shields).
    if state.streak > 0 and state.streak % 7 == 0 and state.shields < max_shields:
        state.shields = min(state.shields + 1, max_shields)
        out.shield_earned = 1
        out.events.append("shield_earned")

    if state.streak > state.best:
        state.best = state.streak

    return out
