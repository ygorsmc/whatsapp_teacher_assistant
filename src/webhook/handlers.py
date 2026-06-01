from fastapi import Request, Response, APIRouter, BackgroundTasks
from typing import Optional
from uuid import uuid4
import json
from sqlalchemy import text
from src.shared.config import settings

from src.webhook.security import verify_meta_signature, check_rate_limit
from src.shared.service_bus import service_bus
from src.shared.telemetry import track_event
from src.cache.redis_client import get_redis
from src.db.database import AsyncSessionLocal
from src.shared.whatsapp_interactive import send_whatsapp_message
from src.shared.constants.messages import (
    UNSUPPORTED_MESSAGE_TYPE, ERROR_UNAUTHORIZED, 
    CMD_SYNC_ALUNOS_START, CMD_SYNC_MATERIAIS_START, CMD_SYNC_MATERIAIS_SUCCESS, CMD_SYNC_SUCCESS, CMD_SYNC_ERROR
)
from src.shared.constants.commands import parse_command, CommandType

async def run_sync_alunos_command(phone: str):
    from src.drive.spreadsheet_sync import sync_users_from_sheet
    try:
        stats = await sync_users_from_sheet()
        if "error" in stats:
            await send_whatsapp_message(phone, CMD_SYNC_ERROR.format(error=stats['error']))
        else:
            msg = CMD_SYNC_SUCCESS.format(
                teachers_before=stats.get('teachers_before', 0),
                teachers_after=stats.get('teachers_after', 0),
                students_before=stats.get('students_before', 0),
                students_after=stats.get('students_after', 0),
                courses_before=stats.get('courses_before', 0),
                courses_after=stats.get('courses_after', 0),
                schedules_before=stats.get('schedules_before', 0),
                schedules_after=stats.get('schedules_after', 0)
            )
            await send_whatsapp_message(phone, msg)
    except Exception as e:
        await send_whatsapp_message(phone, f"❌ Erro inesperado: {str(e)[:100]}")

async def run_sync_materiais_command(phone: str):
    from src.workers.ingestion_worker import sync_google_drive
    try:
        stats = await sync_google_drive()
        msg = CMD_SYNC_MATERIAIS_SUCCESS.format(
            docs_before=stats.get('docs_before', 0) if stats else 0,
            docs_after=stats.get('docs_after', 0) if stats else 0
        )
        await send_whatsapp_message(phone, msg)
    except Exception as e:
        await send_whatsapp_message(phone, f"❌ Erro na ingestão: {str(e)[:100]}")

async def run_list_cursos_command(phone: str):
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(text("SELECT id FROM Teachers WHERE phone = :phone"), {"phone": phone})
            teacher = result.fetchone()
            if not teacher:
                await send_whatsapp_message(phone, "❌ Professor não encontrado no sistema.")
                return
            
            result = await session.execute(
                text("SELECT name, id FROM Courses WHERE teacher_id = :tid AND is_active = 1"),
                {"tid": teacher[0]}
            )
            courses = result.fetchall()
            
            if not courses:
                await send_whatsapp_message(phone, "📚 Você não possui cursos cadastrados.")
                return
                
            msg = "📚 *Seus Cursos Cadastrados:*\n\n"
            for idx, c in enumerate(courses, 1):
                msg += f"{idx}. *{c[0]}*\n(ID: `{c[1]}`)\n\n"
            msg += "💡 *Dica:* Para listar os materiais do Drive, digite `/listar_materiais`"
            await send_whatsapp_message(phone, msg)
    except Exception as e:
        await send_whatsapp_message(phone, "❌ Erro ao buscar cursos.")

async def run_list_materiais_command(phone: str, course_query: str):
    from src.drive.client import drive_client
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(text("SELECT id FROM Teachers WHERE phone = :phone"), {"phone": phone})
            teacher = result.fetchone()
            if not teacher:
                await send_whatsapp_message(phone, "❌ Professor não encontrado no sistema.")
                return
                
            folder_id = settings.GOOGLE_DRIVE_FOLDER_ID
            if not folder_id:
                await send_whatsapp_message(phone, "❌ O Google Drive não está configurado.")
                return
                
            files = await drive_client.list_pdf_files(folder_id)
            if not files:
                await send_whatsapp_message(phone, "📂 A pasta do Google Drive está vazia.")
                return
                
            # Fetch statuses from DB
            result = await session.execute(
                text("SELECT file_id, status FROM file_sync_state WHERE teacher_id = :tid"),
                {"tid": teacher[0]}
            )
            statuses = {row[0]: row[1] for row in result.fetchall()}
            
            msg = "📂 *Materiais no Google Drive:*\n\n"
            
            # Filter by course query if provided
            filtered_files = files
            if course_query:
                query_lower = course_query.lower()
                filtered_files = [f for f in files if query_lower in f['name'].lower()]
                msg = f"📂 *Materiais (Filtro: {course_query}):*\n\n"
                
            if not filtered_files:
                msg += "Nenhum material encontrado com esse filtro."
            
            for f in filtered_files:
                status = statuses.get(f['id'], "não sincronizado")
                emoji = "✅" if status == "concluído" else "⏳" if status == "processando" else "❌"
                c_name = f.get('course_name', 'Pasta Raiz')
                msg += f"{emoji} *{f['name']}*\nCurso: {c_name}\nStatus: {status}\n\n"
                
            await send_whatsapp_message(phone, msg[:4000])
    except Exception as e:
        await send_whatsapp_message(phone, f"❌ Erro ao buscar materiais: {str(e)[:100]}")

async def run_update_lesson_command(phone: str, args: str):
    try:
        if "=" not in args:
            await send_whatsapp_message(phone, "❌ Formato inválido. Use: `/aula Nome da Turma = 2`")
            return
            
        parts = args.split("=", 1)
        class_name = parts[0].strip()
        try:
            new_lesson = int(parts[1].strip())
        except ValueError:
            await send_whatsapp_message(phone, "❌ O número da aula deve ser um valor inteiro.")
            return

        async with AsyncSessionLocal() as session:
            result = await session.execute(text("SELECT id FROM Teachers WHERE phone = :phone"), {"phone": phone})
            teacher = result.fetchone()
            if not teacher:
                await send_whatsapp_message(phone, "❌ Professor não encontrado no sistema.")
                return
            
            stmt = text("""
                SELECT c.id, c.current_lesson_number 
                FROM Classes c
                JOIN Courses crs ON c.course_id = crs.id
                WHERE c.name = :cname AND crs.teacher_id = :tid
            """)
            result = await session.execute(stmt, {"cname": class_name, "tid": teacher[0]})
            class_row = result.fetchone()
            
            if not class_row:
                await send_whatsapp_message(phone, f"❌ Turma '{class_name}' não encontrada.")
                return
                
            class_id = class_row[0]
            
            await session.execute(
                text("UPDATE Classes SET current_lesson_number = :num, updated_at = CURRENT_TIMESTAMP WHERE id = :cid"),
                {"num": new_lesson, "cid": class_id}
            )
            await session.commit()
            
            await send_whatsapp_message(phone, f"✅ Turma '{class_name}' atualizada para a aula {new_lesson} com sucesso!")
            
    except Exception as e:
        await send_whatsapp_message(phone, f"❌ Erro ao atualizar aula: {str(e)[:100]}")

router = APIRouter()

async def get_user_role(phone: str) -> Optional[str]:
    """
    Returns 'student' or 'teacher' or None.
    Cache-Aside: Redis first, SQL fallback.
    """
    redis = await get_redis()
    cache_key = f"role:{phone}"
    
    cached = await redis.get(cache_key)
    if cached:
        return cached

    # Fallback to DB
    async with AsyncSessionLocal() as session:
        # Check student
        result = await session.execute(
            text("SELECT 1 FROM Students WHERE phone = :phone AND is_active = 1"),
            {"phone": phone}
        )
        if result.scalar_one_or_none():
            await redis.set(cache_key, "student", ex=86400)
            return "student"
            
        # Check teacher
        result = await session.execute(
            text("SELECT 1 FROM Teachers WHERE phone = :phone AND is_active = 1"),
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

        # 1. Whitelist + role - Cache-Aside
        user_role = await get_user_role(phone)
        if user_role is None:
            return Response(status_code=200)

        # 2. Rate limiting
        if not await check_rate_limit(phone):
            return Response(status_code=200)

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

        # 3.1. Open service window (24h)
        redis = await get_redis()
        await redis.set(f"service_window:{phone}", "1", ex=86400)

        # 4. Command routing (only for text messages)
        if message_type == "text" and text.startswith("/"):
            if user_role != "teacher":
                await send_whatsapp_message(phone, ERROR_UNAUTHORIZED)
                return Response(status_code=200)
                
            command, args = parse_command(text)
            
            if command == CommandType.SYNC_STUDENTS:
                background_tasks.add_task(run_sync_alunos_command, phone)
                await send_whatsapp_message(phone, CMD_SYNC_ALUNOS_START)
                return Response(status_code=200)
                
            elif command == CommandType.SYNC_MATERIALS:
                background_tasks.add_task(run_sync_materiais_command, phone)
                await send_whatsapp_message(phone, CMD_SYNC_MATERIAIS_START)
                return Response(status_code=200)
                
            elif command == CommandType.LIST_COURSES:
                background_tasks.add_task(run_list_cursos_command, phone)
                return Response(status_code=200)
                
            elif command == CommandType.LIST_MATERIALS:
                background_tasks.add_task(run_list_materiais_command, phone, args)
                return Response(status_code=200)
                
            elif command == CommandType.UPDATE_LESSON:
                background_tasks.add_task(run_update_lesson_command, phone, args)
                return Response(status_code=200)
                
            elif command == CommandType.HELP:
                await send_whatsapp_message(phone, "Comandos disponíveis:\n/sinc_alunos\n/sinc_materiais\n/listar_cursos\n/listar_materiais\n/aula [turma] = [numero]")
                return Response(status_code=200)
                
            # Fallback to Service Bus queue for any future asynchronous workers
            await service_bus.enqueue("command_messages", {
                "phone": phone,
                "text": text,
                "message_id": msg_id
            })
            return Response(status_code=200)

        # 5. Idempotency lock
        lock_key = f"webhook:{msg_id}"
        is_new = await redis.set(lock_key, "1", nx=True, ex=300)
        if not is_new:
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

        return Response(status_code=200)

    except Exception as e:
        track_event("webhook_error", {"error": str(e)})
        # Best effort error response to user
        try:
            # Assumes 'phone' is bound in locals if it failed late
            if 'phone' in locals():
                await send_whatsapp_message(phone, "Ocorreu um erro ao processar sua mensagem, tente novamente.")
        except Exception:
            pass
        
        return Response(status_code=200)
