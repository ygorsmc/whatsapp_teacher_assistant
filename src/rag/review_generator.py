"""
Review Generator — Generates pedagogical summaries with mandatory quiz
from content indexed via Azure Document Intelligence in Qdrant.

All content is generated in a SINGLE LLM call to minimize token cost.
"""

from pydantic import BaseModel, Field
from src.llm.factory import get_provider
from src.rag.retriever import retrieve_context
from src.rag.quiz_generator import QuizAlternative
from src.shared.config import settings
from typing import Optional, cast
import structlog
import random
from src.shared.constants.prompts import REVIEW_PROMPT_TEMPLATE, TOPIC_EXTRACTION_PROMPT

logger = structlog.get_logger(__name__)


class ReviewQuiz(BaseModel):
    question: str = Field(description="Pergunta clara sobre o conteúdo revisado")
    alternatives: list[QuizAlternative] = Field(
        description="Lista de 3 alternativas (A, B, C)"
    )
    correct_answer: str = Field(description="ID da alternativa correta (A, B ou C)")
    explanation: str = Field(
        description="Explicação pedagógica de por que a resposta está correta. NUNCA cite a letra da alternativa (A, B, C) neste texto."
    )


class ReviewOutput(BaseModel):
    summary: str = Field(
        description="Resumo pedagógico dos pontos-chave da aula, em formato conversacional (máx 800 chars)"
    )
    key_points: list[str] = Field(
        description="Lista de 3 a 5 pontos-chave extraídos do conteúdo"
    )
    quiz: ReviewQuiz = Field(
        description="Quiz de múltipla escolha baseado no resumo (3 alternativas)"
    )



# Lentes Pedagógicas customizadas para o Resumo Agendado
REVIEW_PERSPECTIVES = [
    "Faça um resumo informativo que prepare o aluno para uma sessão de perguntas focado em um detalhe técnico minucioso",
    "Faça um resumo informativo que prepare o aluno para uma sessão de perguntas sobre uma situação hipotética do mundo real",
    "Faça um resumo informativo que prepare o aluno para uma sessão de perguntas sobre uma exceção à regra ou limitação",
    "Faça um resumo informativo que prepare o aluno para uma sessão de perguntas explorando a definição do conceito central"
]

async def generate_review(
    teacher_id: str,
    teacher_name: str,
    teacher_subject: str,
    student_id: str,
    student_name: str,
    course_id: str,
    course_name: str,
    subject_name: str | None,
    current_lesson_number: int,
    lesson_label: str,
) -> Optional[ReviewOutput]:
    """Generates a review summary + quiz from indexed lesson content.

    Retrieves the most representative chunks from Qdrant for the given lesson,
    then generates a structured review in a single LLM call.

    Returns None if no content is found for the lesson in Qdrant.
    """
    # 1. Retrieve lesson content from Qdrant
    lesson_content = await retrieve_context(
        teacher_id=teacher_id,
        course_id=course_id,
        subject_name=subject_name,
        current_lesson_number=current_lesson_number,
        query=f"resumo principal da aula {current_lesson_number}",
        top_k=settings.RAG_TOP_K * 2,  # More chunks for comprehensive review
        score_threshold=settings.RAG_SCORE_THRESHOLD,  # Lower threshold to capture more content
    )

    if not lesson_content:
        logger.warning(
            "No content found in Qdrant for review generation",
            teacher_id=teacher_id,
            course_id=course_id,
            current_lesson_number=current_lesson_number,
        )
        return None

    llm = get_provider(settings.DEFAULT_LLM_PROVIDER)
    
    from src.cache.topic_cache import get_cached_topics, set_cached_topics, get_student_used_topics, add_student_used_topic, clear_student_used_topics

    # 2. Etapa 1: Extração de tópicos com Cache Global da Aula
    topics = await get_cached_topics(course_id, current_lesson_number, subject_name)
    
    if not topics:
        topic_prompt = TOPIC_EXTRACTION_PROMPT.format(context=lesson_content)
        topics_response = await llm.agenerate(prompt=topic_prompt)
        
        topics = [t.strip() for t in topics_response.split(",") if t.strip()]
        if not topics:
            topics = ["Conceito Geral da Aula"]
            
        await set_cached_topics(course_id, current_lesson_number, topics, subject_name)
        
    # 3. Restrição por Aluno (Evitar repetição)
    used_topics = await get_student_used_topics(student_id, course_id, current_lesson_number, subject_name)
    available_topics = [t for t in topics if t not in used_topics]
    
    # Se o aluno já respondeu a TODOS os tópicos desta aula, resetamos a lista dele!
    if not available_topics:
        logger.info("Aluno exauriu os tópicos desta aula. Resetando ciclo.", student_id=student_id, lesson_number=current_lesson_number)
        await clear_student_used_topics(student_id, course_id, current_lesson_number, subject_name)
        available_topics = topics # Restaura tudo
        
    chosen_topic = random.choice(available_topics)
    chosen_perspective = random.choice(REVIEW_PERSPECTIVES)
    
    # Grava o tópico recém-sorteado no cache do aluno para ele não recebê-lo de novo
    await add_student_used_topic(student_id, course_id, current_lesson_number, chosen_topic, subject_name)

    # 4. Etapa 2: Geração do Resumo e Quiz (Em 1 só chamada usando Structured Outputs)
    prompt = REVIEW_PROMPT_TEMPLATE.format(
        teacher_name=teacher_name,
        teacher_subject=teacher_subject,
        student_name=student_name,
        course_name=course_name,
        lesson_label=lesson_label,
        topic=chosen_topic,
        perspective=chosen_perspective,
        lesson_content=lesson_content,
    )

    # Single LLM call — generates summary + key_points + quiz together
    try:
        review = cast(ReviewOutput, await llm.agenerate_structured(prompt=prompt, schema=ReviewOutput))
        
        # Algorithmic randomization to prevent LLM bias (always picking B)
        if review.quiz and review.quiz.alternatives:
            correct_id = review.quiz.correct_answer
            correct_text = next((a.text for a in review.quiz.alternatives if a.id == correct_id), None)
            
            if correct_text:
                random.shuffle(review.quiz.alternatives)
                new_ids = ["A", "B", "C"]
                for i, alt in enumerate(review.quiz.alternatives):
                    alt.id = new_ids[i]
                    if alt.text == correct_text:
                        review.quiz.correct_answer = alt.id

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
            error=str(e)[:500]
        )
        return None
