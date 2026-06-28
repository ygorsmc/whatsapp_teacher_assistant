"""Exportação de engajamento (Fase 1, Modelo A — relatório, não nota).

Lógica PURA de formatação: transforma os registros por aluno (já buscados no
banco pelo worker) em linhas tabulares + CSV + tabela de texto. O professor
integra MANUALMENTE ao diário de classe — a decisão de nota fica FORA do sistema
(Modelo A puro). Sem I/O aqui.
"""

from __future__ import annotations

from dataclasses import dataclass

EXPORT_HEADER = [
    "Aluno", "Dias ativos (7d)", "Quizzes (7d)", "Revisões SRS",
    "Seções de leitura", "Sequência (dias)", "XP da semana",
]


@dataclass
class EngagementRecord:
    name: str
    days_active: int
    quizzes: int
    srs_reviews: int
    reading_sections: int
    streak: int
    week_xp: int


def build_engagement_export_rows(records: list[EngagementRecord]) -> list[list[str]]:
    """Cabeçalho + uma linha por aluno (tudo string, pronto p/ CSV/Sheets)."""
    rows: list[list[str]] = [list(EXPORT_HEADER)]
    for r in records:
        rows.append([
            r.name, str(r.days_active), str(r.quizzes), str(r.srs_reviews),
            str(r.reading_sections), str(r.streak), str(r.week_xp),
        ])
    return rows


def _csv_escape(value: str) -> str:
    if any(c in value for c in (',', '"', '\n')):
        return '"' + value.replace('"', '""') + '"'
    return value


def rows_to_csv(rows: list[list[str]]) -> str:
    """Serializa as linhas em CSV (RFC 4180 simplificado)."""
    return "\n".join(",".join(_csv_escape(c) for c in row) for row in rows)
