"""add preferred_channel to Students (roteamento omnichannel)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-06 00:30:00.000000

Roteamento dinâmico "follow-the-user": o aluno recebe o próximo quiz no canal
onde mandou a última mensagem. O webhook grava o canal a cada mensagem; o
scheduler lê esta coluna para escolher o canal de entrega. NULL = canal global.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("Students") as batch_op:
        batch_op.add_column(sa.Column("preferred_channel", sa.String(length=50), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("Students") as batch_op:
        batch_op.drop_column("preferred_channel")
