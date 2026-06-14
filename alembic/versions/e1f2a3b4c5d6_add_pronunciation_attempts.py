"""add PronunciationAttempts (vertical de idiomas — F3 Speaking/Pronúncia)

Guarda APENAS os scores da análise de fala (accuracy/fluency/completeness + detalhe
por palavra em JSON). O ÁUDIO é descartado após a análise (LGPD — voz é dado pessoal
sensível): nunca persistimos o arquivo de voz, só os números. FK em CASCADE para que
o expurgo/anonimização do aluno leve os scores junto.

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
Create Date: 2026-06-14
"""
from alembic import op
import sqlalchemy as sa

revision = "e1f2a3b4c5d6"
down_revision = "d0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "PronunciationAttempts",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("student_id", sa.String(255), sa.ForeignKey("Students.id", ondelete="CASCADE"), nullable=False),
        sa.Column("course_id", sa.String(255), sa.ForeignKey("Courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("language", sa.String(20), nullable=True),       # ISO 639-1 do alvo
        sa.Column("item_ref", sa.Text(), nullable=True),           # texto de referência (transcrição), sem áudio
        sa.Column("accuracy", sa.Float(), nullable=True),
        sa.Column("fluency", sa.Float(), nullable=True),
        sa.Column("completeness", sa.Float(), nullable=True),
        sa.Column("details_json", sa.Text(), nullable=True),       # scores por palavra (JSON), sem áudio
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_PronunciationAttempts_student_id", "PronunciationAttempts", ["student_id"])
    op.create_index("idx_pron_attempts_scope", "PronunciationAttempts", ["course_id", "student_id"])


def downgrade() -> None:
    op.drop_index("idx_pron_attempts_scope", table_name="PronunciationAttempts")
    op.drop_index("ix_PronunciationAttempts_student_id", table_name="PronunciationAttempts")
    op.drop_table("PronunciationAttempts")
