"""add Subjects, Classes.subject_id and StudentSchedule.class_id (camada Disciplina)

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-06-06 10:30:00.000000

Insere a camada "Disciplina" (Subject) entre Curso e Turma:
  - Nova tabela "Subjects" (course_id + name únicos).
  - "Classes".subject_id (FK→Subjects, nullable p/ legado/transição).
  - "StudentSchedule": FK secundário muda de course_id → class_id, para que o
    agendamento aponte a Disciplina/Turma exata daquele horário.

Backfill do class_id: cada agendamento herda a turma da matrícula do aluno no
mesmo curso. Agendamentos órfãos (sem matrícula correspondente) são removidos —
serão recriados pela próxima sincronização da planilha (/sinc_alunos).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Tabela Subjects (Disciplina)
    op.create_table(
        "Subjects",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("course_id", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.ForeignKeyConstraint(["course_id"], ["Courses.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("course_id", "name", name="uq_subject_course_name"),
    )
    op.create_index(op.f("ix_Subjects_course_id"), "Subjects", ["course_id"], unique=False)

    # 2. Classes.subject_id
    with op.batch_alter_table("Classes") as batch_op:
        batch_op.add_column(sa.Column("subject_id", sa.String(length=255), nullable=True))
        batch_op.create_index("ix_Classes_subject_id", ["subject_id"])
        batch_op.create_foreign_key(
            "fk_Classes_subject_id_Subjects", "Subjects", ["subject_id"], ["id"]
        )

    # 3. StudentSchedule: adiciona class_id e faz backfill (course_id ainda existe)
    with op.batch_alter_table("StudentSchedule") as batch_op:
        batch_op.add_column(sa.Column("class_id", sa.String(length=255), nullable=True))

    op.execute(
        'UPDATE "StudentSchedule" SET class_id = ('
        '  SELECT se.class_id FROM "StudentEnrollment" se'
        '  JOIN "Classes" cl ON cl.id = se.class_id'
        '  WHERE se.student_id = "StudentSchedule".student_id'
        '    AND cl.course_id = "StudentSchedule".course_id'
        "  LIMIT 1"
        ") WHERE class_id IS NULL"
    )
    # Órfãos sem matrícula correspondente: serão recriados pela sync.
    op.execute('DELETE FROM "StudentSchedule" WHERE class_id IS NULL')

    # 4. Troca a constraint/FK secundária de course_id para class_id
    with op.batch_alter_table("StudentSchedule") as batch_op:
        batch_op.drop_constraint("uq_student_course_content_hour", type_="unique")
        batch_op.drop_column("course_id")
        batch_op.alter_column("class_id", existing_type=sa.String(length=255), nullable=False)
        batch_op.create_foreign_key(
            "fk_StudentSchedule_class_id_Classes", "Classes", ["class_id"], ["id"]
        )
        batch_op.create_unique_constraint(
            "uq_student_class_content_hour",
            ["student_id", "class_id", "content_type", "preferred_hour"],
        )


def downgrade() -> None:
    # Reverte StudentSchedule: class_id → course_id (backfill via matrícula)
    with op.batch_alter_table("StudentSchedule") as batch_op:
        batch_op.add_column(sa.Column("course_id", sa.String(length=255), nullable=True))

    op.execute(
        'UPDATE "StudentSchedule" SET course_id = ('
        '  SELECT cl.course_id FROM "Classes" cl'
        '  WHERE cl.id = "StudentSchedule".class_id'
        "  LIMIT 1"
        ") WHERE course_id IS NULL"
    )
    op.execute('DELETE FROM "StudentSchedule" WHERE course_id IS NULL')

    with op.batch_alter_table("StudentSchedule") as batch_op:
        batch_op.drop_constraint("uq_student_class_content_hour", type_="unique")
        # A FK de class_id cai junto com a coluna (SQLite recria a tabela; o
        # Postgres dropa a constraint ao remover a coluna que ela referencia).
        batch_op.drop_column("class_id")
        batch_op.alter_column("course_id", existing_type=sa.String(length=255), nullable=False)
        batch_op.create_foreign_key(
            "fk_StudentSchedule_course_id_Courses", "Courses", ["course_id"], ["id"]
        )
        batch_op.create_unique_constraint(
            "uq_student_course_content_hour",
            ["student_id", "course_id", "content_type", "preferred_hour"],
        )

    with op.batch_alter_table("Classes") as batch_op:
        batch_op.drop_index("ix_Classes_subject_id")
        # A FK de subject_id cai junto com a coluna (mesmo motivo do class_id acima).
        batch_op.drop_column("subject_id")

    op.drop_index(op.f("ix_Subjects_course_id"), table_name="Subjects")
    op.drop_table("Subjects")
