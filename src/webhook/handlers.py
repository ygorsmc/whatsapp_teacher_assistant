from fastapi import Request, Response, APIRouter
from typing import Optional
from uuid import uuid4
import json
import re
from sqlalchemy import text as sql_text

from src.webhook.security import check_rate_limit
from src.queue.factory import get_queue
from src.shared.telemetry import track_event
from src.cache.redis_client import get_redis
from src.db.database import AsyncSessionLocal
from src.shared.constants.messages import (
    UNSUPPORTED_MESSAGE_TYPE, ERROR_UNAUTHORIZED
)

from src.channel.interfaces import IMessagingChannel, InboundMessage
from src.channel.whatsapp import WhatsAppChannel
from src.channel.telegram import TelegramChannel

router = APIRouter()

whatsapp_channel = WhatsAppChannel()
telegram_channel = TelegramChannel()

async def get_user_role(sender_id: str, query_column: str) -> Optional[str]:
    """
    Returns 'student' or 'teacher' or None.
    Cache-Aside: Redis first, SQL fallback.
    """
    redis = await get_redis()
    cache_key = f"role:{sender_id}"
    
    # --- HARDCODED ADMIN (First setup) ---
    if sender_id == "5513974033349" or sender_id == "13974033349":
        return "teacher"
    # -------------------------------------

    cached = await redis.get(cache_key)
    if cached:
        return cached

    # Fallback to DB
    async with AsyncSessionLocal() as session:
        # Check student
        result = await session.execute(
            sql_text(f'SELECT 1 FROM \"Students\" WHERE {query_column} = :sid AND is_active = true'),
            {"sid": sender_id}
        )
        if result.scalar_one_or_none():
            await redis.set(cache_key, "student", ex=86400)
            return "student"
            
        # Check teacher
        result = await session.execute(
            sql_text(f'SELECT 1 FROM \"Teachers\" WHERE {query_column} = :sid AND is_active = true'),
            {"sid": sender_id}
        )
        if result.scalar_one_or_none():
            await redis.set(cache_key, "teacher", ex=86400)
            return "teacher"
            
    return None

def extract_phone_for_linking(text: str) -> str | None:
    """Extrai os últimos 11 dígitos do texto se houver um telefone."""
    digits = re.sub(r"\D", "", text)
    if len(digits) >= 11:
        return digits[-11:]
    return None

async def handle_telegram_linking(chat_id: str, text: str) -> Response:
    """Implementa a Trava de Segurança e fluxo de vinculação do Telegram."""
    phone_digits = extract_phone_for_linking(text)
    
    if not phone_digits:
        await telegram_channel.send_text(
            chat_id, 
            "Olá! Para começarmos, por favor digite seu número de telefone cadastrado com DDD (ex: 11999999999)."
        )
        return Response(status_code=200)

    # Tenta vincular
    async with AsyncSessionLocal() as session:
        # Busca professor
        result_teacher = await session.execute(
            sql_text('SELECT id FROM "Teachers" WHERE phone LIKE :phone_like AND is_active = true'),
            {"phone_like": f"%{phone_digits}"}
        )
        teacher_id = result_teacher.scalar_one_or_none()

        if teacher_id:
            await session.execute(
                sql_text('UPDATE "Teachers" SET telegram_chat_id = :chat_id WHERE id = :id'),
                {"chat_id": chat_id, "id": teacher_id}
            )
            await session.commit()
            
            redis = await get_redis()
            await redis.delete(f"role:{chat_id}")
            
            await telegram_channel.send_text(
                chat_id,
                "👨‍🏫 Conta de *Professor* vinculada com sucesso! Você já pode usar comandos como /sinc_alunos."
            )
            return Response(status_code=200)

        # Busca estudante se não for professor
        result_student = await session.execute(
            sql_text('SELECT id FROM "Students" WHERE phone LIKE :phone_like AND is_active = true'),
            {"phone_like": f"%{phone_digits}"}
        )
        student_id = result_student.scalar_one_or_none()

        if student_id:
            # Vincula!
            await session.execute(
                sql_text('UPDATE "Students" SET telegram_chat_id = :chat_id WHERE id = :id'),
                {"chat_id": chat_id, "id": student_id}
            )
            await session.commit()
            
            # Limpa cache se existir
            redis = await get_redis()
            await redis.delete(f"role:{chat_id}")
            
            await telegram_channel.send_text(
                chat_id,
                "✅ Conta vinculada com sucesso! Você já pode conversar comigo."
            )
        else:
            await telegram_channel.send_text(
                chat_id,
                "❌ Não encontrei esse número cadastrado. Tem certeza que é o número correto? Tente novamente."
            )
            
    return Response(status_code=200)


async def _process_message(msg: InboundMessage, channel: IMessagingChannel):
    """
    Processamento unificado agnóstico de canal.
    """
    sender_id = msg.sender_id
    query_column = channel.get_user_query_column()
    
    # 0.5. Global Idempotency lock
    lock_key = f"webhook:{msg.message_id}"
    redis = await get_redis()
    is_new = await redis.set(lock_key, "1", nx=True, ex=300)
    if not is_new:
        print(f"[DIAG] Duplicate webhook dropped: {msg.message_id}")
        return Response(status_code=200)

    # 1. Whitelist + role - Cache-Aside
    user_role = await get_user_role(sender_id, query_column)
    
    if user_role is None:
        # Se for Telegram, ativa a Trava de Segurança (Vinculação)
        if isinstance(channel, TelegramChannel):
            return await handle_telegram_linking(sender_id, msg.text)
        else:
            # WhatsApp
            print(f"[DIAG] REJECTED: user_role is None for {sender_id}")
            return Response(status_code=200)

    # 2. Rate limiting
    if not await check_rate_limit(sender_id):
        print(f"[DIAG] REJECTED: rate limited {sender_id}")
        return Response(status_code=200)

    # Validação de tipo de mensagem
    if msg.message_type == "unknown":
        await channel.send_text(sender_id, UNSUPPORTED_MESSAGE_TYPE)
        return Response(status_code=200)

    # 3.1. Log interaction in DB
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            sql_text(f'SELECT id FROM \"Students\" WHERE {query_column} = :p'), 
            {"p": sender_id}
        )
        student_row = res.fetchone()
        if student_row:
            student_id = student_row[0]
            interaction_type = "message_received"
            if msg.message_type == "interactive" and msg.interactive_payload and msg.interactive_payload.get("id", "").startswith("quiz_"):
                interaction_type = "quiz_answered"
            
            await session.execute(
                sql_text("""
                    INSERT INTO "StudentInteractions" (id, student_id, interaction_type, message_content) 
                    VALUES (:id, :sid, :itype, :msg)
                """),
                {
                    "id": str(uuid4()),
                    "sid": student_id,
                    "itype": interaction_type,
                    "msg": msg.text or (msg.interactive_payload.get("title", "") if msg.interactive_payload else "")
                }
            )
            await session.commit()

    # 4. Command routing (only for text messages)
    if msg.message_type == "text" and msg.text.startswith("/"):
        if user_role != "teacher":
            await channel.send_text(sender_id, ERROR_UNAUTHORIZED)
            return Response(status_code=200)
            
        await get_queue().enqueue("command_messages", {
            "sender_id": sender_id, # Usamos sender_id genericamente agora
            "text": msg.text,
            "message_id": msg.message_id
        })
        return Response(status_code=200)

    # 6. Enqueue to Inbound Messages with session FIFO
    correlation_id = str(uuid4())
    await get_queue().enqueue(
        queue="inbound_messages",
        payload={
            "sender_id": sender_id,
            "text": msg.text,
            "message_type": msg.message_type,
            "interactive_payload": msg.interactive_payload,
            "correlation_id": correlation_id,
            "message_id": msg.message_id
        },
        session_id=sender_id # Garantia FIFO local por chat
    )

    # 7. WAKE UP HACK FOR KEDA
    await get_queue().enqueue("command_messages", {
        "sender_id": sender_id,
        "text": "/wakeup",
        "message_id": f"wakeup_{msg.message_id}"
    })

    return Response(status_code=200)


# ==========================================
# ENDPOINTS
# ==========================================

@router.get("/v1/webhook/whatsapp")
@router.post("/v1/webhook/whatsapp")
async def webhook_whatsapp(request: Request):
    """Webhook nativo do WhatsApp Cloud API."""
    
    # Challenge GET
    if request.method == "GET" and "hub.mode" in request.query_params:
        return whatsapp_channel.build_verification_response(request.query_params)

    # Validação de POST
    body_bytes = await request.body()
    if not await whatsapp_channel.verify_signature(body_bytes, request.headers):
        track_event("webhook_signature_failed", {"channel": "whatsapp"})
        return Response(status_code=200)

    body = json.loads(body_bytes)
    msg = whatsapp_channel.parse_inbound(body)
    
    if not msg:
        return Response(status_code=200)
        
    return await _process_message(msg, whatsapp_channel)


@router.post("/v1/webhook/telegram")
async def webhook_telegram(request: Request):
    """Webhook nativo do Telegram Bot API."""
    
    body_bytes = await request.body()
    if not await telegram_channel.verify_signature(body_bytes, request.headers):
        track_event("webhook_signature_failed", {"channel": "telegram"})
        return Response(status_code=200)

    body = json.loads(body_bytes)
    msg = telegram_channel.parse_inbound(body)
    
    if not msg:
        return Response(status_code=200)
        
    return await _process_message(msg, telegram_channel)

