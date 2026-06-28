"""Gamificação: ativação POR TURMA (Classes.gamification_enabled).

Revision ID: c1d2e3f4a5b6
Revises: c0d1e2f3a4b5
Create Date: 2026-06-28 11:00:00.000000

Resumo: a ativação da gamificação passa a ser decidida por turma na planilha de
cadastro (aba "Turmas", coluna "Gaming"). O flag global GAMIFICATION_ENABLED
vira a chave-mestra/kill-switch; esta coluna é a opção real por turma (default
false ⇒ turma fora da gamificação até a planilha optar). O componente de NOTA
por turma reusa as colunas já existentes grade_weight_pct/weekly_xp_goal
(coluna "Nota Engajamento" → defaults do sistema).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "c0d1e2f3a4b5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "Classes",
        sa.Column("gamification_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("Classes", "gamification_enabled")
