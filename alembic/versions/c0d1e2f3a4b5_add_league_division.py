"""Gamificação Fase 1: WeeklyEngagement.division (Ligas — promo/rebaixamento).

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-06-28 09:00:00.000000

Resumo (esquema §4 "Promoção/rebaixamento"): a divisão (bronze→diamante) em que
o aluno fica para a PRÓXIMA semana, resultado do movimento da liga. NULL =
ranking flat (Fase 0): com LEAGUES_ENABLED off a coluna nunca é escrita ⇒
comportamento idêntico ao da Fase 0.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c0d1e2f3a4b5"
down_revision: Union[str, None] = "b9c0d1e2f3a4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("WeeklyEngagement", sa.Column("division", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("WeeklyEngagement", "division")
