"""Regressões de resiliência/segurança da BORDA (webhook).

Cobre os achados da revisão (Onda 1):
- S8: o webhook NUNCA devolve != 2xx para a Meta/Telegram, mesmo com o Redis
  fora (handler global em main.py) — senão a Meta dispara flood de retries.
- S8b: `check_rate_limit` é fail-open (Redis fora → deixa passar).
- S12: a verificação do secret do Telegram usa comparação em tempo constante.

Estes testes materializam o "Game Day" (§13.7 do PLANO) como suíte permanente.
"""
import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock
from fastapi.testclient import TestClient


# ─────────────────────────── S8b: rate-limit fail-open ───────────────────────
@pytest.mark.asyncio
async def test_check_rate_limit_fail_open_when_redis_down(mocker):
    """Redis indisponível → o rate-limit deixa passar (retorna True)."""
    import src.webhook.security as security

    async def _boom():
        raise ConnectionError("redis down")

    mocker.patch.object(security, "get_redis", side_effect=_boom)
    assert await security.check_rate_limit("5511999990000") is True


@pytest.mark.asyncio
async def test_check_rate_limit_blocks_over_limit(mocker):
    """Acima do limite → bloqueia (False); dentro do limite → passa (True)."""
    import src.webhook.security as security

    class _FakeRedis:
        def __init__(self, count):
            self._count = count

        async def incr(self, key):
            return self._count

        async def expire(self, key, window):
            return True

    async def _redis_over():
        return _FakeRedis(61)

    async def _redis_under():
        return _FakeRedis(1)

    mocker.patch.object(security, "get_redis", side_effect=_redis_over)
    assert await security.check_rate_limit("5511999990000", limit=60) is False

    mocker.patch.object(security, "get_redis", side_effect=_redis_under)
    assert await security.check_rate_limit("5511999990000", limit=60) is True


# ─────────────────────── S12: Telegram secret em tempo constante ──────────────
@pytest.mark.asyncio
async def test_telegram_verify_signature_constant_time(mocker):
    from src.channel.telegram import TelegramChannel
    from src.shared.config import settings

    mocker.patch.object(settings, "ENVIRONMENT", "production")
    channel = TelegramChannel()
    channel.secret_token = "s3cr3t-token"

    ok = await channel.verify_signature(
        b"{}", {"X-Telegram-Bot-Api-Secret-Token": "s3cr3t-token"}
    )
    bad = await channel.verify_signature(
        b"{}", {"X-Telegram-Bot-Api-Secret-Token": "wrong"}
    )
    missing = await channel.verify_signature(b"{}", {})
    assert ok is True
    assert bad is False
    assert missing is False


# ─────────────────────── S8: webhook devolve 200 com Redis fora ───────────────
def test_webhook_returns_200_when_redis_down(mocker):
    """Game Day: Redis morto NÃO pode virar 500 para a Meta (flood de retries)."""
    mocker.patch("src.shared.telemetry.setup_telemetry")
    mocker.patch("src.shared.telemetry.track_event")
    mocker.patch("src.db.database.init_db")

    from src.webhook.main import app
    import src.webhook.handlers as handlers

    # Assinatura válida (não é o alvo do teste) e Redis caindo no idempotency lock.
    mocker.patch.object(
        handlers.whatsapp_channel, "verify_signature",
        new=mocker.AsyncMock(return_value=True),
    )

    async def _boom():
        raise ConnectionError("redis down")

    mocker.patch.object(handlers, "get_redis", side_effect=_boom)

    # raise_server_exceptions=False: o TestClient, por padrão, re-levanta a
    # exceção (já tratada pelo handler) só para dar visibilidade no teste. Sob
    # uvicorn real isso não ocorre — a Meta recebe a Response(200) do handler.
    # Com o handler, a resposta é 200; sem ele, seria 500 — então o assert vale.
    client = TestClient(app, raise_server_exceptions=False)
    payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": "5511999999999",
                        "id": "wamid.RESILIENCE_TEST",
                        "type": "text",
                        "text": {"body": "olá"},
                    }]
                }
            }]
        }]
    }
    resp = client.post("/v1/webhook/whatsapp", json=payload)
    assert resp.status_code == 200


# ───────── PEL-replay: resposta de quiz é CAS atômico (não duplica hooks) ─────
@pytest.mark.asyncio
async def test_quiz_answer_replay_is_idempotent(mocker):
    """Se o UPDATE pending→answered não afeta linha (rowcount=0), outra entrega
    já processou: o handler NÃO dispara os hooks (streak/SRS/desafio) de novo."""
    import src.workers.llm_worker as worker
    from src.shared.constants.messages import ERROR_QUIZ_ANSWERED

    quiz_row = SimpleNamespace(
        quiz_id="QID", correct_answer="A", explanation="x", question_text="q",
        status="pending", quiz_bank_id=None, reading_plan_id=None,
        lesson_id="l1", course_id="crs_1",
    )
    select_result = Mock()
    select_result.fetchone.return_value = quiz_row
    update_result = Mock()
    update_result.rowcount = 0  # outra entrega venceu o CAS

    session = MagicMock()
    session.execute = AsyncMock(side_effect=[select_result, update_result])
    session.commit = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    mocker.patch("src.db.database.AsyncSessionLocal", return_value=cm)

    channel = MagicMock()
    channel.send_text = AsyncMock()
    # spy nos hooks para garantir que NÃO rodam no replay
    spy_linked = mocker.patch.object(worker, "_find_linked_chapter", new=AsyncMock())

    await worker.handle_quiz_response(channel, "5511999990000", "stu_1",
                                      "quiz_QID_A", {}, message_id="m1")

    channel.send_text.assert_awaited_once_with(
        "5511999990000", ERROR_QUIZ_ANSWERED, reply_to="m1")
    spy_linked.assert_not_called()  # hook de sugestão de capítulo não disparou
