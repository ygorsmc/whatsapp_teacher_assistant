"""
Quiz Bank — camada de acesso a dados do Banco de Questões Emergente.

Implementa o Cache Semântico "Lazy" dos quizzes gerados pelo LLM:
  - `find_unseen_quiz`     → Cache Hit: questão pronta que o aluno ainda não viu.
  - `get_used_lenses`      → Cache Miss: quais Lentes Pedagógicas já existem no
                             banco para o tópico (para sortear uma INÉDITA).
  - `save_quiz_package`    → persiste o pacote (resumo + pontos-chave + quiz).
  - `delete_lesson_quizzes`→ invalidação automática (ingestion_worker) quando o
                             material de uma aula é atualizado no Google Drive.
  - `delete_scope_quizzes` → invalidação manual (/limpar_quizzes_[turma]).

Regra de unicidade por aluno: uma questão do banco é considerada "consumida"
pelo aluno assim que é ENTREGUE (existe uma linha em PendingQuizzes com o
quiz_bank_id, em qualquer status). Filtrar por entrega — e não apenas por
resposta — é estritamente mais forte que "ainda não respondeu" e evita o caso
degenerado do aluno receber duas vezes o mesmo quiz pendente.
"""

import json
from typing import List, Optional
from uuid import uuid4

import structlog
from sqlalchemy import text

from src.db.database import AsyncSessionLocal

logger = structlog.get_logger(__name__)


def _subject_filter(subject_id: Optional[str]) -> tuple[str, dict]:
    """Cláusula de isolamento por Disciplina.

    Turmas legadas sem Subject geram linhas com subject_id NULL — o filtro
    precisa ser explícito (`IS NULL`) para não vazar questões entre disciplinas
    do mesmo curso.
    """
    if subject_id:
        return "qb.subject_id = :sub_id", {"sub_id": subject_id}
    return "qb.subject_id IS NULL", {}


async def resolve_subject_id(course_id: str, subject_name: Optional[str]) -> Optional[str]:
    """Resolve o id da Disciplina a partir de (curso, nome). None se não existir."""
    if not subject_name:
        return None
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text('SELECT id FROM "Subjects" WHERE course_id = :cid AND name = :name LIMIT 1'),
                {"cid": course_id, "name": subject_name},
            )
            return result.scalar_one_or_none()
    except Exception as e:
        logger.warning("quiz_bank_resolve_subject_failed", error=str(e)[:200])
        return None


async def find_unseen_quiz(
    student_id: str,
    course_id: str,
    subject_id: Optional[str],
    lesson_number: int,
    topic: str,
    material_type: str = "lesson",
) -> Optional[dict]:
    """CACHE HIT: busca no QuizBank uma questão do tópico que o aluno ainda não viu.

    Equivale à query do plano:
        SELECT * FROM QuizBank
        WHERE topic = :t
          AND id NOT IN (SELECT quiz_bank_id FROM <histórico> WHERE student_id = :s)

    (implementada com NOT EXISTS, que é null-safe e usa o índice
    ix_PendingQuizzes_quiz_bank_id). Retorna a questão mais antiga ainda não
    vista — assim a turma "varre" o banco em ordem antes de forçar novas
    gerações. Retorna dict ou None (Cache Miss).
    """
    subject_clause, subject_params = _subject_filter(subject_id)
    sql = f"""
        SELECT qb.id, qb.topic, qb.pedagogical_lens, qb.question_text,
               qb.alternatives, qb.correct_answer, qb.explanation,
               qb.summary, qb.key_points, qb.audio_script
        FROM "QuizBank" qb
        WHERE qb.course_id = :cid
          AND {subject_clause}
          AND qb.lesson_number = :lesson
          AND qb.material_type = :mat
          AND qb.topic = :topic
          AND NOT EXISTS (
                SELECT 1 FROM "PendingQuizzes" pq
                WHERE pq.quiz_bank_id = qb.id
                  AND pq.student_id = :stid
          )
        ORDER BY qb.created_at ASC
        LIMIT 1
    """
    params = {"cid": course_id, "lesson": lesson_number, "topic": topic, "stid": student_id, "mat": material_type}
    params.update(subject_params)

    async with AsyncSessionLocal() as session:
        result = await session.execute(text(sql), params)
        row = result.mappings().first()
        return dict(row) if row else None


async def get_used_lenses(
    course_id: str,
    subject_id: Optional[str],
    lesson_number: int,
    topic: str,
    material_type: str = "lesson",
) -> List[str]:
    """CACHE MISS: lista as Lentes Pedagógicas já materializadas no banco para o
    tópico. O orquestrador sorteia uma lente FORA desta lista, garantindo que o
    banco cresça com variações inéditas (Tópico 1 + Lente A, depois + Lente B...).
    """
    subject_clause, subject_params = _subject_filter(subject_id)
    sql = f"""
        SELECT DISTINCT qb.pedagogical_lens
        FROM "QuizBank" qb
        WHERE qb.course_id = :cid
          AND {subject_clause}
          AND qb.lesson_number = :lesson
          AND qb.material_type = :mat
          AND qb.topic = :topic
    """
    params = {"cid": course_id, "lesson": lesson_number, "topic": topic, "mat": material_type}
    params.update(subject_params)

    async with AsyncSessionLocal() as session:
        result = await session.execute(text(sql), params)
        return [r[0] for r in result.fetchall()]


async def save_quiz_package(
    course_id: str,
    subject_id: Optional[str],
    lesson_number: int,
    topic: str,
    pedagogical_lens: str,
    question_text: str,
    alternatives: List[dict],
    correct_answer: str,
    explanation: str,
    summary: Optional[str] = None,
    key_points: Optional[List[str]] = None,
    material_type: str = "lesson",
    audio_script: Optional[str] = None,
) -> str:
    """Persiste o pacote pedagógico recém-gerado pelo LLM. Retorna o novo id."""
    bank_id = str(uuid4())
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""
                INSERT INTO "QuizBank"
                    (id, course_id, subject_id, lesson_number, material_type,
                     topic, pedagogical_lens,
                     question_text, alternatives, correct_answer, explanation,
                     summary, key_points, audio_script)
                VALUES
                    (:id, :cid, :sub_id, :lesson, :mat,
                     :topic, :lens,
                     :question, :alternatives, :correct, :explanation,
                     :summary, :key_points, :audio_script)
            """),
            {
                "id": bank_id,
                "cid": course_id,
                "sub_id": subject_id,
                "lesson": lesson_number,
                "mat": material_type,
                "topic": topic,
                "lens": pedagogical_lens,
                "question": question_text,
                "alternatives": json.dumps(alternatives, ensure_ascii=False),
                "correct": correct_answer,
                "explanation": explanation,
                "summary": summary,
                "key_points": json.dumps(key_points or [], ensure_ascii=False),
                "audio_script": audio_script,
            },
        )
        await session.commit()

    logger.info(
        "quiz_bank_package_saved",
        quiz_bank_id=bank_id,
        course_id=course_id,
        subject_id=subject_id,
        lesson_number=lesson_number,
        material_type=material_type,
        topic=topic[:60],
    )
    return bank_id


async def delete_lesson_quizzes(
    course_id: str,
    lesson_number: int,
    subject_id: Optional[str] = None,
    subject_name: Optional[str] = None,
    material_type: str = "lesson",
) -> int:
    """Invalidação automática do ciclo de vida (chamada pelo ingestion_worker).

    Executa o `DELETE FROM QuizBank WHERE course_id = X AND lesson_number = Y`
    (escopado também pela Disciplina E pelo material_type — atualizar o Cap. 3
    do livro não pode apagar os quizzes da Aula 3). Se `subject_id` não vier,
    resolve pelo nome. Retorna o número de linhas apagadas.
    """
    if subject_id is None and subject_name:
        subject_id = await resolve_subject_id(course_id, subject_name)

    subject_clause, subject_params = _subject_filter(subject_id)
    sql = f"""
        DELETE FROM "QuizBank" qb
        WHERE qb.course_id = :cid
          AND {subject_clause}
          AND qb.lesson_number = :lesson
          AND qb.material_type = :mat
    """
    params = {"cid": course_id, "lesson": lesson_number, "mat": material_type}
    params.update(subject_params)

    async with AsyncSessionLocal() as session:
        result = await session.execute(text(sql), params)
        await session.commit()
        deleted = result.rowcount or 0  # type: ignore[attr-defined]  # CursorResult tem rowcount

    logger.info(
        "quiz_bank_lesson_invalidated",
        course_id=course_id,
        subject_id=subject_id,
        lesson_number=lesson_number,
        deleted=deleted,
    )
    return deleted


async def get_student_topic_stats(
    student_id: str,
    course_id: str,
    subject_id: Optional[str],
    lesson_number: int,
    material_type: str = "lesson",
) -> dict[str, tuple[int, int]]:
    """H3.2 — matriz aluno × tópico: {topic: (acertos, total)} nas respostas do
    aluno para esta aula/seção. Alimenta a priorização de tópicos fracos na
    rotação do review_generator."""
    subject_clause, subject_params = _subject_filter(subject_id)
    sql = f"""
        SELECT qb.topic,
               SUM(CASE WHEN pq.is_correct THEN 1 ELSE 0 END) AS corrects,
               COUNT(*) AS total
        FROM "PendingQuizzes" pq
        JOIN "QuizBank" qb ON qb.id = pq.quiz_bank_id
        WHERE pq.student_id = :stid
          AND pq.status = 'answered'
          AND qb.course_id = :cid
          AND {subject_clause}
          AND qb.lesson_number = :lesson
          AND qb.material_type = :mat
        GROUP BY qb.topic
    """
    params = {"stid": student_id, "cid": course_id, "lesson": lesson_number, "mat": material_type}
    params.update(subject_params)

    async with AsyncSessionLocal() as session:
        result = await session.execute(text(sql), params)
        return {r[0]: (int(r[1] or 0), int(r[2] or 0)) for r in result.fetchall()}


async def get_quiz_package(quiz_bank_id: str) -> Optional[dict]:
    """Busca uma questão do banco pelo id (Revisão Espaçada: reentrega é um
    cache hit puro — zero LLM, zero RAG). Retorna dict ou None se a questão
    tiver sido invalidada nesse meio-tempo."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            text("""
                SELECT qb.id, qb.course_id, qb.subject_id, qb.lesson_number,
                       qb.material_type, qb.topic, qb.pedagogical_lens,
                       qb.question_text, qb.alternatives, qb.correct_answer,
                       qb.explanation, qb.summary, qb.key_points, qb.audio_script
                FROM "QuizBank" qb
                WHERE qb.id = :qbid
            """),
            {"qbid": quiz_bank_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None


async def list_simulado_questions(
    course_id: str,
    subject_id: Optional[str],
    max_section: int,
    limit: int = 5,
) -> List[dict]:
    """Monta o pool do /simulado: questões do QuizBank cruzando capítulos e
    tópicos de 1 até `max_section` (livros/apostilas E aulas — para turmas que
    ainda não têm material denso indexado, o simulado continua útil).

    DISTINCT ON (lesson_number, topic): espalha o simulado por
    capítulos/tópicos diferentes em vez de concentrar tudo numa seção.
    """
    subject_clause, subject_params = _subject_filter(subject_id)
    sql = f"""
        SELECT DISTINCT ON (qb.lesson_number, qb.topic)
               qb.id, qb.lesson_number, qb.material_type, qb.topic,
               qb.question_text, qb.alternatives, qb.correct_answer,
               qb.explanation, qb.summary, qb.key_points
        FROM "QuizBank" qb
        WHERE qb.course_id = :cid
          AND {subject_clause}
          AND qb.lesson_number <= :max_section
        ORDER BY qb.lesson_number, qb.topic, qb.created_at DESC
        LIMIT :limit
    """
    params = {"cid": course_id, "max_section": max_section, "limit": limit}
    params.update(subject_params)

    async with AsyncSessionLocal() as session:
        result = await session.execute(text(sql), params)
        return [dict(r) for r in result.mappings().all()]


async def delete_scope_quizzes(course_id: str, subject_id: Optional[str]) -> int:
    """Invalidação manual (/limpar_quizzes): apaga FISICAMENTE todas as questões
    cacheadas do escopo (curso + disciplina da turma), em TODAS as aulas,
    forçando o LLM a gerar tudo do zero. Retorna o número de linhas apagadas.

    O histórico de respostas dos alunos sobrevive: PendingQuizzes.quiz_bank_id
    tem ON DELETE SET NULL e a questão está denormalizada lá.
    """
    subject_clause, subject_params = _subject_filter(subject_id)
    sql = f"""
        DELETE FROM "QuizBank" qb
        WHERE qb.course_id = :cid
          AND {subject_clause}
    """
    params = {"cid": course_id}
    params.update(subject_params)

    async with AsyncSessionLocal() as session:
        result = await session.execute(text(sql), params)
        await session.commit()
        deleted = result.rowcount or 0  # type: ignore[attr-defined]  # CursorResult tem rowcount

    logger.info(
        "quiz_bank_scope_cleared",
        course_id=course_id,
        subject_id=subject_id,
        deleted=deleted,
    )
    return deleted
