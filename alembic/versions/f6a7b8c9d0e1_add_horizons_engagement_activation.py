"""Horizontes H1-H3: ClassChallenges, SentinelAlerts, ActivationKeys,
KeyActivations, Rubrics + challenge_id em PendingQuizzes + registration_source/
consent_at em Students.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-12 09:00:00.000000

Resumo:
  - "ClassChallenges": desafio de turma (H1.1) e simulado cronometrado (H2.3)
    — agregação anônima de respostas via PendingQuizzes.challenge_id.
  - "SentinelAlerts": dedupe dos alertas da Sentinela Emocional (H1.2).
  - "ActivationKeys"/"KeyActivations": ativação por QR Code (H2.1 leitor /
    H3.1 professor) com auditoria anti-fraude.
  - "Rubrics": critérios de correção dissertativa do professor (H2.2).
  - Students.registration_source/consent_at: auto-cadastro LGPD (H2.1).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. ClassChallenges — Desafio de Turma / Simulado Cronometrado
    op.create_table(
        "ClassChallenges",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("class_id", sa.String(length=255), nullable=False),
        sa.Column("quiz_bank_id", sa.String(length=255), nullable=True),
        sa.Column("kind", sa.String(length=50), nullable=False, server_default="challenge"),
        sa.Column("question_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("sent_at", sa.DateTime(), nullable=False),
        sa.Column("closes_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="open"),
        sa.Column("participants_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correct_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.ForeignKeyConstraint(["class_id"], ["Classes.id"]),
        sa.ForeignKeyConstraint(["quiz_bank_id"], ["QuizBank.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ClassChallenges_class_id"), "ClassChallenges", ["class_id"], unique=False)
    op.create_index(op.f("ix_ClassChallenges_quiz_bank_id"), "ClassChallenges", ["quiz_bank_id"], unique=False)
    op.create_index("idx_challenge_close", "ClassChallenges", ["closes_at", "status"], unique=False)

    # 2. SentinelAlerts — dedupe da Sentinela Emocional
    op.create_table(
        "SentinelAlerts",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("student_id", sa.String(length=255), nullable=False),
        sa.Column("class_id", sa.String(length=255), nullable=True),
        sa.Column("rule", sa.String(length=100), nullable=False),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("notified_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.ForeignKeyConstraint(["student_id"], ["Students.id"]),
        sa.ForeignKeyConstraint(["class_id"], ["Classes.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_SentinelAlerts_student_id"), "SentinelAlerts", ["student_id"], unique=False)
    op.create_index(op.f("ix_SentinelAlerts_class_id"), "SentinelAlerts", ["class_id"], unique=False)
    op.create_index("idx_sentinel_dedupe", "SentinelAlerts", ["student_id", "rule", "notified_at"], unique=False)

    # 3. ActivationKeys — chaves de QR Code (leitor/professor)
    op.create_table(
        "ActivationKeys",
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="reader"),
        sa.Column("class_id", sa.String(length=255), nullable=True),
        sa.Column("batch", sa.String(length=100), nullable=True),
        sa.Column("max_activations", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("activation_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="available"),
        sa.Column("first_activated_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.ForeignKeyConstraint(["class_id"], ["Classes.id"]),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index(op.f("ix_ActivationKeys_class_id"), "ActivationKeys", ["class_id"], unique=False)
    op.create_index("idx_activation_key_class", "ActivationKeys", ["class_id", "status"], unique=False)

    # 4. KeyActivations — auditoria anti-fraude
    op.create_table(
        "KeyActivations",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("student_id", sa.String(length=255), nullable=True),
        sa.Column("teacher_id", sa.String(length=255), nullable=True),
        sa.Column("channel", sa.String(length=20), nullable=False, server_default="whatsapp"),
        sa.Column("activated_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.ForeignKeyConstraint(["key"], ["ActivationKeys.key"]),
        sa.ForeignKeyConstraint(["student_id"], ["Students.id"]),
        sa.ForeignKeyConstraint(["teacher_id"], ["Teachers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_KeyActivations_key"), "KeyActivations", ["key"], unique=False)
    op.create_index(op.f("ix_KeyActivations_student_id"), "KeyActivations", ["student_id"], unique=False)
    op.create_index(op.f("ix_KeyActivations_teacher_id"), "KeyActivations", ["teacher_id"], unique=False)

    # 5. Rubrics — correção dissertativa ancorada no professor
    op.create_table(
        "Rubrics",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("course_id", sa.String(length=255), nullable=False),
        sa.Column("subject_id", sa.String(length=255), nullable=True),
        sa.Column("criteria", sa.Text(), nullable=False),
        sa.Column("scale", sa.String(length=100), nullable=False, server_default="0 a 10"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.ForeignKeyConstraint(["course_id"], ["Courses.id"]),
        sa.ForeignKeyConstraint(["subject_id"], ["Subjects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_Rubrics_course_id"), "Rubrics", ["course_id"], unique=False)
    op.create_index(op.f("ix_Rubrics_subject_id"), "Rubrics", ["subject_id"], unique=False)
    op.create_index("idx_rubric_scope", "Rubrics", ["course_id", "subject_id"], unique=False)

    # 6. PendingQuizzes.challenge_id
    with op.batch_alter_table("PendingQuizzes") as batch_op:
        batch_op.add_column(sa.Column("challenge_id", sa.String(length=255), nullable=True))
        batch_op.create_index("ix_PendingQuizzes_challenge_id", ["challenge_id"])
        batch_op.create_foreign_key(
            "fk_PendingQuizzes_challenge_id_ClassChallenges",
            "ClassChallenges",
            ["challenge_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # 7. Students.registration_source + consent_at (auto-cadastro LGPD)
    with op.batch_alter_table("Students") as batch_op:
        batch_op.add_column(
            sa.Column("registration_source", sa.String(length=50), nullable=False, server_default="spreadsheet")
        )
        batch_op.add_column(sa.Column("consent_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("Students") as batch_op:
        batch_op.drop_column("consent_at")
        batch_op.drop_column("registration_source")

    with op.batch_alter_table("PendingQuizzes") as batch_op:
        batch_op.drop_constraint("fk_PendingQuizzes_challenge_id_ClassChallenges", type_="foreignkey")
        batch_op.drop_index("ix_PendingQuizzes_challenge_id")
        batch_op.drop_column("challenge_id")

    op.drop_index("idx_rubric_scope", table_name="Rubrics")
    op.drop_index(op.f("ix_Rubrics_subject_id"), table_name="Rubrics")
    op.drop_index(op.f("ix_Rubrics_course_id"), table_name="Rubrics")
    op.drop_table("Rubrics")

    op.drop_index(op.f("ix_KeyActivations_teacher_id"), table_name="KeyActivations")
    op.drop_index(op.f("ix_KeyActivations_student_id"), table_name="KeyActivations")
    op.drop_index(op.f("ix_KeyActivations_key"), table_name="KeyActivations")
    op.drop_table("KeyActivations")

    op.drop_index("idx_activation_key_class", table_name="ActivationKeys")
    op.drop_index(op.f("ix_ActivationKeys_class_id"), table_name="ActivationKeys")
    op.drop_table("ActivationKeys")

    op.drop_index("idx_sentinel_dedupe", table_name="SentinelAlerts")
    op.drop_index(op.f("ix_SentinelAlerts_class_id"), table_name="SentinelAlerts")
    op.drop_index(op.f("ix_SentinelAlerts_student_id"), table_name="SentinelAlerts")
    op.drop_table("SentinelAlerts")

    op.drop_index("idx_challenge_close", table_name="ClassChallenges")
    op.drop_index(op.f("ix_ClassChallenges_quiz_bank_id"), table_name="ClassChallenges")
    op.drop_index(op.f("ix_ClassChallenges_class_id"), table_name="ClassChallenges")
    op.drop_table("ClassChallenges")
