"""Analytics por DISCIPLINA: StudentInteractions.subject_id.

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-07-01 22:00:00.000000

Resumo: o ledger de interações (XP, perguntas, quizzes) passa a carimbar a
Disciplina (Subjects.id) do evento, habilitando o /minha_xp e o /engajamento
do aluno POR DISCIPLINA. Coluna nullable e SEM FK de propósito: é um carimbo
de analytics (linhas antigas ficam NULL = "Geral"; apagar/renomear disciplina
não pode quebrar o histórico). Sem backfill — a atribuição começa a valer dos
eventos novos em diante.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "StudentInteractions",
        sa.Column("subject_id", sa.String(255), nullable=True),
    )
    # Agrupamentos por disciplina do aluno (/minha_xp, /engajamento do aluno).
    op.create_index(
        "idx_interaction_student_subject",
        "StudentInteractions",
        ["student_id", "subject_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_interaction_student_subject", table_name="StudentInteractions")
    op.drop_column("StudentInteractions", "subject_id")
