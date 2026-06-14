"""add Students.messaging_opted_out (opt-out por thread — F9 / Quality Rating Meta)

O aluno pode encerrar o recebimento de mensagens proativas a qualquer momento
("Envie SAIR") — exigência de Quality Rating da Meta. Coluna booleana (default
false): nenhum aluno opta out por padrão ⇒ comportamento atual intocado. As filas
proativas passam a respeitar a flag (cláusula SQL compartilhada).

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-06-14
"""
from alembic import op
import sqlalchemy as sa

revision = "a3b4c5d6e7f8"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "Students",
        sa.Column("messaging_opted_out", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("Students", "messaging_opted_out")
