"""add QuizBank (cache semantico) e PendingQuizzes.quiz_bank_id

Revision ID: d4e5f6a7b8c9
Revises: 6e4e71a0042c
Create Date: 2026-06-10 09:00:00.000000

Banco de Questões Emergente (Cache Semântico "Lazy"):
  - Nova tabela "QuizBank": pacotes pedagógicos (resumo + pontos-chave + quiz)
    gerados pelo LLM, escopados por (course_id, subject_id, lesson_number) e
    indexados pela dupla de variação (topic + pedagogical_lens).
  - "PendingQuizzes".quiz_bank_id (FK → QuizBank, ON DELETE SET NULL): cada
    entrega de quiz a um aluno passa a referenciar a questão de origem. É a
    "tabela de junção" que (1) impede a repetição da mesma dupla Tópico+Lente
    para o mesmo aluno e (2) alimenta o comando /acertos (agregação por topic).

ON DELETE SET NULL é proposital: /limpar_quizzes e a invalidação automática do
ingestion_worker deletam FISICAMENTE o QuizBank, mas o histórico de respostas
sobrevive (PendingQuizzes denormaliza a questão completa).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "6e4e71a0042c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Tabela QuizBank (Banco de Questões / Cache Semântico)
    op.create_table(
        "QuizBank",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("course_id", sa.String(length=255), nullable=False),
        sa.Column("subject_id", sa.String(length=255), nullable=True),
        sa.Column("lesson_number", sa.Integer(), nullable=False),
        sa.Column("topic", sa.String(length=500), nullable=False),
        sa.Column("pedagogical_lens", sa.String(length=500), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("alternatives", sa.Text(), nullable=False),
        sa.Column("correct_answer", sa.String(length=255), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("key_points", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("(CURRENT_TIMESTAMP)"), nullable=True),
        sa.ForeignKeyConstraint(["course_id"], ["Courses.id"]),
        sa.ForeignKeyConstraint(["subject_id"], ["Subjects.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_QuizBank_course_id"), "QuizBank", ["course_id"], unique=False)
    op.create_index(op.f("ix_QuizBank_subject_id"), "QuizBank", ["subject_id"], unique=False)
    op.create_index(op.f("ix_QuizBank_topic"), "QuizBank", ["topic"], unique=False)
    # Índice composto que cobre o lookup exato do Cache Hit
    op.create_index(
        "idx_quizbank_scope",
        "QuizBank",
        ["course_id", "subject_id", "lesson_number", "topic"],
        unique=False,
    )

    # 2. PendingQuizzes.quiz_bank_id (junção de consumo por aluno)
    with op.batch_alter_table("PendingQuizzes") as batch_op:
        batch_op.add_column(sa.Column("quiz_bank_id", sa.String(length=255), nullable=True))
        batch_op.create_index("ix_PendingQuizzes_quiz_bank_id", ["quiz_bank_id"])
        batch_op.create_foreign_key(
            "fk_PendingQuizzes_quiz_bank_id_QuizBank",
            "QuizBank",
            ["quiz_bank_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("PendingQuizzes") as batch_op:
        batch_op.drop_constraint("fk_PendingQuizzes_quiz_bank_id_QuizBank", type_="foreignkey")
        batch_op.drop_index("ix_PendingQuizzes_quiz_bank_id")
        batch_op.drop_column("quiz_bank_id")

    op.drop_index("idx_quizbank_scope", table_name="QuizBank")
    op.drop_index(op.f("ix_QuizBank_topic"), table_name="QuizBank")
    op.drop_index(op.f("ix_QuizBank_subject_id"), table_name="QuizBank")
    op.drop_index(op.f("ix_QuizBank_course_id"), table_name="QuizBank")
    op.drop_table("QuizBank")
