import asyncio
import json
import structlog
from azure.servicebus.aio import ServiceBusClient, AutoLockRenewer
from src.shared.config import settings
from src.shared.telemetry import track_event, logger
from src.shared.whatsapp_interactive import send_whatsapp_message
from src.db.database import AsyncSessionLocal
from sqlalchemy import text
from src.shared.constants.messages import (
    CMD_SYNC_ALUNOS_START, CMD_SYNC_MATERIAIS_START, CMD_SYNC_MATERIAIS_SUCCESS, CMD_SYNC_SUCCESS, CMD_SYNC_ERROR
)
from src.shared.constants.commands import parse_command, CommandType

logger = structlog.get_logger(__name__)

async def run_sync_alunos_command(phone: str):
    from src.drive.spreadsheet_sync import sync_users_from_sheet
    try:
        await send_whatsapp_message(phone, CMD_SYNC_ALUNOS_START)
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
        await send_whatsapp_message(phone, CMD_SYNC_MATERIAIS_START)
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

async def process_command_message(message_body: dict):
    phone = message_body.get("phone")
    text_content = message_body.get("text", "")
    
    command, args = parse_command(text_content)
    logger.info("process_command_message", phone=phone, command=command, args=args)
    
    if command == CommandType.SYNC_STUDENTS:
        await run_sync_alunos_command(phone)
    elif command == CommandType.SYNC_MATERIALS:
        await run_sync_materiais_command(phone)
    elif command == CommandType.LIST_COURSES:
        await run_list_cursos_command(phone)
    elif command == CommandType.LIST_MATERIALS:
        await run_list_materiais_command(phone, args)
    elif command == CommandType.UPDATE_LESSON:
        await run_update_lesson_command(phone, args)
    elif command == CommandType.HELP:
        await send_whatsapp_message(phone, "Comandos disponíveis:\n/sinc_alunos\n/sinc_materiais\n/listar_cursos\n/listar_materiais\n/aula [turma] = [numero]")
    else:
        await send_whatsapp_message(phone, "❌ Comando desconhecido.")

async def command_loop():
    logger.info("main_worker_starting", environment=settings.ENVIRONMENT, worker_type="command")
    
    if not settings.SERVICEBUS_CONNECTION_STRING:
        logger.error("SERVICEBUS_CONNECTION_STRING is missing")
        return

    # Standard queue receiver (not session-enabled for commands)
    async with ServiceBusClient.from_connection_string(settings.SERVICEBUS_CONNECTION_STRING, keep_alive_interval=30) as client:
        receiver = client.get_queue_receiver(queue_name="command_messages", prefetch_count=10)
        async with receiver:
            logger.info("command_worker_listening", queue="command_messages")
            while True:
                try:
                    messages = await receiver.receive_messages(max_wait_time=5, max_message_count=10)
                    for message in messages:
                        try:
                            body_bytes = b"".join(message.body)
                            payload = json.loads(body_bytes.decode('utf-8'))
                            await process_command_message(payload)
                            await receiver.complete_message(message)
                        except Exception as e:
                            logger.error("error_processing_command", error=str(e))
                            # Depending on requirements, we can deadletter it or just complete it to prevent poison loops
                            await receiver.dead_letter_message(message, reason="Processing Error", error_description=str(e)[:500])
                except Exception as e:
                    logger.error("command_worker_loop_error", error=str(e))
                    await asyncio.sleep(5)
