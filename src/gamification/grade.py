"""Engajamento como componente de NOTA (Fase 2, Modelo C) — lógica PURA.

⚠️ O ponto MAIS sensível do projeto. A evidência é cética (Hanus & Fox 2015:
gamificação com nota baixou motivação intrínseca e nota final). Por isso o
default é NÃO vincular (Modelo A), e quando se vincula tudo aqui é blindado:

  - **Por THRESHOLD de esforço, não por rank** — premia atingir um PISO semanal
    acessível ao aluno fraco, nunca a posição no leaderboard (rankear nota =
    pressão de performance + injustiça do N-effect).
  - **Só ADICIONA, nunca subtrai** — é um piso, não punição (o erro do ClassDojo).
  - **Fórmula 100% transparente** — `engajamento = min(XP_da_semana / META, 100%)`;
    o aluno SEMPRE sabe quanto falta.
  - **Teto de peso 5–10%** — `MAX_GRADE_WEIGHT_PCT` é o limite duro do sistema.
  - **Anti-gaming ANTES de contar** — o `XP_da_semana` usado já EXCLUI eventos
    `xp_suspect` (a soma do ledger filtra `xp_suspect=false`).

Sem I/O aqui — só a aritmética transparente.
"""

from __future__ import annotations

# Limite DURO do sistema para o peso na nota (pesquisa: peso baixo + transparência).
MAX_GRADE_WEIGHT_PCT = 10.0


def clamp_weight(weight_pct: float) -> float:
    """Garante o teto/piso do peso (0 < peso ≤ MAX). Acima do teto, corta no teto."""
    return max(0.0, min(float(weight_pct), MAX_GRADE_WEIGHT_PCT))


def engagement_fraction(week_xp: int, goal: int) -> float:
    """Fração da meta semanal atingida, em [0, 1]. Meta inválida ⇒ 0 (sem nota)."""
    if goal is None or goal <= 0:
        return 0.0
    return max(0.0, min(week_xp / goal, 1.0))


def grade_points(week_xp: int, goal: int, weight_pct: float) -> float:
    """Pontos de engajamento na nota (em pontos percentuais da nota final).

    `fração × peso`, SEMPRE ≥ 0 (só adiciona). Ex.: meta 350, fez 280 (80%),
    peso 10% ⇒ 8.0 (oito pontos percentuais da nota). Arredonda a 1 casa.
    """
    frac = engagement_fraction(week_xp, goal)
    return round(frac * clamp_weight(weight_pct), 1)


def xp_to_full(week_xp: int, goal: int) -> int:
    """Quanto XP ainda falta para a nota cheia de engajamento (0 se já atingiu)."""
    if goal is None or goal <= 0:
        return 0
    return max(0, int(goal) - int(week_xp))


def is_grade_enabled(weight_pct, goal) -> bool:
    """A turma é Modelo C? (ambos configurados e válidos). NULL/0 ⇒ Modelo A."""
    try:
        return weight_pct is not None and goal is not None and float(weight_pct) > 0 and int(goal) > 0
    except (TypeError, ValueError):
        return False
