"""add lgpd guardian consent and data subject requests

Plano LGPD (P1.1 + P1.2 — _docs/docs/PLANO_LGPD.md):
  - Students.guardian_phone / Students.is_child (Art. 14 — informados pela
    escola controladora via planilha, colunas opcionais).
  - GuardianConsents: opt-in do responsável com versão do termo (accountability).
  - DataSubjectRequests: trilha de auditoria dos pedidos de titulares (Art. 18).

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-06-12
"""
from alembic import op
import sqlalchemy as sa

revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("Students", sa.Column("guardian_phone", sa.String(255), nullable=True))
    op.add_column(
        "Students",
        sa.Column("is_child", sa.Boolean(), nullable=False, server_default="false"),
    )

    op.create_table(
        "GuardianConsents",
        sa.Column("id", sa.String(255), primary_key=True),
        sa.Column("student_id", sa.String(255), sa.ForeignKey("Students.id"), nullable=False),
        sa.Column("guardian_phone", sa.String(255), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="pending"),
        sa.Column("term_version", sa.String(50), nullable=False, server_default="v1"),
        sa.Column("requested_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("consented_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("idx_guardian_consent_status", "GuardianConsents", ["student_id", "status"])
    op.create_index("ix_GuardianConsents_student_id", "GuardianConsents", ["student_id"])

    op.create_table(
        "DataSubjectRequests",
        sa.Column("id", sa.String(255), primary_key=True),
        # Sem FK de propósito: o registro do pedido sobrevive à anonimização.
        sa.Column("student_id", sa.String(255), nullable=True),
        sa.Column("request_type", sa.String(50), nullable=False),
        sa.Column("status", sa.String(50), nullable=False, server_default="received"),
        sa.Column("channel", sa.String(50), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index("ix_DataSubjectRequests_student_id", "DataSubjectRequests", ["student_id"])


def downgrade() -> None:
    op.drop_index("ix_DataSubjectRequests_student_id", table_name="DataSubjectRequests")
    op.drop_table("DataSubjectRequests")
    op.drop_index("ix_GuardianConsents_student_id", table_name="GuardianConsents")
    op.drop_index("idx_guardian_consent_status", table_name="GuardianConsents")
    op.drop_table("GuardianConsents")
    op.drop_column("Students", "is_child")
    op.drop_column("Students", "guardian_phone")
