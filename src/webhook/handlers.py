from fastapi import Request, Response, APIRouter, BackgroundTasks
from typing import Optional
from uuid import uuid4
import json
import asyncio
from sqlalchemy import text as sql_text
from src.shared.config import settings

from src.webhook.security import verify_meta_signature, check_rate_limit
from src.shared.service_bus import service_bus
from src.shared.telemetry import track_event
from src.cache.redis_client import get_redis
from src.db.database import AsyncSessionLocal
from src.shared.whatsapp_interactive import send_whatsapp_message
from src.shared.constants.messages import (
    UNSUPPORTED_MESSAGE_TYPE, ERROR_UNAUTHORIZED
)
from src.shared.constants.commands import parse_command, CommandType

router = APIRouter()

async def get_user_role(phone: str) -> Optional[str]:
    """
    Returns 'student' or 'teacher' or None.
    Cache-Aside: Redis first, SQL fallback.
    """
    redis = await get_redis()
    cache_key = f"role:{phone}"
    
    # --- HARDCODED ADMIN (First setup) ---
    if phone == "5513974033349":
        return "teacher"
    # -------------------------------------

    cached = await redis.get(cache_key)
    if cached:
        return cached

    # Fallback to DB
    async with AsyncSessionLocal() as session:
        # Check student
        result = await session.execute(
            sql_text("SELECT 1 FROM Students WHERE phone = :phone AND is_active = 1"),
            {"phone": phone}
        )
        if result.scalar_one_or_none():
            await redis.set(cache_key, "student", ex=86400)
            return "student"
            
        # Check teacher
        result = await session.execute(
            sql_text("SELECT 1 FROM Teachers WHERE phone = :phone AND is_active = 1"),
            {"phone": phone}
        )
        if result.scalar_one_or_none():
            await redis.set(cache_key, "teacher", ex=86400)
            return "teacher"
            
    return None

@router.get("/v1/webhook")
@router.post("/v1/webhook")
async def handle_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Main webhook handler for Meta's WhatsApp API.
    SLA < 200ms. No LLM processing here.
    """
    try:
        body_bytes = await request.body()
        
        # Meta validation challenge
        if "hub.mode" in request.query_params:
            if request.query_params.get("hub.verify_token") != settings.META_VERIFY_TOKEN:
                return Response(content="Verification failed", status_code=403)
            return Response(content=request.query_params.get("hub.challenge"), media_type="text/plain")

        # 0. Signature validation
        signature = request.headers.get("X-Hub-Signature-256", "")
        if not await verify_meta_signature(body_bytes, signature):
            print(f"[DEBUG] Signature validation failed. Expected match with META_APP_SECRET.")
            track_event("webhook_signature_failed")
            return Response(status_code=200) # Silent drop

        body = json.loads(body_bytes)
        print(f"\n[DEBUG] WEBHOOK PAYLOAD: {json.dumps(body, indent=2)}\n")

        # Parse message (Simplified for this example; Meta payload is deeply nested)
        try:
            entry = body.get("entry", [{}])[0]
            changes = entry.get("changes", [{}])[0]
            value = changes.get("value", {})
            if "messages" not in value:
                print("[DEBUG] No 'messages' found in payload. Returning 200.")
                return Response(status_code=200)
                
            msg_data = value["messages"][0]
            phone = msg_data["from"]
            msg_type = msg_data["type"]
            msg_id = msg_data["id"]
            
            # Extract text safely
            text = msg_data.get("text", {}).get("body", "") if msg_type == "text" else ""
            
        except (KeyError, IndexError):
            return Response(status_code=200)

        # 0.5. Global Idempotency lock (prevent Meta retries from processing twice)
        lock_key = f"webhook:{msg_id}"
        redis = await get_redis()
        is_new = await redis.set(lock_key, "1", nx=True, ex=300)
        if not is_new:
            print(f"[DIAG] Duplicate webhook dropped: {msg_id}")
            return Response(status_code=200)

        # 1. Whitelist + role - Cache-Aside
        print(f"[DIAG] Step 1: get_user_role for {phone}")
        user_role = await get_user_role(phone)
        print(f"[DIAG] Step 1 result: role={user_role}")
        if user_role is None:
            print(f"[DIAG] REJECTED: user_role is None for {phone}")
            return Response(status_code=200)

        # 2. Rate limiting
        print(f"[DIAG] Step 2: check_rate_limit for {phone}")
        if not await check_rate_limit(phone):
            print(f"[DIAG] REJECTED: rate limited {phone}")
            return Response(status_code=200)
        print(f"[DIAG] Step 2 passed")

        # 3. Message type filter and interactive parsing
        if msg_type == "interactive":
            interactive = msg_data.get("interactive", {})
            if interactive.get("type") == "button_reply":
                interactive_payload = {
                    "type": "button_reply",
                    "id": interactive["button_reply"]["id"],
                    "title": interactive["button_reply"]["title"],
                }
            elif interactive.get("type") == "list_reply":
                interactive_payload = {
                    "type": "list_reply",
                    "id": interactive["list_reply"]["id"],
                    "title": interactive["list_reply"]["title"],
                    "description": interactive["list_reply"].get("description", ""),
                }
            else:
                return Response(status_code=200) # Unknown interactive type
                
            text = interactive_payload["title"] # Text fallback
            message_type = "interactive"
            
        elif msg_type == "text":
            interactive_payload = None
            text = msg_data.get("text", {}).get("body", "").strip()
            message_type = "text"
            
        else:
            await send_whatsapp_message(phone, UNSUPPORTED_MESSAGE_TYPE)
            return Response(status_code=200)

        # 3.1. Log interaction in DB (Serves as 24h window for inbound messages)
        async with AsyncSessionLocal() as session:
            res = await session.execute(sql_text("SELECT id FROM Students WHERE phone = :p"), {"p": phone})
            student_row = res.fetchone()
            if student_row:
                student_id = student_row[0]
                interaction_type = "quiz_answered" if message_type == "interactive" and interactive_payload and interactive_payload.get("id", "").startswith("quiz_") else "message_received"
                
                await session.execute(
                    sql_text("""
                        INSERT INTO StudentInteractions (id, student_id, interaction_type, message_content) 
                        VALUES (:id, :sid, :itype, :msg)
                    """),
                    {
                        "id": str(uuid4()),
                        "sid": student_id,
                        "itype": interaction_type,
                        "msg": text
                    }
                )
                await session.commit()
        # 4. Command routing (only for text messages)
        print(f"[DIAG] Step 4: command routing. message_type={message_type}, text={text[:50]}")
        if message_type == "text" and text.startswith("/"):
            print(f"[DIAG] Step 4a: is command. user_role={user_role}")
            if user_role != "teacher":
                print(f"[DIAG] REJECTED: not teacher for command")
                await send_whatsapp_message(phone, ERROR_UNAUTHORIZED)
                return Response(status_code=200)
                
            # Route all commands directly to the Service Bus command_messages queue
            print(f"[DIAG] Step 4b: Enqueueing command to Service Bus")
            await service_bus.enqueue("command_messages", {
                "phone": phone,
                "text": text,
                "message_id": msg_id
            })
            return Response(status_code=200)

        # 6. Enqueue to Inbound Messages with session FIFO
        correlation_id = str(uuid4())
        await service_bus.enqueue(
            queue="inbound_messages",
            payload={
                "phone": phone,
                "text": text,
                "message_type": message_type,
                "interactive_payload": interactive_payload,
                "correlation_id": correlation_id,
                "message_id": msg_id
            },
            session_id=phone
        )

        # 7. WAKE UP HACK FOR KEDA
        # Session Queues (inbound_messages) take 3 minutes to trigger KEDA scale up due to Azure Monitor.
        # command_messages is a normal queue and triggers in 15 seconds via Connection String.
        # By dropping an empty wakeup ping, we instantly turn on the worker!
        await service_bus.enqueue("command_messages", {
            "phone": phone,
            "text": "/wakeup",
            "message_id": f"wakeup_{msg_id}"
        })

        return Response(status_code=200)

    except Exception as e:
        import traceback
        print(f"\n[WEBHOOK EXCEPTION] {e}")
        traceback.print_exc()
        track_event("webhook_error", {"error": str(e)})
        # Best effort error response to user
        try:
            # Assumes 'phone' is bound in locals if it failed late
            if 'phone' in locals():
                await send_whatsapp_message(phone, "Ocorreu um erro ao processar sua mensagem, tente novamente.")
        except Exception:
            pass
        
        return Response(status_code=200)
