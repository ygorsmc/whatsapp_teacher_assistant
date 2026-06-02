import json
import structlog
from src.llm.factory import get_provider

logger = structlog.get_logger(__name__)

async def classify_course_intent(text: str, enrolled_courses: list[dict]) -> str | None:
    """
    Given a user's free text message, determine which course they are referring to.
    Returns the course_id, or None if ambiguous/unknown.
    """
    if not enrolled_courses:
        return None
        
    if len(enrolled_courses) == 1:
        return enrolled_courses[0]["id"]
        
    prompt = "Você é um roteador de mensagens JSON.\n\n"
    prompt += "Cursos disponíveis para este aluno:\n"
    for idx, course in enumerate(enrolled_courses):
        prompt += f"- ID: {course['id']} | Nome: {course['name']}\n"
        
    prompt += f"\nMensagem recebida do aluno: \"{text}\"\n\n"
    prompt += "Identifique a qual curso a mensagem acima se refere. Retorne APENAS um JSON válido neste formato:\n"
    prompt += '{"course_id": "ID_DO_CURSO"}\n\n'
    prompt += "Se a mensagem for muito genérica (ex: 'oi', 'ajuda'), ou se aplicar a múltiplos cursos sem especificar, retorne null:\n"
    prompt += '{"course_id": null}\n'
    
    provider = get_provider() 
    
    try:
        response = await provider.agenerate(
            prompt=prompt,
            system_message="You are a JSON API. Extract the intent and reply strictly in valid JSON format, with no markdown code blocks or extra text."
        )
        
        clean_response = response.strip()
        if clean_response.startswith("```json"):
            clean_response = clean_response.replace("```json", "").replace("```", "").strip()
        elif clean_response.startswith("```"):
            clean_response = clean_response.replace("```", "").strip()
            
        data = json.loads(clean_response)
        course_id = data.get("course_id")
        
        if course_id and any(c["id"] == course_id for c in enrolled_courses):
            logger.info("Course intent classified", course_id=course_id, text=text[:30])
            return course_id
        else:
            logger.info("Course intent ambiguous", text=text[:30], response=clean_response)
            return None
            
    except Exception as e:
        logger.error("Failed to classify course intent", error=str(e))
        return None
