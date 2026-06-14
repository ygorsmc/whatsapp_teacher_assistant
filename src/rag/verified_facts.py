"""
Fatos verificados primeiro (F6.3) — gabaritos e erratas no fluxo de AULAS.

O Addon de Livros já injeta AnswerKeys/Erratas nas perguntas sobre o material
denso (`build_reading_rag_addons`). Este módulo cobre o flanco que faltava:
quando o roteador detecta referência de exercício no fluxo CLÁSSICO de aulas,
o fato verificado (cadastrado pelo professor) entra SEMPRE acima do contexto
recuperado — a resposta oficial ancora a explicação (anti-alucinação).

Best-effort: qualquer falha devolve string vazia (o fluxo segue intocado).
"""

from typing import Optional

import structlog

from src.rag.query_router import route_query

logger = structlog.get_logger(__name__)


async def fetch_verified_fact_directives(
    course_id: str,
    subject_id: Optional[str],
    query: str,
) -> str:
    """Diretivas de Gabarito Verificado + Errata Viva para a pergunta, quando
    o roteador detecta referência exata de exercício/capítulo."""
    route = route_query(query)
    if not (route.exercise_ref or route.chapter_ref):
        return ""

    directives = ""
    try:
        # Gabarito Verificado: a resposta oficial como âncora.
        if route.exercise_ref:
            from sqlalchemy import text as sql_text
            from src.db.database import AsyncSessionLocal
            from src.shared.constants.prompts import VERIFIED_ANSWER_KEY_DIRECTIVE

            subject_clause = "ak.subject_id = :sub" if subject_id else "ak.subject_id IS NULL"
            async with AsyncSessionLocal() as session:
                res = await session.execute(
                    sql_text(f"""
                        SELECT ak.section_ref, ak.correct_answer, ak.explanation
                        FROM "AnswerKeys" ak
                        WHERE ak.course_id = :cid AND {subject_clause}
                          AND ak.section_ref = :ref
                        LIMIT 1
                    """),
                    {"cid": course_id, "ref": route.exercise_ref,
                     **({"sub": subject_id} if subject_id else {})},
                )
                key = res.fetchone()
            if key:
                explanation_block = f"Explicação oficial: {key.explanation}\n" if key.explanation else ""
                directives += VERIFIED_ANSWER_KEY_DIRECTIVE.format(
                    section_ref=key.section_ref,
                    correct_answer=key.correct_answer,
                    explanation_block=explanation_block,
                )

        # Errata Viva da seção citada.
        if route.chapter_ref:
            from src.workers.reading_journey import get_errata_warning
            errata_note = await get_errata_warning(course_id, subject_id, route.chapter_ref)
            if errata_note:
                directives += (
                    "\nERRATA OFICIAL desta seção (cadastrada pelo professor — mencione ao aluno):"
                    f"{errata_note}\n"
                )
    except Exception as e:
        logger.warning("verified_facts_lookup_failed", error=str(e)[:200])

    return directives
