"""add file_sync_state.attempts (contador de tentativas → dead_letter após 3)

A coluna `attempts` existia só no model (`FileSyncState`) e era usada pelo
ingestion_worker (marca `concluído` com `attempts=0`; em falha incrementa e vira
`dead_letter` após 3) — mas nenhuma migration a criava. Em bancos montados por
migration (prod), o UPDATE final quebrava com `UndefinedColumnError`, deixando o
arquivo preso e causando REINGESTÃO em loop (re-OCR a cada ciclo). Integer,
server_default 0, nullable (o código usa COALESCE(attempts, 0)).

Revision ID: a8b9c0d1e2f3
Revises: a3b4c5d6e7f8
Create Date: 2026-06-19
"""
from alembic import op
import sqlalchemy as sa

revision = "a8b9c0d1e2f3"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "file_sync_state",
        sa.Column("attempts", sa.Integer(), nullable=True, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("file_sync_state", "attempts")
