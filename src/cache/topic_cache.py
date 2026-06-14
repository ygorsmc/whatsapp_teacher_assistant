import json
import structlog
from typing import List, Optional
from src.cache.redis_client import get_redis

logger = structlog.get_logger(__name__)

# TTL de 30 dias para o cache dos tópicos
TOPICS_CACHE_TTL = 30 * 24 * 60 * 60

def _topics_key(course_id: str, lesson_number: int, subject_name: Optional[str] = None, material_type: str = "lesson") -> str:
    safe_subject = subject_name.replace(" ", "_").lower() if subject_name else "default"
    # Addon de Livros: o capítulo 3 do livro NÃO pode colidir com a aula 3.
    # Para 'lesson' mantemos o formato legado (preserva o cache existente).
    suffix = "" if material_type == "lesson" else f":{material_type}"
    return f"topics:{course_id}:{safe_subject}:{lesson_number}{suffix}"

async def get_cached_topics(course_id: str, lesson_number: int, subject_name: Optional[str] = None, material_type: str = "lesson") -> Optional[List[str]]:
    """Tenta recuperar a lista de tópicos em cache para uma aula/seção específica."""
    try:
        redis = await get_redis()
        cache_key = _topics_key(course_id, lesson_number, subject_name, material_type)
        
        cached_data = await redis.get(cache_key)
        if cached_data:
            logger.info("Cache hit para tópicos da aula", cache_key=cache_key)
            return json.loads(cached_data)
            
        logger.info("Cache miss para tópicos da aula", cache_key=cache_key)
        return None
    except Exception as e:
        logger.error("Falha ao buscar tópicos no cache", error=str(e))
        return None

async def set_cached_topics(course_id: str, lesson_number: int, topics: List[str], subject_name: Optional[str] = None, material_type: str = "lesson") -> bool:
    """Salva a lista de tópicos no Redis com TTL de 30 dias."""
    try:
        if not topics:
            return False

        redis = await get_redis()
        cache_key = _topics_key(course_id, lesson_number, subject_name, material_type)
        
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

async def invalidate_cached_topics(course_id: str, lesson_number: int, subject_name: Optional[str] = None, material_type: str = "lesson") -> None:
    """Invalida a lista de tópicos da aula (Ciclo de Vida do Cache).

    Chamado pelo ingestion_worker quando um novo PDF da aula é processado:
    se o material base mudou, a lista de tópicos extraída do material antigo
    pode estar obsoleta. O próximo pedido de quiz forçará o LLM a reler o
    novo RAG e recriar a lista. (Os sets `used_topics:*` por aluno não
    precisam ser tocados: tópicos que não existirem mais na nova lista são
    simplesmente ignorados pelo filtro de disponibilidade.)
    """
    try:
        redis = await get_redis()
        cache_key = _topics_key(course_id, lesson_number, subject_name, material_type)
        await redis.delete(cache_key)
        logger.info("Cache de tópicos da aula invalidado", cache_key=cache_key)
    except Exception as e:
        logger.error("Falha ao invalidar tópicos no cache", error=str(e))

# TTL de 14 dias para tópicos consumidos por aluno
STUDENT_TOPIC_TTL = 14 * 24 * 60 * 60

def _used_topics_key(student_id: str, course_id: str, lesson_number: int, subject_name: Optional[str] = None, material_type: str = "lesson") -> str:
    safe_subject = subject_name.replace(" ", "_").lower() if subject_name else "default"
    suffix = "" if material_type == "lesson" else f":{material_type}"
    return f"used_topics:{student_id}:{course_id}:{safe_subject}:{lesson_number}{suffix}"

async def get_student_used_topics(student_id: str, course_id: str, lesson_number: int, subject_name: Optional[str] = None, material_type: str = "lesson") -> List[str]:
    """Retorna a lista de tópicos que o aluno já consumiu para esta aula."""
    try:
        redis = await get_redis()
        cache_key = _used_topics_key(student_id, course_id, lesson_number, subject_name, material_type)
        
        # SMEMBERS returns bytes or strings depending on decode_responses
        members = await redis.smembers(cache_key)
        return [m.decode('utf-8') if isinstance(m, bytes) else m for m in members]
    except Exception as e:
        logger.error("Falha ao buscar tópicos consumidos do aluno", error=str(e))
        return []

async def add_student_used_topic(student_id: str, course_id: str, lesson_number: int, topic: str, subject_name: Optional[str] = None, material_type: str = "lesson") -> None:
    """Adiciona um tópico consumido na lista do aluno com TTL de 14 dias."""
    try:
        redis = await get_redis()
        cache_key = _used_topics_key(student_id, course_id, lesson_number, subject_name, material_type)
        
        await redis.sadd(cache_key, topic)
        await redis.expire(cache_key, STUDENT_TOPIC_TTL)
    except Exception as e:
        logger.error("Falha ao salvar tópico consumido do aluno", error=str(e))

async def clear_student_used_topics(student_id: str, course_id: str, lesson_number: int, subject_name: Optional[str] = None, material_type: str = "lesson") -> None:
    """Limpa o rastreamento do aluno para reiniciar o ciclo caso ele exaure os tópicos."""
    try:
        redis = await get_redis()
        cache_key = _used_topics_key(student_id, course_id, lesson_number, subject_name, material_type)
        
        await redis.delete(cache_key)
        logger.info("Ciclo de tópicos do aluno resetado", cache_key=cache_key)
    except Exception as e:
        logger.error("Falha ao limpar tópicos do aluno", error=str(e))
