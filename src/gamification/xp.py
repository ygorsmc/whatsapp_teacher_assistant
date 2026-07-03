"""XP de Engajamento — a mãe-métrica (lógica pura).

Um único XP move streak, liga e conquistas (lição do Duolingo). O XP é de
ESFORÇO, não de nota: errar um quiz ainda pontua (menos que acertar). Tudo aqui
é determinístico — sem I/O.

Ações que geram XP (pesos calibráveis por env var em `settings`):
    quiz_correct, quiz_wrong, srs_ontime, reading, rag_doubt, essay, voice
+ bônus de consistência (1º ato válido do dia) e teto diário (anti-grind).

O XP COMPETITIVO é semanal e zera segunda 00:00 BRT — a chave é `week_id`
(ISO week da data LOCAL/BRT). Acima do teto diário, a ação ainda conta para
streak/aprendizado, mas não soma XP competitivo.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, NamedTuple

from src.shared.config import settings

# Conjunto canônico de ações pontuáveis (a fonte das fontes da §3 do esquema).
XP_ACTIONS = (
    "quiz_correct",
    "quiz_wrong",
    "srs_ontime",
    "reading",
    "rag_doubt",
    "essay",
    "voice",
)


def base_xp_for(action: str, *, weights: dict | None = None) -> int:
    """XP base de uma ação (antes de consistência e teto). Ação desconhecida = 0.

    `weights` permite injetar valores nos testes; por padrão lê `settings`.
    """
    if weights is None:
        weights = {
            "quiz_correct": settings.XP_QUIZ_CORRECT,
            "quiz_wrong": settings.XP_QUIZ_WRONG,
            "srs_ontime": settings.XP_SRS_ONTIME,
            "reading": settings.XP_READING,
            "rag_doubt": settings.XP_RAG_DOUBT,
            "essay": settings.XP_ESSAY,
            "voice": settings.XP_VOICE,
        }
    return int(weights.get(action, 0))


def week_id_for(dt_local: datetime) -> str:
    """ISO week da data LOCAL (BRT): "2026-W26". Fronteira da liga (segunda 00:00).

    Usa o calendário ISO (semana começa na segunda) — coerente com o reinício
    semanal do esquema. Receba SEMPRE um datetime já convertido para BRT.
    """
    iso = dt_local.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def cap_remaining(awarded_today: int, cap: int | None = None) -> int:
    """Quanto ainda cabe no teto diário de XP competitivo (nunca negativo)."""
    if cap is None:
        cap = settings.XP_DAILY_CAP
    return max(0, cap - max(0, awarded_today))


def clamp_to_cap(amount: int, awarded_today: int, cap: int | None = None) -> int:
    """Limita `amount` ao que resta do teto diário. Acima do teto ⇒ 0 competitivo
    (a ação ainda vale para streak/aprendizado — isso é decidido no worker)."""
    return min(max(0, amount), cap_remaining(awarded_today, cap))


def consistency_bonus(is_first_valid_act_today: bool, *, bonus: int | None = None) -> int:
    """Bônus por COMPARECER: o 1º ato válido do dia rende um extra (lógica do
    'Local Legend' do Strava). Demais atos do dia = 0 de bônus."""
    if bonus is None:
        bonus = settings.XP_CONSISTENCY_BONUS
    return int(bonus) if is_first_valid_act_today else 0


# ==============================================================================
# /minha_xp — dobra e rotulagem por (curso, disciplina) — lógica pura
# ==============================================================================

class XpSubjectRow(NamedTuple):
    """Linha agregada do ledger por (curso, disciplina). `subject_*` vem NULL
    quando o evento não carimbou disciplina (ex.: dúvida/voz/redação numa turma
    sem Disciplina cadastrada) — nesse caso o LEFT JOIN devolve nome NULL."""
    course_id: str | None
    course_name: str | None
    subject_id: str | None
    subject_name: str | None
    total: int
    week: int


class XpLine(NamedTuple):
    """Seção renderizável do /minha_xp: rótulo 'Disciplina - Curso' + valores."""
    label: str
    total: int
    week: int


def xp_line_label(subject_name: str | None, course_name: str | None) -> str:
    """Rótulo da seção. Sempre inclui o curso quando conhecido (pedido do prof.):
    'B1 - English Course'. Sem disciplina identificada ⇒ 'Geral - {curso}'."""
    if subject_name and course_name:
        return f"{subject_name} - {course_name}"
    if subject_name:
        return subject_name
    if course_name:
        return f"Geral - {course_name}"
    return "Geral"


def fold_xp_lines(rows: Iterable[XpSubjectRow]) -> list[XpLine]:
    """Agrega o XP em seções 'Disciplina - Curso', ordenadas por total desc.

    O ponto-chave é a DOBRA por curso: eventos sem disciplina (subject NULL)
    não podem virar uma 'linha de curso' à parte da disciplina. Quando o curso
    tem UMA única disciplina identificada, todo o XP sem carimbo é atribuído a
    ela (o não-carimbo é falha de stamping, não uma disciplina de verdade) —
    assim 'English Course' (sem carimbo) e 'B1' viram uma só linha somada.
    Com 2+ disciplinas no curso a atribuição fica ambígua: o XP sem carimbo
    vira uma linha honesta 'Geral - {curso}'. Sem disciplina alguma, idem.
    """
    by_course: dict[str | None, list[XpSubjectRow]] = {}
    for r in rows:
        by_course.setdefault(r.course_id, []).append(r)

    # bucket: (course_id, subject_name|None) -> [total, week, course_name]
    buckets: dict[tuple[str | None, str | None], list] = {}
    for _course_id, crows in by_course.items():
        course_name = next((r.course_name for r in crows if r.course_name), None)
        named = sorted({r.subject_name for r in crows if r.subject_name})
        sole = named[0] if len(named) == 1 else None
        for r in crows:
            subject_name = r.subject_name or sole  # dobra o sem-carimbo na única disciplina
            key = (_course_id, subject_name)
            bucket = buckets.setdefault(key, [0, 0, course_name])
            bucket[0] += int(r.total or 0)
            bucket[1] += int(r.week or 0)

    lines = [
        XpLine(label=xp_line_label(subject_name, course_name), total=total, week=week)
        for (_course_id, subject_name), (total, week, course_name) in buckets.items()
        if total > 0 or week > 0
    ]
    lines.sort(key=lambda ln: (-ln.total, ln.label))
    return lines
