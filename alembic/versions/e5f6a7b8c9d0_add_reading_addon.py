"""Addon de Livros e Apostilas: ReadingPlans, SpacedRepetitionQueue, AnswerKeys,
LessonChapterLinks, Erratas + material_type no QuizBank + reading_plan_id em
PendingQuizzes + active_chapters em Classes.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-11 09:00:00.000000

Resumo do Addon:
  - "ReadingPlans": Jornada de Leitura Guiada por (aluno, curso, disciplina,
    material). current_section = seções CONCLUÍDAS; o avanço acontece quando o
    aluno passa no quiz de check-in (portão), nunca por incremento cego.
  - "SpacedRepetitionQueue": revisão espaçada D+3/D+10/D+30 sobre questões do
    QuizBank (reentrega = cache hit puro, custo $0). FK CASCADE: questão
    invalidada → revisão morre junto.
  - "AnswerKeys": gabarito oficial verificado ("cap3_ex2" → resposta) usado
    como âncora da explicação socrática (anti-alucinação).
  - "LessonChapterLinks": mapeamento Aula ↔ Capítulo (sugestão pós-aula).
  - "Erratas": errata viva injetada nas leituras/respostas da seção.
  - QuizBank.material_type: o banco de questões passa a servir 'lesson',
    'book' e 'workbook' (lesson_number = capítulo/seção nos materiais densos).
  - PendingQuizzes.reading_plan_id: marca o quiz de check-in da leitura.
  - Classes.active_chapters: Modo Professor Curador (JSON de capítulos ativos).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. ReadingPlans — Jornada de Leitura Guiada
    op.create_table(
        "ReadingPlans",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("student_id", sa.String(length=255), nullable=False),
        sa.Column("course_id", sa.String(length=255), nullable=False),
        sa.Column("subject_id", sa.String(length=255), nullable=True),
        sa.Column("material_type", sa.String(length=50), nullable=False, server_default="book"),
        sa.Column("total_sections", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("current_section", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("pace", sa.String(length=50), nullable=False, server_default="daily"),
        sa.Column("preferred_time", sa.String(length=5), nullable=False, server_default="07:00"),
        sa.Column("streak_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_read_at", sa.DateTime(), nullable=True),
        sa.Column("is_paused", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("next_trigger_at", sa.DateTime(), nullable=True),
        sa.Column("last_suggestion_at", sa.DateTime(), nullable=True),
        sa.Column("next_checkin_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.ForeignKeyConstraint(["student_id"], ["Students.id"]),
        sa.ForeignKeyConstraint(["course_id"], ["Courses.id"]),
        sa.ForeignKeyConstraint(["subject_id"], ["Subjects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("student_id", "course_id", "subject_id", "material_type",
                            name="uq_reading_plan_scope"),
    )
    op.create_index(op.f("ix_ReadingPlans_student_id"), "ReadingPlans", ["student_id"], unique=False)
    op.create_index(op.f("ix_ReadingPlans_course_id"), "ReadingPlans", ["course_id"], unique=False)
    op.create_index(op.f("ix_ReadingPlans_subject_id"), "ReadingPlans", ["subject_id"], unique=False)
    op.create_index("idx_reading_plan_trigger", "ReadingPlans", ["next_trigger_at", "is_paused"], unique=False)
    op.create_index("idx_reading_plan_checkin", "ReadingPlans", ["next_checkin_at", "is_paused"], unique=False)

    # 2. SpacedRepetitionQueue — Revisão Espaçada (D+3, D+10, D+30)
    op.create_table(
        "SpacedRepetitionQueue",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("student_id", sa.String(length=255), nullable=False),
        sa.Column("quiz_bank_id", sa.String(length=255), nullable=False),
        sa.Column("next_review_at", sa.DateTime(), nullable=False),
        sa.Column("interval_days", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("repetition_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("ease_factor", sa.Float(), nullable=False, server_default="2.5"),
        sa.Column("status", sa.String(length=50), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.ForeignKeyConstraint(["student_id"], ["Students.id"]),
        sa.ForeignKeyConstraint(["quiz_bank_id"], ["QuizBank.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("student_id", "quiz_bank_id", name="uq_srq_student_quiz"),
    )
    op.create_index(op.f("ix_SpacedRepetitionQueue_student_id"), "SpacedRepetitionQueue", ["student_id"], unique=False)
    op.create_index(op.f("ix_SpacedRepetitionQueue_quiz_bank_id"), "SpacedRepetitionQueue", ["quiz_bank_id"], unique=False)
    op.create_index("idx_srq_due", "SpacedRepetitionQueue", ["next_review_at", "status"], unique=False)

    # 3. AnswerKeys — Gabarito Verificado
    op.create_table(
        "AnswerKeys",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("course_id", sa.String(length=255), nullable=False),
        sa.Column("subject_id", sa.String(length=255), nullable=True),
        sa.Column("material_type", sa.String(length=50), nullable=False, server_default="book"),
        sa.Column("section_ref", sa.String(length=100), nullable=False),
        sa.Column("correct_answer", sa.Text(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.ForeignKeyConstraint(["course_id"], ["Courses.id"]),
        sa.ForeignKeyConstraint(["subject_id"], ["Subjects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_AnswerKeys_course_id"), "AnswerKeys", ["course_id"], unique=False)
    op.create_index(op.f("ix_AnswerKeys_subject_id"), "AnswerKeys", ["subject_id"], unique=False)
    op.create_index("idx_answer_key_lookup", "AnswerKeys", ["course_id", "subject_id", "section_ref"], unique=False)

    # 4. LessonChapterLinks — Mapeamento Aula ↔ Capítulo
    op.create_table(
        "LessonChapterLinks",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("course_id", sa.String(length=255), nullable=False),
        sa.Column("subject_id", sa.String(length=255), nullable=True),
        sa.Column("lesson_number", sa.Integer(), nullable=False),
        sa.Column("chapter_number", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.ForeignKeyConstraint(["course_id"], ["Courses.id"]),
        sa.ForeignKeyConstraint(["subject_id"], ["Subjects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("course_id", "subject_id", "lesson_number", name="uq_lesson_chapter_link"),
    )
    op.create_index(op.f("ix_LessonChapterLinks_course_id"), "LessonChapterLinks", ["course_id"], unique=False)
    op.create_index(op.f("ix_LessonChapterLinks_subject_id"), "LessonChapterLinks", ["subject_id"], unique=False)

    # 5. Erratas — Errata Viva
    op.create_table(
        "Erratas",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("course_id", sa.String(length=255), nullable=False),
        sa.Column("subject_id", sa.String(length=255), nullable=True),
        sa.Column("material_type", sa.String(length=50), nullable=False, server_default="book"),
        sa.Column("section_ref", sa.String(length=100), nullable=False),
        sa.Column("section_number", sa.Integer(), nullable=True),
        sa.Column("correction", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.ForeignKeyConstraint(["course_id"], ["Courses.id"]),
        sa.ForeignKeyConstraint(["subject_id"], ["Subjects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_Erratas_course_id"), "Erratas", ["course_id"], unique=False)
    op.create_index(op.f("ix_Erratas_subject_id"), "Erratas", ["subject_id"], unique=False)
    op.create_index("idx_errata_lookup", "Erratas", ["course_id", "subject_id", "section_number"], unique=False)

    # 6. QuizBank.material_type — o banco de questões serve livros e apostilas
    with op.batch_alter_table("QuizBank") as batch_op:
        batch_op.add_column(
            sa.Column("material_type", sa.String(length=50), nullable=False, server_default="lesson")
        )

    # 7. PendingQuizzes.reading_plan_id — quiz de check-in da leitura
    with op.batch_alter_table("PendingQuizzes") as batch_op:
        batch_op.add_column(sa.Column("reading_plan_id", sa.String(length=255), nullable=True))
        batch_op.create_index("ix_PendingQuizzes_reading_plan_id", ["reading_plan_id"])
        batch_op.create_foreign_key(
            "fk_PendingQuizzes_reading_plan_id_ReadingPlans",
            "ReadingPlans",
            ["reading_plan_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # 8. Classes.active_chapters — Modo Professor Curador
    with op.batch_alter_table("Classes") as batch_op:
        batch_op.add_column(sa.Column("active_chapters", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("Classes") as batch_op:
        batch_op.drop_column("active_chapters")

    with op.batch_alter_table("PendingQuizzes") as batch_op:
        batch_op.drop_constraint("fk_PendingQuizzes_reading_plan_id_ReadingPlans", type_="foreignkey")
        batch_op.drop_index("ix_PendingQuizzes_reading_plan_id")
        batch_op.drop_column("reading_plan_id")

    with op.batch_alter_table("QuizBank") as batch_op:
        batch_op.drop_column("material_type")

    op.drop_index("idx_errata_lookup", table_name="Erratas")
    op.drop_index(op.f("ix_Erratas_subject_id"), table_name="Erratas")
    op.drop_index(op.f("ix_Erratas_course_id"), table_name="Erratas")
    op.drop_table("Erratas")

    op.drop_index(op.f("ix_LessonChapterLinks_subject_id"), table_name="LessonChapterLinks")
    op.drop_index(op.f("ix_LessonChapterLinks_course_id"), table_name="LessonChapterLinks")
    op.drop_table("LessonChapterLinks")

    op.drop_index("idx_answer_key_lookup", table_name="AnswerKeys")
    op.drop_index(op.f("ix_AnswerKeys_subject_id"), table_name="AnswerKeys")
    op.drop_index(op.f("ix_AnswerKeys_course_id"), table_name="AnswerKeys")
    op.drop_table("AnswerKeys")

    op.drop_index("idx_srq_due", table_name="SpacedRepetitionQueue")
    op.drop_index(op.f("ix_SpacedRepetitionQueue_quiz_bank_id"), table_name="SpacedRepetitionQueue")
    op.drop_index(op.f("ix_SpacedRepetitionQueue_student_id"), table_name="SpacedRepetitionQueue")
    op.drop_table("SpacedRepetitionQueue")

    op.drop_index("idx_reading_plan_checkin", table_name="ReadingPlans")
    op.drop_index("idx_reading_plan_trigger", table_name="ReadingPlans")
    op.drop_index(op.f("ix_ReadingPlans_subject_id"), table_name="ReadingPlans")
    op.drop_index(op.f("ix_ReadingPlans_course_id"), table_name="ReadingPlans")
    op.drop_index(op.f("ix_ReadingPlans_student_id"), table_name="ReadingPlans")
    op.drop_table("ReadingPlans")
