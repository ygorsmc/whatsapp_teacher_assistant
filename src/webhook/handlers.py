from fastapi import Request, Response, APIRouter
from typing import Optional
from uuid import uuid4
import json
import re
import structlog
from sqlalchemy import text as sql_text

from src.webhook.security import check_rate_limit
from src.shared.config import settings
from src.queue.factory import get_queue
from src.shared.telemetry import track_event
from src.cache.redis_client import get_redis
from src.db.database import AsyncSessionLocal
from src.shared.constants.messages import (
    UNSUPPORTED_MESSAGE_TYPE, ERROR_UNAUTHORIZED
)
from src.shared.constants.commands import (
    MANAGEMENT_COMMAND_REGEX, STUDENT_COMMAND_REGEX, ACTIVATION_CODE_REGEX,
    GUARDIAN_REVOKE_REGEX,
)

from src.channel.interfaces import IMessagingChannel, InboundMessage
from src.channel.whatsapp import WhatsAppChannel
from src.channel.telegram import TelegramChannel

router = APIRouter()

# LGPD P1.4: logger estruturado — `sender_id`/contatos são mascarados pelo
# `mask_pii_processor` (telemetry.py). Substitui os `print()` de diagnóstico,
# que iam em claro (com PII) para o stdout/Log Analytics.
logger = structlog.get_logger(__name__)

whatsapp_channel = WhatsAppChannel()
telegram_channel = TelegramChannel()

async def get_user_role(sender_id: str, query_column: str) -> Optional[str]:
    """
    Returns 'student' or 'teacher' or None.
    Cache-Aside: Redis first, SQL fallback.
    """
    redis = await get_redis()
    cache_key = f"role:{sender_id}"
    
    # --- BOOTSTRAP ADMIN (First setup) ---
    if settings.BOOTSTRAP_ADMIN_PHONE and (sender_id == settings.BOOTSTRAP_ADMIN_PHONE or sender_id == settings.BOOTSTRAP_ADMIN_PHONE[2:]):
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
        logger.info("webhook_duplicate_dropped", message_id=msg.message_id, sender_id=sender_id)
        return Response(status_code=200)

    # 1. Whitelist + role - Cache-Aside
    user_role = await get_user_role(sender_id, query_column)

    # ── H2.1: ATIVAÇÃO POR QR CODE (fast-path, SLA 200ms) ────────────────────
    # O QR é um deep link wa.me → o fluxo é exclusivo do WhatsApp. Mensagem de
    # texto com chave (LIVRO-/PROF-) é roteada para a fila de ativação — de
    # remetente CONHECIDO (2º livro) ou DESCONHECIDO (que o edge descartaria).
    # Custo aqui: 1 regex pré-compilado (+1 EXISTS no Redis só p/ desconhecidos).
    if msg.message_type == "text" and isinstance(channel, WhatsAppChannel):
        has_code = bool(ACTIVATION_CODE_REGEX.search(msg.text or ""))
        pending = False
        if not has_code and user_role is None:
            # Desconhecido SEM código: só passa se estiver no meio de uma
            # ativação (respondendo o nome). Estado no Redis, TTL 1h.
            from src.workers.activation_worker import has_pending_activation
            pending = await has_pending_activation(sender_id)
        if has_code or pending:
            await get_queue().enqueue("command_messages", {
                "sender_id": sender_id,
                "text": msg.text,
                "message_id": msg.message_id,
                "channel": channel.channel_name,
                "role": "activation",
            })
            track_event("activation_routed", {"sender_id": sender_id, "step": "code" if has_code else "name"})
            return Response(status_code=200)

    # ── LGPD Art. 14: CONSENTIMENTO DO RESPONSÁVEL (fast-path, SLA 200ms) ─────
    # O responsável NÃO é usuário do bot. O clique de Autorizo/Não autorizo
    # (payload `guardian_consent_*`) ou o texto REVOGAR de um remetente
    # DESCONHECIDO são roteados para o worker de consentimento, que valida o
    # telefone contra guardian_phone. Custo aqui: prefixo de payload / 1 regex.
    if settings.GUARDIAN_CONSENT_ENABLED:
        reply_id = (msg.interactive_payload or {}).get("id", "") if msg.interactive_payload else ""
        is_consent_button = reply_id.startswith("guardian_consent_")
        is_revoke_text = (
            user_role is None
            and msg.message_type == "text"
            and bool(GUARDIAN_REVOKE_REGEX.match((msg.text or "").strip()))
        )
        if is_consent_button or is_revoke_text:
            await get_queue().enqueue("command_messages", {
                "sender_id": sender_id,
                "text": msg.text,
                "message_id": msg.message_id,
                "channel": channel.channel_name,
                "interactive_payload": msg.interactive_payload,
                "role": "guardian",
            })
            track_event("guardian_routed", {"sender_id": sender_id, "kind": "button" if is_consent_button else "revoke"})
            return Response(status_code=200)

    if user_role is None:
        # Se for Telegram, ativa a Trava de Segurança (Vinculação)
        if isinstance(channel, TelegramChannel):
            return await handle_telegram_linking(sender_id, msg.text)
        else:
            # WhatsApp
            logger.info("webhook_rejected_unknown_sender", sender_id=sender_id)
            return Response(status_code=200)

    # 2. Rate limiting
    if not await check_rate_limit(sender_id):
        logger.info("webhook_rejected_rate_limited", sender_id=sender_id)
        return Response(status_code=200)

    # Validação de tipo de mensagem
    if msg.message_type == "unknown":
        await channel.send_text(sender_id, UNSUPPORTED_MESSAGE_TYPE)
        return Response(status_code=200)

    # H2.2 / F3: imagem (redação) e áudio (fala/pronúncia) — só de ALUNOS; seguem
    # para o worker com o media_id (o download NUNCA acontece no webhook, SLA 200ms).
    if msg.message_type in ("image", "audio") and user_role != "student":
        await channel.send_text(sender_id, UNSUPPORTED_MESSAGE_TYPE)
        return Response(status_code=200)

    # Removido bloco 3.1 síncrono de banco de dados para não quebrar SLA da Meta (200ms)

    # 4. Command routing (only for text messages)
    if msg.message_type == "text" and msg.text.startswith("/"):
        if user_role != "teacher":
            # ── Comandos de ALUNO (Addon de Livros) ──────────────────────
            # Allowlist explícita via regex pré-compilado (O(µs), sem I/O):
            # /pausar_leitura, /retomar_leitura, /meu_progresso, /flashcards.
            # REGRA DE OURO (SLA Meta 200ms): nada é executado aqui — apenas
            # rotulamos e enfileiramos para o command_worker. Qualquer outro
            # comando de aluno continua rejeitado na borda, como sempre.
            student_match = STUDENT_COMMAND_REGEX.match(msg.text.strip())
            if student_match:
                await get_queue().enqueue("command_messages", {
                    "sender_id": sender_id,
                    "text": msg.text,
                    "message_id": msg.message_id,
                    "channel": channel.channel_name,
                    "role": "student",  # validado acima via cache-aside (Redis)
                })
                track_event("student_command_routed", {
                    "sender_id": sender_id,
                    "command": student_match.group("cmd").lower(),
                })
                return Response(status_code=200)

            await channel.send_text(sender_id, ERROR_UNAUTHORIZED)
            return Response(status_code=200)

        # ── Comandos gerenciais: /comando_[turma]_[arg] ──────────────────
        # Regex pré-compilado e SEM I/O (O(µs)): identifica os comandos
        # /engajamento, /acertos, /risco, /duvidas_frequentes, /ver_aluno,
        # /resetar_aluno, /limpar_quizzes e /aviso e os rotula no payload.
        # REGRA DE OURO (SLA Meta 200ms): NENHUM comando é executado aqui de
        # forma síncrona — o webhook apenas roteia para a fila
        # `command_messages`, onde o command_worker fará o trabalho pesado.
        mgmt_match = MANAGEMENT_COMMAND_REGEX.match(msg.text.strip())

        await get_queue().enqueue("command_messages", {
            "sender_id": sender_id,
            "text": msg.text,
            "message_id": msg.message_id,
            "channel": channel.channel_name,
            "role": "teacher",  # já validado acima via cache-aside (Redis)
            "management_command": mgmt_match.group("cmd").lower() if mgmt_match else None,
        })
        track_event("command_routed", {
            "sender_id": sender_id,
            "management_command": mgmt_match.group("cmd").lower() if mgmt_match else "legacy",
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
            "message_id": msg.message_id,
            "channel": channel.channel_name,
            "media_id": msg.media_id,
        },
        session_id=sender_id # Garantia FIFO local por chat
    )

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
