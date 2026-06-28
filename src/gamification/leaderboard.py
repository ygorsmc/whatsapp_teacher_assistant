"""Leaderboard anônimo por coorte — cálculo PURO (sem I/O).

Ranking RELATIVO por coorte (~25–30), anônimo, semanal. Mostra top 3 + a
vizinhança do aluno; o fundo NUNCA é exposto como "último". Coortes pequenas
(< MIN) não circulam placar (regra LGPD). Coorte = turma; turma grande é
fatiada em blocos PAREADOS por engajamento (ordena por XP, fatia) — resolve o
iniciante que vê o líder com 10.000 pontos e desiste.

Aqui só a MATEMÁTICA (partição, rank, recorte da vizinhança). A renderização da
mensagem fica no worker (acoplada às constantes de `messages.py`).
"""

from __future__ import annotations

from dataclasses import dataclass


# ── Ligas (Fase 1) — divisões estilo Duolingo (baixa → alta) ─────────────────
# O novato entra na mais baixa; sobe ao terminar no topo da coorte, desce ao
# terminar no fundo. Densidade: agrupar turmas paralelas do mesmo curso já é
# resolvido a montante (a coorte é por curso). Rótulos legíveis p/ o aluno.
DIVISIONS: tuple[str, ...] = ("bronze", "prata", "ouro", "safira", "diamante")
DIVISION_LABEL = {
    "bronze": "🥉 Liga Bronze",
    "prata": "🥈 Liga Prata",
    "ouro": "🥇 Liga Ouro",
    "safira": "💠 Liga Safira",
    "diamante": "💎 Liga Diamante",
}


def division_index(division: str | None) -> int:
    """Índice da divisão (0 = mais baixa). None/desconhecida ⇒ a mais baixa."""
    if division in DIVISIONS:
        return DIVISIONS.index(division)
    return 0


def promote(division: str | None) -> str:
    i = division_index(division)
    return DIVISIONS[min(i + 1, len(DIVISIONS) - 1)]


def relegate(division: str | None) -> str:
    i = division_index(division)
    return DIVISIONS[max(i - 1, 0)]


def apply_league_movement(
    division: str | None,
    rank: int,
    cohort_size: int,
    *,
    promote_top: int = 5,
    relegate_bottom: int = 5,
) -> tuple[str, str]:
    """Movimento da liga ao fim da semana → (nova_divisão, evento).

    `evento` ∈ {'promoted','relegated','stay'}. O topo `promote_top` sobe; o fundo
    `relegate_bottom` desce (desde que a coorte seja grande o bastante p/ não
    rebaixar quem também está no topo). Coorte pequena = todo mundo fica.
    """
    cur = division if division in DIVISIONS else DIVISIONS[0]
    if cohort_size < max(promote_top, relegate_bottom) + 1:
        return cur, "stay"
    if rank <= promote_top:
        nxt = promote(cur)
        return nxt, ("promoted" if nxt != cur else "stay")
    if rank > cohort_size - relegate_bottom:
        nxt = relegate(cur)
        return nxt, ("relegated" if nxt != cur else "stay")
    return cur, "stay"


@dataclass(frozen=True)
class RankedEntry:
    student_id: str
    xp: int
    rank: int


def partition_into_cohorts(
    ranked: list[tuple[str, int]],
    target_size: int,
) -> list[list[tuple[str, int]]]:
    """Fatia (student_id, xp) JÁ ordenado por XP desc em coortes ~`target_size`,
    pareadas por engajamento (vizinhos no XP ficam na mesma coorte).

    ≤ target_size ⇒ uma coorte só (a turma é a coorte). Acima, divide em blocos
    de tamanho equilibrado (nº de blocos = ceil(total/target); evita um bloco
    órfão minúsculo no fim).
    """
    n = len(ranked)
    if n == 0:
        return []
    if n <= target_size:
        return [list(ranked)]

    num_blocks = (n + target_size - 1) // target_size
    base = n // num_blocks
    rem = n % num_blocks  # os `rem` primeiros blocos levam 1 a mais

    cohorts: list[list[tuple[str, int]]] = []
    i = 0
    for b in range(num_blocks):
        size = base + (1 if b < rem else 0)
        cohorts.append(list(ranked[i:i + size]))
        i += size
    return cohorts


def rank_cohort(cohort: list[tuple[str, int]]) -> list[RankedEntry]:
    """Ordena por XP desc (desempate estável por student_id) e atribui rank 1..n."""
    ordered = sorted(cohort, key=lambda t: (-t[1], t[0]))
    return [RankedEntry(sid, xp, i + 1) for i, (sid, xp) in enumerate(ordered)]


@dataclass
class LeagueView:
    cohort_size: int
    my_rank: int | None
    my_xp: int
    top3: list[RankedEntry]
    neighbors: list[RankedEntry]     # vizinhança (inclui o próprio aluno)
    xp_to_next: int | None           # quanto falta p/ subir uma posição (None no topo)
    circulates: bool                 # coorte >= min_cohort?


def build_league_view(
    ranked: list[RankedEntry],
    me_id: str,
    *,
    min_cohort: int = 3,
    neighbors_radius: int = 1,
) -> LeagueView:
    """Monta o recorte que o aluno vê: top 3 + vizinhança (±radius). Nunca a
    lista inteira nem o rótulo "Nº de N". `circulates=False` ⇒ só progresso
    pessoal (coorte pequena demais)."""
    size = len(ranked)
    by_id = {e.student_id: e for e in ranked}
    me = by_id.get(me_id)
    my_rank = me.rank if me else None
    my_xp = me.xp if me else 0

    circulates = size >= min_cohort
    top3 = [e for e in ranked if e.rank <= 3]

    neighbors: list[RankedEntry] = []
    xp_to_next: int | None = None
    if me is not None:
        lo = max(1, me.rank - neighbors_radius)
        hi = min(size, me.rank + neighbors_radius)
        neighbors = [e for e in ranked if lo <= e.rank <= hi]
        if me.rank > 1:
            ahead = next((e for e in ranked if e.rank == me.rank - 1), None)
            if ahead is not None:
                xp_to_next = max(0, ahead.xp - me.xp)

    return LeagueView(
        cohort_size=size,
        my_rank=my_rank,
        my_xp=my_xp,
        top3=top3,
        neighbors=neighbors,
        xp_to_next=xp_to_next,
        circulates=circulates,
    )
