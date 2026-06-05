from src.queue.interfaces import MessageContext
from src.shared.config import settings
from src.channel.factory import get_channel
from src.shared.telemetry import logger
from src.db.database import AsyncSessionLocal
from sqlalchemy import text
from src.shared.constants.messages import (
    CMD_SYNC_ALUNOS_START, CMD_SYNC_MATERIAIS_START, CMD_SYNC_MATERIAIS_SUCCESS, CMD_SYNC_SUCCESS, CMD_SYNC_ERROR
)
from src.shared.constants.commands import parse_command, CommandType



async def run_sync_alunos_command(channel, sender_id: str):
    from src.drive.spreadsheet_sync import sync_users_from_sheet
    try:
        await channel.send_text(sender_id, CMD_SYNC_ALUNOS_START)
        stats = await sync_users_from_sheet()
        if "error" in stats:
            await channel.send_text(sender_id, CMD_SYNC_ERROR.format(error=stats['error']))
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
            await channel.send_text(sender_id, msg)
    except Exception as e:
        await channel.send_text(sender_id, f"❌ Erro inesperado: {str(e)[:100]}")

async def run_sync_materiais_command(channel, sender_id: str):
    from src.workers.ingestion_worker import sync_google_drive
    try:
        await channel.send_text(sender_id, CMD_SYNC_MATERIAIS_START)
        stats = await sync_google_drive()
        msg = CMD_SYNC_MATERIAIS_SUCCESS.format(
            docs_before=stats.get('docs_before', 0) if stats else 0,
            docs_after=stats.get('docs_after', 0) if stats else 0
        )
        await channel.send_text(sender_id, msg)
    except Exception as e:
        await channel.send_text(sender_id, f"❌ Erro na ingestão: {str(e)[:100]}")

async def run_list_cursos_command(channel, sender_id: str):
    try:
        async with AsyncSessionLocal() as session:
            query_col = channel.get_user_query_column()
            result = await session.execute(text(f'SELECT id FROM \"Teachers\" WHERE {query_col} = :sid'), {"sid": sender_id})
            teacher = result.fetchone()
            if not teacher:
                await channel.send_text(sender_id, "❌ Professor não encontrado no sistema.")
                return
            
            result = await session.execute(
                text('SELECT name, id FROM \"Courses\" WHERE teacher_id = :tid AND is_active = true'),
                {"tid": teacher[0]}
            )
            courses = result.fetchall()
            
            if not courses:
                await channel.send_text(sender_id, "📚 Você não possui cursos cadastrados.")
                return
                
            msg = "📚 *Seus Cursos Cadastrados:*\n\n"
            for idx, c in enumerate(courses, 1):
                msg += f"{idx}. *{c[0]}*\n(ID: `{c[1]}`)\n\n"
            msg += "💡 *Dica:* Para listar os materiais do Drive, digite `/listar_materiais`"
            await channel.send_text(sender_id, msg)
    except Exception:
        await channel.send_text(sender_id, "❌ Erro ao buscar cursos.")

async def run_list_materiais_command(channel, sender_id: str, course_query: str):
    from src.drive.client import drive_client
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(text('SELECT id FROM \"Teachers\" WHERE phone = :phone'), {"phone": sender_id})
            teacher = result.fetchone()
            if not teacher:
                await channel.send_text(sender_id, "❌ Professor não encontrado no sistema.")
                return
                
            folder_id = settings.GOOGLE_DRIVE_FOLDER_ID
            if not folder_id:
                await channel.send_text(sender_id, "❌ O Google Drive não está configurado.")
                return
                
            files = await drive_client.list_course_files(folder_id)
            if not files:
                await channel.send_text(sender_id, "📂 A pasta do Google Drive está vazia.")
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
                
            await channel.send_text(sender_id, msg[:4000])
    except Exception as e:
        await channel.send_text(sender_id, f"❌ Erro ao buscar materiais: {str(e)[:100]}")

async def run_update_lesson_command(channel, sender_id: str, args: str):
    try:
        if "=" not in args:
            await channel.send_text(sender_id, "❌ Formato inválido. Use: `/aula Nome da Turma = 2`")
            return
            
        parts = args.split("=", 1)
        class_name = parts[0].strip()
        try:
            new_lesson = int(parts[1].strip())
        except ValueError:
            await channel.send_text(sender_id, "❌ O número da aula deve ser um valor inteiro.")
            return

        async with AsyncSessionLocal() as session:
            result = await session.execute(text('SELECT id FROM \"Teachers\" WHERE phone = :phone'), {"phone": sender_id})
            teacher = result.fetchone()
            if not teacher:
                await channel.send_text(sender_id, "❌ Professor não encontrado no sistema.")
                return
            
            stmt = text("""
                SELECT c.id, c.current_lesson_number 
                FROM "Classes" c
                JOIN "Courses" crs ON c.course_id = crs.id
                WHERE c.name = :cname AND crs.teacher_id = :tid
            """)
            result = await session.execute(stmt, {"cname": class_name, "tid": teacher[0]})
            class_row = result.fetchone()
            
            if not class_row:
                await channel.send_text(sender_id, f"❌ Turma '{class_name}' não encontrada.")
                return
                
            class_id = class_row[0]
            
            await session.execute(
                text('UPDATE \"Classes\" SET current_lesson_number = :num, updated_at = CURRENT_TIMESTAMP WHERE id = :cid'),
                {"num": new_lesson, "cid": class_id}
            )
            await session.commit()
            
            await channel.send_text(sender_id, f"✅ Turma '{class_name}' atualizada para a aula {new_lesson} com sucesso!")
            
    except Exception as e:
        await channel.send_text(sender_id, f"❌ Erro ao atualizar aula: {str(e)[:100]}")

async def run_broadcast_quiz_command(channel, sender_id: str, class_name: str):
    try:
        # Removendo '_' caso o professor digite /quiz_1
        if class_name.startswith("_"):
            class_name = class_name[1:]
            
        if not class_name:
            await channel.send_text(sender_id, "❌ Informe a turma. Exemplo: `/quiz 1` ou `/quiz_1`")
            return
            
        async with AsyncSessionLocal() as session:
            # 1. Fetch Teacher
            query_col = channel.get_user_query_column()
            result = await session.execute(text(f'SELECT id FROM \"Teachers\" WHERE {query_col} = :sid'), {"sid": sender_id})
            teacher = result.fetchone()
            if not teacher:
                await channel.send_text(sender_id, "❌ Professor não encontrado no sistema.")
                return
                
            # 2. Fetch Class & Course
            stmt = text("""
                SELECT c.id, c.current_lesson_number, crs.id as course_id, c.name
                FROM "Classes" c
                JOIN "Courses" crs ON c.course_id = crs.id
                WHERE c.name = :cname AND crs.teacher_id = :tid
            """)
            result = await session.execute(stmt, {"cname": class_name, "tid": teacher[0]})
            class_row = result.fetchone()
            
            if not class_row:
                await channel.send_text(sender_id, f"❌ Turma '{class_name}' não encontrada.")
                return
                
            class_id = class_row[0]
            current_lesson = class_row[1]
            course_id = class_row[2]
            c_name = class_row[3]
            
            # 3. Fetch Enrolled Active Students
            stmt_students = text("""
                SELECT s.id, s.phone, s.name 
                FROM "Students" s
                JOIN "StudentEnrollment" se ON s.id = se.student_id
                WHERE se.class_id = :cid AND s.is_active = true
            """)
            result = await session.execute(stmt_students, {"cid": class_id})
            students = result.fetchall()
            
            if not students:
                await channel.send_text(sender_id, f"❌ Nenhum aluno ativo encontrado na turma '{c_name}'.")
                return
                
        # Send a loading message to teacher
        await channel.send_text(sender_id, f"⏳ Gerando quiz para a aula {current_lesson} da turma {c_name}...\nIsso pode levar alguns segundos.")
        
        # 4. Generate Quiz
        from src.rag.retriever import retrieve_context
        from src.rag.quiz_generator import generate_quiz
        
        lesson_content = await retrieve_context(
            teacher_id=teacher[0],
            course_id=course_id,
            current_lesson_number=current_lesson,
            query=f"resumo principal da aula {current_lesson}",
            top_k=5,
            score_threshold=0.3
        )
        
        if not lesson_content:
            await channel.send_text(sender_id, f"❌ Nenhum material encontrado no banco de dados para a aula {current_lesson}.")
            return
            
        quiz = await generate_quiz(
            course_id=course_id,
            lesson_number=current_lesson,
            lesson_content=lesson_content,
            num_alternatives=3
        )
        
        if not quiz:
            await channel.send_text(sender_id, "❌ Falha ao gerar o quiz com a IA.")
            return
            
        # 5. Broadcast quiz to all students
        from src.workers.micro_learning import send_quiz_to_student
        success_count = 0
        
        for st in students:
            try:
                await send_quiz_to_student(
                    channel=channel,
                    student={
                        "id": st[0],
                        "sender_id": st[1],
                        "course_id": course_id,
                        "lesson_pointer": str(current_lesson)
                    },
                    quiz=quiz
                )
                success_count += 1
            except Exception as e:
                logger.error("failed_to_send_quiz_to_student", student_id=st[0], error=str(e))
                
        await channel.send_text(sender_id, f"✅ Quiz enviado com sucesso para {success_count} aluno(s) da turma '{c_name}'!")
        
    except Exception as e:
        await channel.send_text(sender_id, f"❌ Erro ao enviar quiz: {str(e)[:100]}")

async def process_command_message(message_body: dict):
    sender_id = str(message_body.get("sender_id", message_body.get("phone", "")))
    text_content = message_body.get("text", "")
    channel = get_channel()
    
    command, args = parse_command(text_content)
    logger.info("process_command_message", sender_id=sender_id, command=command, args=args)
    
    if command == CommandType.SYNC_STUDENTS:
        await run_sync_alunos_command(channel, sender_id)
    elif command == CommandType.SYNC_MATERIALS:
        await run_sync_materiais_command(channel, sender_id)
    elif command == CommandType.LIST_COURSES:
        await run_list_cursos_command(channel, sender_id)
    elif command == CommandType.LIST_MATERIALS:
        await run_list_materiais_command(channel, sender_id, args)
    elif command == CommandType.UPDATE_LESSON:
        await run_update_lesson_command(channel, sender_id, args)
    elif command == CommandType.SEND_QUIZ:
        await run_broadcast_quiz_command(channel, sender_id, args)
    elif command == CommandType.HELP:
        await channel.send_text(sender_id, "Comandos disponíveis:\n/sinc_alunos\n/sinc_materiais\n/listar_cursos\n/listar_materiais\n/aula [turma] = [numero]\n/quiz_[turma]")
    elif command == CommandType.WAKEUP:
        logger.info("waking_up_worker_via_hack")
        return
    else:
        await channel.send_text(sender_id, "❌ Comando desconhecido.")

async def command_loop():
    logger.info("main_worker_starting", environment=settings.ENVIRONMENT, worker_type="command")
    
    from src.queue.factory import get_queue
    
    async def handler(payload: dict, ctx: MessageContext):
        try:
            await process_command_message(payload)
            await ctx.complete()
        except Exception as e:
            logger.error("error_processing_command", error=str(e))
            await ctx.dead_letter(reason="Processing Error", description=str(e)[:500])
            
    queue = get_queue()
    await queue.consume_loop("command_messages", handler, use_sessions=False)
