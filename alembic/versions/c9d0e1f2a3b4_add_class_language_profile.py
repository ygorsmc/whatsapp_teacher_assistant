"""add class language profile (idioma + nivel)

Vertical de Idiomas — perfil de idioma por TURMA (vindo da aba "Turmas" da
planilha: colunas opcionais Idioma=Sim/Não e Nível). Decide a língua do TEXTO da
revisão por nível (Iniciante → pt-BR; demais → língua-alvo) e habilita o áudio na
língua-alvo. Colunas opcionais/seguras: flag off por padrão; nível NULL = turma
não-idioma ou nível não informado → comportamento atual intocado.

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-06-13
"""
from alembic import op
import sqlalchemy as sa

revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "Classes",
        sa.Column("is_language", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "Classes",
        sa.Column("proficiency_level", sa.String(20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("Classes", "proficiency_level")
    op.drop_column("Classes", "is_language")
