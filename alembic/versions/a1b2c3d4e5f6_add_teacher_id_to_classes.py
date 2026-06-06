"""add teacher_id to Classes (posse por turma)

Revision ID: a1b2c3d4e5f6
Revises: cc02304a0711
Create Date: 2026-06-05 23:30:00.000000

A posse de turma deixou de ser apenas por curso (Courses.teacher_id) e passou a
ser por turma (Classes.teacher_id), pois um mesmo curso pode ter turmas de
professores diferentes (vínculo vem da aba "Professores": Curso + Turma).

O backfill copia o dono do curso para cada turma existente, garantindo que
nenhuma turma fique sem professor logo após a migration (o que quebraria o
join de contexto do aluno). A sync depois refina a posse por turma.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "cc02304a0711"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("Classes") as batch_op:
        batch_op.add_column(sa.Column("teacher_id", sa.String(length=255), nullable=True))
        batch_op.create_index("ix_Classes_teacher_id", ["teacher_id"])
        batch_op.create_foreign_key(
            "fk_Classes_teacher_id_Teachers", "Teachers", ["teacher_id"], ["id"]
        )

    # Backfill: cada turma herda o professor do seu curso.
    op.execute(
        'UPDATE "Classes" SET teacher_id = '
        '(SELECT teacher_id FROM "Courses" WHERE "Courses".id = "Classes".course_id) '
        "WHERE teacher_id IS NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("Classes") as batch_op:
        batch_op.drop_constraint("fk_Classes_teacher_id_Teachers", type_="foreignkey")
        batch_op.drop_index("ix_Classes_teacher_id")
        batch_op.drop_column("teacher_id")
