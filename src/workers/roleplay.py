"""
Roleplay / cenário estruturado (F4 — Vertical de Idiomas).

Modo conversacional: quando o aluno pede ("vamos praticar uma conversa no
restaurante"), o bot assume um PERSONAGEM e conduz a dramatização na língua-alvo,
um turno por vez, ancorado no material (Recusa Honesta). O estado da sessão (o
cenário) vive no Redis (`roleplay:{student}`, TTL curto); cada turno é 1 chamada
de LLM dentro da janela 24h — sem LLM-juiz.

Integra-se ao fluxo normal de resposta: enquanto a sessão está ativa, anexamos a
`ROLEPLAY_DIRECTIVE` ao System Prompt (o RAG continua ancorando). Atrás de
`ROLEPLAY_ENABLED` + `LANGUAGE_MODE_ENABLED` + turma de idioma.
"""

import structlog

from src.cache.redis_client import get_redis
from src.shared.config import settings
from src.shared.constants.prompts import ROLEPLAY_DIRECTIVE
from src.shared.language import language_name
from src.rag.query_router import detect_roleplay_intent, is_roleplay_exit

logger = structlog.get_logger(__name__)

ROLEPLAY_TTL = 1800  # 30 min — TTL curto (a cena não persiste indefinidamente)
_DEFAULT_SCENARIO = "conversa cotidiana"


def roleplay_enabled() -> bool:
    return bool(settings.LANGUAGE_MODE_ENABLED and settings.ROLEPLAY_ENABLED)


def _key(student_id: str) -> str:
    return f"roleplay:{student_id}"


async def maybe_apply_roleplay(
    system_message: str,
    query: str,
    student_id: str,
    target_language: str,
    cefr_level: str,
) -> tuple[str, bool]:
    """Se houver sessão de roleplay ativa (ou intenção de iniciar), anexa a
    `ROLEPLAY_DIRECTIVE` ao System Prompt e devolve (prompt, True). Pedido de sair
    → encerra a sessão NESTE turno (rule 5 dá o adeus). Senão devolve (prompt, False).

    O chamador já garantiu ROLEPLAY_ENABLED + curso de idioma."""
    redis = await get_redis()
    key = _key(student_id)
    raw = await redis.get(key)

    scenario: str | None = None
    if raw is not None:
        scenario = raw.decode() if isinstance(raw, bytes) else str(raw)
        if is_roleplay_exit(query):
            await redis.delete(key)   # último turno do roleplay; depois volta ao normal
        else:
            await redis.expire(key, ROLEPLAY_TTL)
    else:
        detected = detect_roleplay_intent(query)
        if detected is not None:
            scenario = detected or _DEFAULT_SCENARIO
            await redis.set(key, scenario, ex=ROLEPLAY_TTL)
            logger.info("roleplay_started", student_id=student_id, scenario=scenario[:40])

    if scenario is None:
        return system_message, False

    directive = ROLEPLAY_DIRECTIVE.format(
        scenario=scenario or _DEFAULT_SCENARIO,
        target_language_name=language_name(target_language),
        cefr_level=cefr_level,
    )
    return system_message + directive, True
