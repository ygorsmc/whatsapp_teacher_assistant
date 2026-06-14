"""add StudentEnrollment.cefr_level (vertical de idiomas — F5 Nivelamento)

O teste de nivelamento (placement) classifica o aluno em A1–C2 e grava o resultado
POR MATRÍCULA (aluno × turma) — granularidade individual que afina o nível por turma
(Classes.proficiency_level continua sendo o default da turma). Coluna opcional (NULL =
não nivelado) → comportamento atual intocado.

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-06-14
"""
from alembic import op
import sqlalchemy as sa

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("StudentEnrollment", sa.Column("cefr_level", sa.String(10), nullable=True))


def downgrade() -> None:
    op.drop_column("StudentEnrollment", "cefr_level")
