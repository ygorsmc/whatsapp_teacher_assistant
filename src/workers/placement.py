"""
Nivelamento CEFR (F5) — teste de placement automático.

Fluxo (1 round-trip, sem máquina de estados pesada):
  /nivelamento → gera 6 questões calibradas A1→C2 (CEFR_PLACEMENT_PROMPT, 1 LLM)
    → envia numa mensagem + guarda o gabarito no Redis (placement:{sender}, TTL 1h)
  resposta do aluno (texto) → o llm_worker, vendo a sessão ativa, roteia para cá
    → classify_cefr (puro) → grava cefr_level na matrícula (StudentEnrollment)

Reusa o quiz determinístico (alternativas A/B/C). Atrás de PLACEMENT_TEST_ENABLED
+ LANGUAGE_MODE_ENABLED + turma de idioma. classify_cefr/parse_placement_answers
são puros e testáveis.
"""

import json
import re

import structlog
from pydantic import BaseModel, Field

from src.cache.redis_client import get_redis
from src.shared.config import settings
from src.shared.constants.messages import (
    PLACEMENT_ANSWERS_UNCLEAR,
    PLACEMENT_FAILED,
    PLACEMENT_INTRO,
    PLACEMENT_RESULT,
    PLACEMENT_UNAVAILABLE,
)
from src.shared.constants.prompts import CEFR_PLACEMENT_PROMPT
from src.shared.language import classify_cefr, detect_language_course, language_name

logger = structlog.get_logger(__name__)

PLACEMENT_TTL = 3600  # 1h para responder
PLACEMENT_N = 6       # uma questão por nível CEFR (A1→C2)
_LETTERS = ["A", "B", "C", "D", "E"]

_NUMBERED = re.compile(r"(\d+)\s*[-).:]*\s*([A-Ea-e])\b")
_ANY_LETTER = re.compile(r"[A-Ea-e]")


class PlacementQuestion(BaseModel):
    cefr: str = Field(description="Nível CEFR: A1, A2, B1, B2, C1 ou C2")
    question: str
    options: list[str] = Field(description="3 alternativas, na ordem A, B, C")
    correct: str = Field(description="Letra da correta: A, B ou C")


class PlacementQuiz(BaseModel):
    questions: list[PlacementQuestion] = Field(description="6 questões em ordem A1→C2")


def placement_enabled() -> bool:
    return bool(settings.LANGUAGE_MODE_ENABLED and settings.PLACEMENT_TEST_ENABLED)


def _key(sender_id: str) -> str:
    return f"placement:{sender_id}"


def parse_placement_answers(text: str, n: int = PLACEMENT_N) -> list[str | None]:
    """Extrai as respostas do aluno (puro/testável). Aceita '1A 2B 3C', '1) A',
    'A B C ...' ou bloco contíguo 'ABCABC'. Numeração tem precedência; senão usa a
    sequência de letras. Posições sem resposta ficam None."""
    out: list[str | None] = [None] * n
    pairs = _NUMBERED.findall(text or "")
    if pairs:
        for num, letter in pairs:
            i = int(num) - 1
            if 0 <= i < n:
                out[i] = letter.upper()
        return out
    for i, letter in enumerate(_ANY_LETTER.findall(text or "")[:n]):
        out[i] = letter.upper()
    return out


async def start_placement(channel, sender_id: str) -> None:
    """/nivelamento → gera e envia o teste; guarda o gabarito no Redis.

    Self-contained (resolve o aluno e a TURMA DE IDIOMA dele pelo identificador do
    canal). Sem turma de idioma → indisponível."""
    if not placement_enabled():
        await channel.send_text(sender_id, PLACEMENT_UNAVAILABLE)
        return

    from src.db.database import AsyncSessionLocal
    from sqlalchemy import text as sql_text
    query_col = channel.get_user_query_column()
    async with AsyncSessionLocal() as session:
        row = (await session.execute(
            sql_text(f'''SELECT s.id AS student_id, cl.id AS class_id, co.name AS course_name
                         FROM "Students" s
                         JOIN "StudentEnrollment" se ON se.student_id = s.id
                         JOIN "Classes" cl ON cl.id = se.class_id AND cl.is_language = true
                         JOIN "Courses" co ON co.id = cl.course_id
                         WHERE s.{query_col} = :sid AND s.is_active = true
                         LIMIT 1'''),
            {"sid": sender_id},
        )).fetchone()

    if not row:
        await channel.send_text(sender_id, PLACEMENT_UNAVAILABLE)
        return

    class_id = row.class_id
    spec = detect_language_course(row.course_name, default_target=settings.DEFAULT_TARGET_LANGUAGE)
    target = spec.target_language if spec else settings.DEFAULT_TARGET_LANGUAGE

    from src.llm.factory import get_provider
    from typing import cast
    llm = get_provider(settings.DEFAULT_LLM_PROVIDER)
    try:
        quiz = cast(PlacementQuiz, await llm.agenerate_structured(
            prompt=CEFR_PLACEMENT_PROMPT.format(
                target_language_name=language_name(target),
                native_language_name=language_name(settings.DEFAULT_NATIVE_LANGUAGE),
            ),
            schema=PlacementQuiz,
        ))
    except Exception as e:
        logger.error("placement_generation_failed", error=str(e)[:300])
        await channel.send_text(sender_id, PLACEMENT_FAILED)
        return

    questions = quiz.questions[:PLACEMENT_N]
    if len(questions) < PLACEMENT_N:
        await channel.send_text(sender_id, PLACEMENT_FAILED)
        return

    # Mensagem do teste + gabarito (só cefr + correta) no Redis.
    body = PLACEMENT_INTRO
    for i, q in enumerate(questions, start=1):
        opts = "\n".join(f"   {_LETTERS[j]}) {opt}" for j, opt in enumerate(q.options[:3]))
        body += f"\n*{i}.* {q.question}\n{opts}\n"
    key_data = {
        "class_id": class_id,
        "answers": [{"cefr": q.cefr, "correct": (q.correct or "A").strip().upper()[:1]} for q in questions],
    }
    redis = await get_redis()
    await redis.set(_key(sender_id), json.dumps(key_data), ex=PLACEMENT_TTL)
    await channel.send_text(sender_id, body[:4000])


async def placement_active(sender_id: str) -> bool:
    redis = await get_redis()
    return bool(await redis.get(_key(sender_id)))


async def handle_placement_answer(channel, sender_id: str, student_context: dict, text: str) -> bool:
    """Processa a resposta do teste (chamado pelo llm_worker quando há sessão
    ativa). Retorna True se consumiu a mensagem (era resposta de nivelamento)."""
    redis = await get_redis()
    raw = await redis.get(_key(sender_id))
    if not raw:
        return False

    data = json.loads(raw)
    answers = data.get("answers", [])
    n = len(answers)
    parsed = parse_placement_answers(text, n)
    if all(p is None for p in parsed):
        await channel.send_text(sender_id, PLACEMENT_ANSWERS_UNCLEAR)
        return True  # sessão segue ativa para nova tentativa

    results = [parsed[i] == answers[i]["correct"] for i in range(n)]
    cefr = classify_cefr(results)

    # Grava o nível por matrícula (aluno × turma).
    student = student_context["student"]
    class_id = data.get("class_id") or getattr(student_context.get("class_obj"), "id", None)
    if class_id:
        try:
            from src.db.database import AsyncSessionLocal
            from sqlalchemy import text as sql_text
            async with AsyncSessionLocal() as session:
                await session.execute(
                    sql_text('''UPDATE "StudentEnrollment" SET cefr_level = :lvl, updated_at = CURRENT_TIMESTAMP
                                WHERE student_id = :sid AND class_id = :cid'''),
                    {"lvl": cefr, "sid": student.id, "cid": class_id},
                )
                await session.commit()
        except Exception as e:
            logger.error("placement_persist_failed", error=str(e)[:300])

    await redis.delete(_key(sender_id))

    from src.shared.telemetry import track_event
    track_event("cefr_placed", {"student_id": student.id, "class_id": class_id, "cefr": cefr})
    await channel.send_text(sender_id, PLACEMENT_RESULT.format(cefr=cefr))
    return True
