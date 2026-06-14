"""keyactivation idempotency — chave não consumida 2x pelo mesmo titular

Anti-duplo-consumo (revisão §13.9): o mesmo número (Student) ou professor
(Teacher) não pode consumir a mesma ActivationKey duas vezes. Índices únicos
PARCIAIS em KeyActivations garantem isso no banco (e blindam contra corrida
entre pods); a lógica de `_consume_key` usa ON CONFLICT DO NOTHING sobre eles.

Dedupe defensivo antes de criar os índices: se já houver duplicatas em
produção (geradas pelo bug antigo), mantém a ativação mais antiga por titular.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-06-13
"""
from alembic import op

revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Dedupe defensivo (mantém a 1ª ativação por (key, titular); corrige o
    #    activation_count das chaves afetadas).
    op.execute('''
        DELETE FROM "KeyActivations" ka
        USING "KeyActivations" keep
        WHERE ka.student_id IS NOT NULL
          AND ka.key = keep.key
          AND ka.student_id = keep.student_id
          AND ka.created_at > keep.created_at
    ''')
    op.execute('''
        DELETE FROM "KeyActivations" ka
        USING "KeyActivations" keep
        WHERE ka.teacher_id IS NOT NULL
          AND ka.key = keep.key
          AND ka.teacher_id = keep.teacher_id
          AND ka.created_at > keep.created_at
    ''')
    op.execute('''
        UPDATE "ActivationKeys" ak
        SET activation_count = sub.cnt
        FROM (SELECT key, COUNT(*) AS cnt FROM "KeyActivations" GROUP BY key) sub
        WHERE ak.key = sub.key AND ak.activation_count <> sub.cnt
    ''')

    # 2. Índices únicos parciais (um titular consome a chave no máx. 1x).
    op.execute('''
        CREATE UNIQUE INDEX uq_keyactivation_student
        ON "KeyActivations" (key, student_id) WHERE student_id IS NOT NULL
    ''')
    op.execute('''
        CREATE UNIQUE INDEX uq_keyactivation_teacher
        ON "KeyActivations" (key, teacher_id) WHERE teacher_id IS NOT NULL
    ''')


def downgrade() -> None:
    op.drop_index("uq_keyactivation_teacher", table_name="KeyActivations")
    op.drop_index("uq_keyactivation_student", table_name="KeyActivations")
