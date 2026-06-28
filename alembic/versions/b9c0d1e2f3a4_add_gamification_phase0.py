"""Gamificação Fase 0: ledger de XP (StudentInteractions), streak/escudos
(Students), config opt-in de nota (Classes) + tabelas WeeklyEngagement e
StudentBadges.

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-06-27 20:30:00.000000

Resumo (esquema_gamificacao_whatsapp_teacher_assistant.md, §10):
  - StudentInteractions += xp_awarded/xp_week_id/xp_suspect (ledger de XP de
    Engajamento — colunas NULLABLE, flag OFF = nunca escritas).
  - Students += engagement_streak/_best/streak_shields/streak_last_active_date
    (streak guarda-chuva, número pessoal) + ranking_opted_out (/ocultar_ranking).
  - Classes += grade_weight_pct/weekly_xp_goal (opt-in da Fase 2; NULL = Modelo A).
  - "WeeklyEngagement": snapshot competitivo semanal (coorte, pseudônimo, rank, XP).
  - "StudentBadges": conquistas persistentes (código, nível, data).

Reversível: o downgrade dropa as 2 tabelas e todas as colunas novas — não há
backfill (flags OFF, colunas nullable/com default).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b9c0d1e2f3a4"
down_revision: Union[str, None] = "a8b9c0d1e2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. StudentInteractions — ledger de XP de Engajamento
    op.add_column("StudentInteractions", sa.Column("xp_awarded", sa.Integer(), nullable=True))
    op.add_column("StudentInteractions", sa.Column("xp_week_id", sa.String(length=20), nullable=True))
    op.add_column(
        "StudentInteractions",
        sa.Column("xp_suspect", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.create_index(
        "idx_interaction_xp_week", "StudentInteractions", ["student_id", "xp_week_id"], unique=False
    )

    # 2. Students — streak de engajamento + escudos + opt-out de ranking
    op.add_column("Students", sa.Column("engagement_streak", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("Students", sa.Column("engagement_streak_best", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("Students", sa.Column("streak_shields", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("Students", sa.Column("streak_last_active_date", sa.Date(), nullable=True))
    op.add_column(
        "Students", sa.Column("ranking_opted_out", sa.Boolean(), nullable=False, server_default="false")
    )

    # 3. Classes — config opt-in de nota (Fase 2; NULL = Modelo A)
    op.add_column("Classes", sa.Column("grade_weight_pct", sa.Numeric(precision=5, scale=2), nullable=True))
    op.add_column("Classes", sa.Column("weekly_xp_goal", sa.Integer(), nullable=True))

    # 4. WeeklyEngagement — snapshot competitivo semanal
    op.create_table(
        "WeeklyEngagement",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("student_id", sa.String(length=255), nullable=False),
        sa.Column("course_id", sa.String(length=255), nullable=False),
        sa.Column("teacher_id", sa.String(length=255), nullable=True),
        sa.Column("week_id", sa.String(length=20), nullable=False),
        sa.Column("cohort_id", sa.String(length=255), nullable=True),
        sa.Column("cohort_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("xp_week", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rank_in_cohort", sa.Integer(), nullable=True),
        sa.Column("pseudonym", sa.String(length=100), nullable=True),
        sa.Column("opted_out_ranking", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.ForeignKeyConstraint(["student_id"], ["Students.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["course_id"], ["Courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["teacher_id"], ["Teachers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("student_id", "course_id", "week_id", name="uq_weekly_engagement_scope"),
    )
    op.create_index(op.f("ix_WeeklyEngagement_student_id"), "WeeklyEngagement", ["student_id"], unique=False)
    op.create_index(op.f("ix_WeeklyEngagement_course_id"), "WeeklyEngagement", ["course_id"], unique=False)
    op.create_index(op.f("ix_WeeklyEngagement_teacher_id"), "WeeklyEngagement", ["teacher_id"], unique=False)
    op.create_index(op.f("ix_WeeklyEngagement_cohort_id"), "WeeklyEngagement", ["cohort_id"], unique=False)
    op.create_index(
        "idx_weekly_engagement_cohort", "WeeklyEngagement", ["course_id", "week_id", "cohort_id"], unique=False
    )

    # 5. StudentBadges — conquistas persistentes
    op.create_table(
        "StudentBadges",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("student_id", sa.String(length=255), nullable=False),
        sa.Column("course_id", sa.String(length=255), nullable=False),
        sa.Column("teacher_id", sa.String(length=255), nullable=True),
        sa.Column("badge_code", sa.String(length=50), nullable=False),
        sa.Column("level", sa.String(length=20), nullable=False, server_default="bronze"),
        sa.Column("unlocked_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("seen_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.ForeignKeyConstraint(["student_id"], ["Students.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["course_id"], ["Courses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["teacher_id"], ["Teachers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("student_id", "course_id", "badge_code", name="uq_student_badge_scope"),
    )
    op.create_index(op.f("ix_StudentBadges_student_id"), "StudentBadges", ["student_id"], unique=False)
    op.create_index(op.f("ix_StudentBadges_course_id"), "StudentBadges", ["course_id"], unique=False)
    op.create_index(op.f("ix_StudentBadges_teacher_id"), "StudentBadges", ["teacher_id"], unique=False)
    op.create_index("idx_student_badge_lookup", "StudentBadges", ["student_id", "course_id"], unique=False)


def downgrade() -> None:
    op.drop_index("idx_student_badge_lookup", table_name="StudentBadges")
    op.drop_index(op.f("ix_StudentBadges_teacher_id"), table_name="StudentBadges")
    op.drop_index(op.f("ix_StudentBadges_course_id"), table_name="StudentBadges")
    op.drop_index(op.f("ix_StudentBadges_student_id"), table_name="StudentBadges")
    op.drop_table("StudentBadges")

    op.drop_index("idx_weekly_engagement_cohort", table_name="WeeklyEngagement")
    op.drop_index(op.f("ix_WeeklyEngagement_cohort_id"), table_name="WeeklyEngagement")
    op.drop_index(op.f("ix_WeeklyEngagement_teacher_id"), table_name="WeeklyEngagement")
    op.drop_index(op.f("ix_WeeklyEngagement_course_id"), table_name="WeeklyEngagement")
    op.drop_index(op.f("ix_WeeklyEngagement_student_id"), table_name="WeeklyEngagement")
    op.drop_table("WeeklyEngagement")

    op.drop_column("Classes", "weekly_xp_goal")
    op.drop_column("Classes", "grade_weight_pct")

    op.drop_column("Students", "ranking_opted_out")
    op.drop_column("Students", "streak_last_active_date")
    op.drop_column("Students", "streak_shields")
    op.drop_column("Students", "engagement_streak_best")
    op.drop_column("Students", "engagement_streak")

    op.drop_index("idx_interaction_xp_week", table_name="StudentInteractions")
    op.drop_column("StudentInteractions", "xp_suspect")
    op.drop_column("StudentInteractions", "xp_week_id")
    op.drop_column("StudentInteractions", "xp_awarded")
