import json
import structlog
from typing import List, Optional
from src.cache.redis_client import get_redis

logger = structlog.get_logger(__name__)

# TTL de 30 dias para o cache dos tópicos
TOPICS_CACHE_TTL = 30 * 24 * 60 * 60

async def get_cached_topics(course_id: str, lesson_number: int, subject_name: Optional[str] = None) -> Optional[List[str]]:
    """Tenta recuperar a lista de tópicos em cache para uma aula específica."""
    try:
        redis = await get_redis()
        safe_subject = subject_name.replace(" ", "_").lower() if subject_name else "default"
        cache_key = f"topics:{course_id}:{safe_subject}:{lesson_number}"
        
        cached_data = await redis.get(cache_key)
        if cached_data:
            logger.info("Cache hit para tópicos da aula", cache_key=cache_key)
            return json.loads(cached_data)
            
        logger.info("Cache miss para tópicos da aula", cache_key=cache_key)
        return None
    except Exception as e:
        logger.error("Falha ao buscar tópicos no cache", error=str(e))
        return None

async def set_cached_topics(course_id: str, lesson_number: int, topics: List[str], subject_name: Optional[str] = None) -> bool:
    """Salva a lista de tópicos no Redis com TTL de 30 dias."""
    try:
        if not topics:
            return False
            
        redis = await get_redis()
        safe_subject = subject_name.replace(" ", "_").lower() if subject_name else "default"
        cache_key = f"topics:{course_id}:{safe_subject}:{lesson_number}"
        
        await redis.set(
            cache_key, 
            json.dumps(topics),
            ex=TOPICS_CACHE_TTL
        )
        logger.info("Tópicos salvos no cache com sucesso", cache_key=cache_key)
        return True
    except Exception as e:
        logger.error("Falha ao salvar tópicos no cache", error=str(e))
        return False

# TTL de 14 dias para tópicos consumidos por aluno
STUDENT_TOPIC_TTL = 14 * 24 * 60 * 60

async def get_student_used_topics(student_id: str, course_id: str, lesson_number: int, subject_name: Optional[str] = None) -> List[str]:
    """Retorna a lista de tópicos que o aluno já consumiu para esta aula."""
    try:
        redis = await get_redis()
        safe_subject = subject_name.replace(" ", "_").lower() if subject_name else "default"
        cache_key = f"used_topics:{student_id}:{course_id}:{safe_subject}:{lesson_number}"
        
        # SMEMBERS returns bytes or strings depending on decode_responses
        members = await redis.smembers(cache_key)
        return [m.decode('utf-8') if isinstance(m, bytes) else m for m in members]
    except Exception as e:
        logger.error("Falha ao buscar tópicos consumidos do aluno", error=str(e))
        return []

async def add_student_used_topic(student_id: str, course_id: str, lesson_number: int, topic: str, subject_name: Optional[str] = None) -> None:
    """Adiciona um tópico consumido na lista do aluno com TTL de 14 dias."""
    try:
        redis = await get_redis()
        safe_subject = subject_name.replace(" ", "_").lower() if subject_name else "default"
        cache_key = f"used_topics:{student_id}:{course_id}:{safe_subject}:{lesson_number}"
        
        await redis.sadd(cache_key, topic)
        await redis.expire(cache_key, STUDENT_TOPIC_TTL)
    except Exception as e:
        logger.error("Falha ao salvar tópico consumido do aluno", error=str(e))

async def clear_student_used_topics(student_id: str, course_id: str, lesson_number: int, subject_name: Optional[str] = None) -> None:
    """Limpa o rastreamento do aluno para reiniciar o ciclo caso ele exaure os tópicos."""
    try:
        redis = await get_redis()
        safe_subject = subject_name.replace(" ", "_").lower() if subject_name else "default"
        cache_key = f"used_topics:{student_id}:{course_id}:{safe_subject}:{lesson_number}"
        
        await redis.delete(cache_key)
        logger.info("Ciclo de tópicos do aluno resetado", cache_key=cache_key)
    except Exception as e:
        logger.error("Falha ao limpar tópicos do aluno", error=str(e))

