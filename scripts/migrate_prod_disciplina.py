"""
Migração IDEMPOTENTE da camada Disciplina para produção.

Contexto: produção bootstrapa o schema via create_all (init_db) e o alembic_version
ficou travado numa revisão antiga. O create_all cria a tabela nova `Subjects`, mas
NÃO adiciona colunas novas a tabelas existentes (Classes.subject_id, StudentSchedule.class_id).
Este script aplica essas mudanças de forma idempotente — pode rodar quantas vezes quiser.

Uso (dentro do container de prod, após o deploy):
    python scripts/migrate_prod_disciplina.py

Cada statement roda isolado (commit/rollback próprio). Construções idempotentes:
  - CREATE TABLE / INDEX IF NOT EXISTS
  - ADD COLUMN IF NOT EXISTS
  - DROP CONSTRAINT IF EXISTS
  - ADD CONSTRAINT em DO-block que ignora duplicate_object / undefined_column
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402
from src.db.database import AsyncSessionLocal  # noqa: E402

STATEMENTS = [
    ("Subjects table", """
        CREATE TABLE IF NOT EXISTS "Subjects" (
            id VARCHAR(255) PRIMARY KEY,
            course_id VARCHAR(255) NOT NULL REFERENCES "Courses"(id),
            name VARCHAR(255) NOT NULL,
            is_active BOOLEAN,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_subject_course_name UNIQUE (course_id, name)
        )
    """),
    ("ix_Subjects_course_id", '''
        CREATE INDEX IF NOT EXISTS "ix_Subjects_course_id" ON "Subjects" (course_id)
    '''),

    # --- Classes.subject_id ---
    ("Classes.subject_id column", '''
        ALTER TABLE "Classes" ADD COLUMN IF NOT EXISTS subject_id VARCHAR(255)
    '''),
    ("Classes.subject_id FK", '''
        DO $$ BEGIN
            ALTER TABLE "Classes" ADD CONSTRAINT fk_Classes_subject_id_Subjects
                FOREIGN KEY (subject_id) REFERENCES "Subjects"(id);
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    '''),
    ("ix_Classes_subject_id", '''
        CREATE INDEX IF NOT EXISTS "ix_Classes_subject_id" ON "Classes" (subject_id)
    '''),

    # --- StudentSchedule.class_id (substitui o course_id) ---
    ("StudentSchedule.class_id column", '''
        ALTER TABLE "StudentSchedule" ADD COLUMN IF NOT EXISTS class_id VARCHAR(255)
    '''),
    ("StudentSchedule.class_id FK", '''
        DO $$ BEGIN
            ALTER TABLE "StudentSchedule" ADD CONSTRAINT fk_StudentSchedule_class_id_Classes
                FOREIGN KEY (class_id) REFERENCES "Classes"(id);
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    '''),
    ("backfill class_id via matrícula", '''
        UPDATE "StudentSchedule" ss SET class_id = (
            SELECT se.class_id FROM "StudentEnrollment" se
            JOIN "Classes" cl ON cl.id = se.class_id
            WHERE se.student_id = ss.student_id AND cl.course_id = ss.course_id
            LIMIT 1
        ) WHERE ss.class_id IS NULL AND ss.course_id IS NOT NULL
    '''),
    ("course_id nullable (vestigial)", '''
        DO $$ BEGIN
            ALTER TABLE "StudentSchedule" ALTER COLUMN course_id DROP NOT NULL;
        EXCEPTION WHEN undefined_column THEN NULL; END $$
    '''),
    ("drop unique antigo (course)", '''
        ALTER TABLE "StudentSchedule" DROP CONSTRAINT IF EXISTS uq_student_course_content_hour
    '''),
    ("add unique novo (class)", '''
        DO $$ BEGIN
            ALTER TABLE "StudentSchedule" ADD CONSTRAINT uq_student_class_content_hour
                UNIQUE (student_id, class_id, content_type, preferred_hour);
        EXCEPTION WHEN duplicate_object THEN NULL; END $$
    '''),
]


async def run() -> None:
    ok = skip = 0
    async with AsyncSessionLocal() as s:
        for desc, sql in STATEMENTS:
            try:
                await s.execute(text(sql))
                await s.commit()
                ok += 1
                print(f"OK   {desc}")
            except Exception as e:  # idempotência: statement já aplicado / inaplicável
                await s.rollback()
                skip += 1
                print(f"SKIP {desc}: {str(e)[:120]}")
    print(f"migrate_prod_disciplina: done (ok={ok}, skip={skip})")


if __name__ == "__main__":
    asyncio.run(run())
