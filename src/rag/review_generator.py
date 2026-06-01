"""
Review Generator — Generates pedagogical summaries with mandatory quiz
from content indexed via Azure Document Intelligence in Qdrant.

All content is generated in a SINGLE LLM call to minimize token cost.
"""

from pydantic import BaseModel, Field
from langchain_core.output_parsers import PydanticOutputParser
from src.llm.factory import get_provider
from src.rag.retriever import retrieve_context
from src.rag.quiz_generator import QuizAlternative
from src.shared.config import settings
from typing import Optional
import structlog

logger = structlog.get_logger(__name__)


class ReviewQuiz(BaseModel):
    question: str = Field(description="Pergunta clara sobre o conteúdo revisado")
    alternatives: list[QuizAlternative] = Field(
        description="Lista de 3 alternativas (A, B, C)"
    )
    correct_answer: str = Field(description="ID da alternativa correta (A, B ou C)")
    explanation: str = Field(
        description="Explicação pedagógica de por que a resposta está correta"
    )


class ReviewOutput(BaseModel):
    summary: str = Field(
        description="Resumo pedagógico dos pontos-chave da aula, em formato WhatsApp (máx 800 chars)"
    )
    key_points: list[str] = Field(
        description="Lista de 3 a 5 pontos-chave extraídos do conteúdo"
    )
    quiz: ReviewQuiz = Field(
        description="Quiz de múltipla escolha baseado no resumo (3 alternativas)"
    )


parser = PydanticOutputParser(pydantic_object=ReviewOutput)

from src.shared.constants.prompts import REVIEW_PROMPT_TEMPLATE


async def generate_review(
    teacher_id: str,
    teacher_name: str,
    teacher_subject: str,
    student_name: str,
    course_id: str,
    course_name: str,
    current_lesson_number: int,
    lesson_label: str,
) -> Optional[ReviewOutput]:
    """Generates a review summary + quiz from indexed lesson content.

    Retrieves the most representative chunks from Qdrant for the given lesson,
    then generates a structured review in a single LLM call.

    Returns None if no content is found for the lesson in Qdrant.
    """
    # 1. Retrieve lesson content from Qdrant
    # Use a broad query to get representative chunks from the entire lesson
    lesson_content = await retrieve_context(
        teacher_id=teacher_id,
        course_id=course_id,
        current_lesson_number=current_lesson_number,
        query=f"resumo principal da aula {current_lesson_number}",
        top_k=8,  # More chunks for comprehensive review
        score_threshold=0.3,  # Lower threshold to capture more content
    )

    if not lesson_content:
        logger.warning(
            "No content found in Qdrant for review generation",
            teacher_id=teacher_id,
            course_id=course_id,
            current_lesson_number=current_lesson_number,
        )
        return None

    # 2. Build the prompt using str.format()
    prompt = REVIEW_PROMPT_TEMPLATE.format(
        teacher_name=teacher_name,
        teacher_subject=teacher_subject,
        student_name=student_name,
        course_name=course_name,
        lesson_label=lesson_label,
        lesson_content=lesson_content,
        format_instructions=parser.get_format_instructions(),
    )

    # 3. Single LLM call — generates summary + key_points + quiz together
    llm = get_provider(settings.DEFAULT_LLM_PROVIDER)
    response = await llm.agenerate(prompt=prompt)

    # 4. Parse structured output
    try:
        review = parser.parse(response)
        logger.info(
            "Review generated successfully",
            teacher_id=teacher_id,
            course_id=course_id,
            current_lesson_number=current_lesson_number,
            key_points_count=len(review.key_points),
        )
        return review
    except Exception as e:
        logger.error(
            "Failed to parse review output from LLM",
            error=str(e)[:500],
            raw_response=response[:200],
        )
        return None
