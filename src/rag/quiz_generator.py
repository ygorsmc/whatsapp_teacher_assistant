from pydantic import BaseModel, Field
from src.llm.factory import get_provider
from src.shared.constants.prompts import QUIZ_GENERATION_PROMPT, TOPIC_EXTRACTION_PROMPT
import random
from src.cache.topic_cache import get_cached_topics, set_cached_topics

class QuizAlternative(BaseModel):
    id: str = Field(description="Identificador da alternativa (A, B, C, D, E)")
    text: str = Field(description="Texto da alternativa")

class QuizOutput(BaseModel):
    question: str = Field(description="Pergunta clara e direta sobre o conteúdo da aula")
    alternatives: list[QuizAlternative] = Field(description="Lista com EXATAMENTE 3 alternativas (A, B, C)")
    correct_answer: str = Field(description="ID da alternativa correta")
    explanation: str = Field(description="Explicação pedagógica de por que a resposta está correta. NUNCA cite a letra da alternativa (A, B, C) neste texto.")

# Lentes Pedagógicas para forçar variação no quiz
QUIZ_PERSPECTIVES = [
    "Focado em um detalhe técnico minucioso e importante",
    "Focado em uma aplicação prática ou situação hipotética do mundo real",
    "Focado exclusivamente na definição exata do conceito central",
    "Focado na exceção à regra, em uma limitação ou no que NÃO se aplica",
    "Focado na conexão com um problema cotidiano do mercado de trabalho"
]

async def generate_quiz(
    course_id: str,
    lesson_number: int,
    lesson_content: str,
    num_alternatives: int = 4,
) -> QuizOutput:
    """Gera um quiz em JSON estruturado via LLM usando Two-Step Variance.
    
    Etapa 1: Extrai tópicos base (com cache via Redis).
    Etapa 2: Sorteia um tópico e uma perspectiva e gera o quiz.
    """
    llm = get_provider()
    
    # Etapa 1: Extração de tópicos com Cache
    topics = await get_cached_topics(course_id, lesson_number)
    
    if not topics:
        topic_prompt = TOPIC_EXTRACTION_PROMPT.format(context=lesson_content)
        topics_response = await llm.agenerate(topic_prompt)
        
        # Processa os tópicos retornados
        topics = [t.strip() for t in topics_response.split(",") if t.strip()]
        if not topics:
            topics = ["Conceito Geral da Aula"]
            
        # Salva no cache por 30 dias
        await set_cached_topics(course_id, lesson_number, topics)
        
    chosen_topic = random.choice(topics)
    chosen_perspective = random.choice(QUIZ_PERSPECTIVES)
    
    prompt = QUIZ_GENERATION_PROMPT.format(
        context=lesson_content,
        topic=chosen_topic,
        perspective=chosen_perspective,
    )
    
    quiz = await llm.agenerate_structured(prompt, QuizOutput)
    
    # Algorithmic randomization
    if quiz.alternatives:
        correct_id = quiz.correct_answer
        correct_text = next((a.text for a in quiz.alternatives if a.id == correct_id), None)
        
        if correct_text:
            random.shuffle(quiz.alternatives)
            new_ids = ["A", "B", "C", "D", "E"]
            for i, alt in enumerate(quiz.alternatives):
                alt.id = new_ids[i]
                if alt.text == correct_text:
                    quiz.correct_answer = alt.id
                    
    return quiz
