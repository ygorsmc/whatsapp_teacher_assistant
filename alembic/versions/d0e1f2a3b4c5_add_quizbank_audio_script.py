"""add QuizBank.audio_script (vertical de idiomas — áudio na língua-alvo)

O pacote pedagógico do QuizBank passa a guardar, opcionalmente, o roteiro de ÁUDIO
na língua-alvo (palavra/expressão + exemplo no vocabulário; revisão traduzida).
Persistir é necessário para o áudio sobreviver ao Cache Hit e à reentrega da
Repetição Espaçada (SRS), sem nova chamada de LLM. Coluna opcional (NULL) — sem
áudio = comportamento atual.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-06-14
"""
from alembic import op
import sqlalchemy as sa

revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("QuizBank", sa.Column("audio_script", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("QuizBank", "audio_script")
