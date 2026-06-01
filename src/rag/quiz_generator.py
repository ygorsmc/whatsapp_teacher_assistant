from pydantic import BaseModel, Field
from langchain_core.output_parsers import PydanticOutputParser
from src.llm.factory import get_provider

class QuizAlternative(BaseModel):
    id: str = Field(description="Identificador da alternativa (A, B, C, D, E)")
    text: str = Field(description="Texto da alternativa")

class QuizOutput(BaseModel):
    question: str = Field(description="Pergunta clara e direta sobre o conteúdo da aula")
    alternatives: list[QuizAlternative] = Field(description="Lista de alternativas (2 a 5)")
    correct_answer: str = Field(description="ID da alternativa correta")
    explanation: str = Field(description="Explicação pedagógica de por que a resposta está correta")

parser = PydanticOutputParser(pydantic_object=QuizOutput)

from src.shared.constants.prompts import QUIZ_GENERATION_PROMPT

async def generate_quiz(
    lesson_content: str,
    num_alternatives: int = 4,
) -> QuizOutput:
    """Gera um quiz em JSON estruturado via LLM.
    
    O custo de tokens é ÚNICO: a pergunta, alternativas, resposta correta
    e explicação são gerados em uma única chamada. A validação posterior
    é determinística (zero tokens).
    """
    llm = get_provider().llm_instance()
    prompt = QUIZ_GENERATION_PROMPT.format(
        context=lesson_content,
        format_instructions=parser.get_format_instructions(),
    )
    response = await llm.ainvoke(prompt)
    return parser.parse(response.content)
