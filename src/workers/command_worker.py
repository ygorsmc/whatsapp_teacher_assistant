import re
import json
from src.queue.interfaces import MessageContext
from datetime import datetime, timezone, timedelta
from src.shared.config import settings
from src.channel.factory import get_channel
from src.shared.telemetry import logger
from src.db.database import AsyncSessionLocal
from src.cache.redis_client import get_redis
from sqlalchemy import text
from src.shared.constants.messages import (
    CMD_SYNC_ALUNOS_START, CMD_SYNC_SUCCESS, CMD_SYNC_ERROR,
    CMD_SYNC_UNLINKED_WARNING, CMD_SYNC_NO_TEACHER_WARNING, CMD_SYNC_DISCIPLINA_WARNING,
    CMD_HELP_TEXT, CMD_CLASS_NOT_FOUND, CMD_TEACHER_NOT_FOUND, CMD_AVISO_USAGE,
    CMD_STUDENT_PHONE_USAGE, CMD_STUDENT_NOT_FOUND, CMD_STUDENT_NOT_OWNED,
    CMD_DELETE_USAGE, CMD_DELETE_NEED_LIST, CMD_DELETE_NOT_FOUND, CMD_DELETE_EMPTY,
    CMD_DELETE_CONFIRM, CMD_DELETE_CONFIRM_YES, CMD_DELETE_CONFIRM_NO,
    CMD_DELETE_EXPIRED, CMD_DELETE_CANCELLED, CMD_DELETE_SUCCESS,
    CMD_STUDENT_HELP_HEADER, CMD_STUDENT_HELP_LANGUAGE, CMD_STUDENT_HELP_READING,
    CMD_STUDENT_HELP_GAMING, CMD_STUDENT_HELP_GRADE, CMD_STUDENT_HELP_FOOTER,
)
from src.shared.constants.commands import (
    parse_command, parse_management_command, parse_student_command,
    parse_engagement_days, CommandType,
)


# ==============================================================================
# HELPERS DE RESOLUÇÃO E POSSE
# ==============================================================================

async def _resolve_teacher(session, channel, sender_id: str, extra_fields: str = ""):
    """Resolve o professor pelo identificador do canal (phone/telegram_chat_id),
    com "self-heal" do vínculo via sufixo do telefone — mesmo padrão dos
    comandos legados (/aula, /quiz). Retorna a Row ou None.

    Campos retornados: id, is_admin[, extra_fields...]
    """
    query_col = channel.get_user_query_column()
    fields = "id, is_admin" + (f", {extra_fields}" if extra_fields else "")

    result = await session.execute(
        text(f'SELECT {fields} FROM "Teachers" WHERE {query_col} = :sid'), {"sid": sender_id}
    )
    teacher = result.fetchone()

    if not teacher:
        res_stu = await session.execute(
            text(f'SELECT phone FROM "Students" WHERE {query_col} = :sid'), {"sid": sender_id}
        )
        stu_phone = res_stu.scalar_one_or_none()
        if stu_phone:
            phone_suffix = stu_phone[-8:] if len(stu_phone) >= 8 else stu_phone
            res_tea = await session.execute(
                text(f'SELECT {fields} FROM "Teachers" WHERE phone LIKE :phone_like'),
                {"phone_like": f"%{phone_suffix}"}
            )
            teacher = res_tea.fetchone()
            if teacher:
                await session.execute(
                    text(f'UPDATE "Teachers" SET {query_col} = :sid WHERE id = :tid'),
                    {"sid": sender_id, "tid": teacher[0]}
                )
                await session.commit()

    return teacher


async def _resolve_owned_class(session, teacher_id: str, is_admin: bool, class_query: str):
    """Resolve a turma por nome (ILIKE) GARANTINDO a posse (Classes.teacher_id),
    a menos que o professor seja admin. Esta é a validação de segurança central
    de TODOS os comandos gerenciais por turma.

    Retorna Row(id, name, course_id, subject_id, current_lesson_number,
    subject_name, is_language, proficiency_level) ou None. (As 2 últimas — vertical
    de Idiomas — são apêndices; os demais comandos usam só os índices 0–5.)
    """
    base_sql = """
        SELECT c.id, c.name, c.course_id, c.subject_id, c.current_lesson_number,
               sub.name AS subject_name, c.is_language, c.proficiency_level
        FROM "Classes" c
        LEFT JOIN "Subjects" sub ON sub.id = c.subject_id
        WHERE c.name ILIKE '%' || :cname || '%'
    """
    if is_admin:
        result = await session.execute(text(base_sql), {"cname": class_query})
    else:
        result = await session.execute(
            text(base_sql + " AND c.teacher_id = :tid"),
            {"cname": class_query, "tid": teacher_id}
        )
    return result.fetchone()


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_naive(dt):
    """Normaliza timestamps vindos do banco (str/aware) para naive UTC."""
    if dt is None:
        return None
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt


# ==============================================================================
# COMANDOS LEGADOS
# ==============================================================================

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
                subjects_before=stats.get('subjects_before', 0),
                subjects_after=stats.get('subjects_after', 0),
                schedules_before=stats.get('schedules_before', 0),
                schedules_after=stats.get('schedules_after', 0)
            )
            # Avisa o professor sobre alunos com agendamento mas sem vínculo no canal
            # (no Telegram: telegram_chat_id NULL) — esses NÃO recebem quizzes.
            unlinked_names = stats.get('unlinked_names', [])
            if unlinked_names:
                names_str = "\n".join(f"• {n}" for n in unlinked_names)
                msg += CMD_SYNC_UNLINKED_WARNING.format(count=len(unlinked_names), names=names_str, bot=settings.BOT.replace("_", "\\_"))
            # Avisa o professor sobre turmas sem dono explícito na aba "Professores"
            # (caíram no Admin) — /quiz e /aula não vão achá-las pelo professor real.
            no_teacher = stats.get('classes_sem_professor', [])
            if no_teacher:
                names_str = "\n".join(f"• {n}" for n in no_teacher)
                msg += CMD_SYNC_NO_TEACHER_WARNING.format(count=len(no_teacher), names=names_str)
            # Avisa sobre turmas com disciplina divergente entre as abas Alunos e Turmas
            # (vale a da aba Turmas) — uma turma deve ter UMA disciplina.
            divergente = stats.get('disciplina_divergente', [])
            if divergente:
                names_str = "\n".join(f"• {n}" for n in divergente)
                msg += CMD_SYNC_DISCIPLINA_WARNING.format(count=len(divergente), names=names_str)
            await channel.send_text(sender_id, msg)
    except Exception as e:
        await channel.send_text(sender_id, f"❌ Erro inesperado: {str(e)[:100]}")

async def run_sync_materiais_command(channel, sender_id: str):
    from src.workers.ingestion_worker import sync_google_drive
    try:
        await channel.send_text(sender_id, "⏳ Verificando o Google Drive por novos materiais. Arquivos pendentes serão colocados na Fila de Ingestão e processados em background...")
        await sync_google_drive()

        msg = "✅ Varredura concluída! Novos arquivos identificados foram enviados para a Fila de Processamento (limite de 2 simultâneos na nuvem).\n\nUse /listar_materiais para acompanhar o status."
        await channel.send_text(sender_id, msg)
    except Exception as e:
        await channel.send_text(sender_id, f"❌ Erro na varredura: {str(e)[:100]}")

async def run_list_cursos_command(channel, sender_id: str):
    try:
        async with AsyncSessionLocal() as session:
            teacher = await _resolve_teacher(session, channel, sender_id)
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return
            
            teacher_id = teacher[0]
            is_admin = teacher[1]
            
            # Posse por turma: lista cursos em que o professor é dono de pelo menos uma turma,
            # exceto se for admin (lista tudo).
            if is_admin:
                result = await session.execute(
                    text('''SELECT DISTINCT crs.name, crs.id
                            FROM "Courses" crs
                            JOIN "Classes" cl ON cl.course_id = crs.id
                            WHERE crs.is_active = true''')
                )
            else:
                result = await session.execute(
                    text('''SELECT DISTINCT crs.name, crs.id
                            FROM "Courses" crs
                            JOIN "Classes" cl ON cl.course_id = crs.id
                            WHERE cl.teacher_id = :tid AND crs.is_active = true'''),
                    {"tid": teacher_id}
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

async def run_list_classes_command(channel, sender_id: str):
    try:
        async with AsyncSessionLocal() as session:
            teacher = await _resolve_teacher(session, channel, sender_id)
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return
            
            teacher_id = teacher[0]
            is_admin = teacher[1]
            
            query = """
                SELECT cl.name as class_name, crs.name as course_name, 
                       cl.current_lesson_number, cl.start_date, cl.schedule_map
                FROM "Classes" cl
                JOIN "Courses" crs ON cl.course_id = crs.id
                WHERE crs.is_active = true
            """
            params = {}
            if not is_admin:
                query += " AND cl.teacher_id = :tid"
                params["tid"] = teacher_id
                
            query += " ORDER BY crs.name, cl.name"
            
            result = await session.execute(text(query), params)
            classes = result.fetchall()
            
            if not classes:
                await channel.send_text(sender_id, "📚 Você não possui turmas cadastradas ou ativas.")
                return
                
            msg = "📊 *Status das Suas Turmas:*\n\n"
            for c in classes:
                lesson = c.current_lesson_number
                start_date = c.start_date.strftime('%d/%m/%Y') if c.start_date else 'N/A'
                
                # Se for aula 0, significa que não iniciou
                status = f"Aula atual: {lesson}" if lesson > 0 else "Aguardando início (Aula 0)"
                
                msg += f"🏫 *Turma {c.class_name}* ({c.course_name})\n"
                msg += f"├ Início: {start_date}\n"
                msg += f"└ Status: {status}\n\n"
                
            await channel.send_text(sender_id, msg)
    except Exception as e:
        logger.error("error_listing_classes", error=str(e))
        await channel.send_text(sender_id, "❌ Erro ao buscar turmas.")

# ── /listar_materiais + /delete (exclusão de material indexado) ──────────────
# A lista numerada memoriza, no Redis, o vínculo  nº → arquivo  (TTL 1h). O
# professor lida só com o número; por baixo, o /delete apaga por `file_id` do
# Drive (à prova de homônimos), com fallback para `source`+escopo nos vetores
# legados (ingeridos antes do file_id no payload). TODO filtro inclui teacher_id.

_MATERIALS_INDEX_TTL = 3600        # validade da numeração do /listar_materiais
_DELETE_PENDING_TTL = 300          # validade da confirmação do /delete

_SECTION_LABELS = {"lesson": "Aula", "book": "Cap.", "workbook": "Módulo"}


def _materials_index_key(teacher_id: str) -> str:
    return f"materials_index:{teacher_id}"


def _delete_pending_key(sender_id: str) -> str:
    return f"material_delete_pending:{sender_id}"


def _section_label(material_type: str, lesson_number: int) -> str:
    return f"{_SECTION_LABELS.get(material_type, 'Seção')} {lesson_number}"


def _material_path(course_name, subject_name, material_type, lesson_number, name) -> str:
    """Caminho lógico legível: Curso › Disciplina › <Seção> › arquivo."""
    parts = [p for p in (course_name, subject_name,
                         _section_label(material_type, lesson_number), name) if p]
    return " › ".join(parts)


def _material_primary_filter(file_id: str, teacher_id: str):
    """Filtro preciso por file_id (vetores novos) — sempre escopado por teacher."""
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    must: list = [
        FieldCondition(key="file_id", match=MatchValue(value=file_id)),
        FieldCondition(key="teacher_id", match=MatchValue(value=teacher_id)),
    ]
    return Filter(must=must)


def _material_legacy_filter(ref: dict, teacher_id: str):
    """Fallback por source+escopo (vetores legados, sem file_id no payload).
    Mantém o isolamento por teacher_id+course_id (regra inviolável)."""
    from qdrant_client.models import Filter, FieldCondition, MatchValue
    must: list = [
        FieldCondition(key="source", match=MatchValue(value=ref["source"])),
        FieldCondition(key="teacher_id", match=MatchValue(value=teacher_id)),
    ]
    if ref.get("course_id"):
        must.append(FieldCondition(key="course_id", match=MatchValue(value=ref["course_id"])))
    if ref.get("subject_name"):
        must.append(FieldCondition(key="subject_name", match=MatchValue(value=ref["subject_name"])))
    if ref.get("lesson_number") is not None:
        must.append(FieldCondition(key="lesson_number", match=MatchValue(value=ref["lesson_number"])))
    if ref.get("material_type"):
        must.append(FieldCondition(key="material_type", match=MatchValue(value=ref["material_type"])))
    return Filter(must=must)


async def _count_material_chunks(ref: dict, teacher_id: str) -> tuple[int, str]:
    """Conta os trechos do arquivo. Tenta por file_id; se 0 (vetores legados),
    conta por source+escopo. Retorna (quantidade, modo usado)."""
    from src.rag.qdrant_client import get_qdrant
    qdrant = await get_qdrant()
    primary = await qdrant.count(
        collection_name=settings.QDRANT_COLLECTION,
        count_filter=_material_primary_filter(ref["file_id"], teacher_id),
        exact=True,
    )
    if primary.count > 0:
        return primary.count, "file_id"
    legacy = await qdrant.count(
        collection_name=settings.QDRANT_COLLECTION,
        count_filter=_material_legacy_filter(ref, teacher_id),
        exact=True,
    )
    return legacy.count, "legacy"


async def run_list_materiais_command(channel, sender_id: str, course_query: str):
    from src.drive.factory import get_material_source
    try:
        async with AsyncSessionLocal() as session:
            teacher = await _resolve_teacher(session, channel, sender_id)
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return
            teacher_id = teacher[0]

            folder_id = settings.GOOGLE_DRIVE_FOLDER_ID
            if not folder_id:
                await channel.send_text(sender_id, "❌ O Google Drive não está configurado.")
                return

            files = await get_material_source().list_materials(folder_id)
            if not files:
                await channel.send_text(sender_id, "📂 A pasta do Google Drive está vazia.")
                return

            statuses = {row[0]: row[1] for row in (await session.execute(
                text("SELECT file_id, status FROM file_sync_state WHERE teacher_id = :tid"),
                {"tid": teacher_id},
            )).fetchall()}
            course_ids = {row[1]: row[0] for row in (await session.execute(
                text('SELECT id, name FROM "Courses"')
            )).fetchall()}

        # Filtro opcional por nome + ordenação determinística (numeração estável).
        filtered = files
        header = "📂 *Materiais no Google Drive:*"
        if course_query:
            q = course_query.lower()
            filtered = [f for f in files if q in f.name.lower()]
            header = f"📂 *Materiais (Filtro: {course_query}):*"
        filtered = sorted(filtered, key=lambda f: (
            f.course_name or "", f.subject_name or "", f.material_type or "",
            f.lesson_number or 0, f.name,
        ))

        if not filtered:
            await channel.send_text(sender_id, f"{header}\n\nNenhum material encontrado com esse filtro.")
            return

        index: dict[str, dict] = {}
        lines = [header, ""]
        for n, f in enumerate(filtered, start=1):
            status = statuses.get(f.id, "não sincronizado")
            emoji = ("✅" if status == "concluído" else "⏳" if status == "processando"
                     else "🗑️" if status == "excluído" else "❌")
            path = _material_path(f.course_name, f.subject_name, f.material_type, f.lesson_number, f.name)
            lines.append(f"*{n}.* {emoji} {path}\n     _{status}_")
            index[str(n)] = {
                "file_id": f.id,
                "source": f.name,
                "course_id": course_ids.get(f.course_name),
                "subject_name": f.subject_name,
                "lesson_number": f.lesson_number,
                "material_type": f.material_type,
                "path": path,
            }

        redis = await get_redis()
        await redis.set(_materials_index_key(teacher_id), json.dumps(index), ex=_MATERIALS_INDEX_TTL)

        lines.append("\n🗑️ Para remover um material do bot: `/delete_<nº>` (ex.: `/delete_1`).")
        await channel.send_text(sender_id, "\n".join(lines)[:4000])
    except Exception as e:
        logger.error("list_materiais_command_failed", error=str(e)[:300])
        await channel.send_text(sender_id, f"❌ Erro ao buscar materiais: {str(e)[:100]}")


async def run_delete_material_command(channel, sender_id: str, arg: str):
    """/delete_<nº> — passo 1: resolve o nº da última listagem, conta os trechos
    e pede confirmação por botões (o clique é tratado no llm_worker)."""
    num = (arg or "").strip()
    if not num.isdigit():
        await channel.send_text(sender_id, CMD_DELETE_USAGE)
        return
    try:
        async with AsyncSessionLocal() as session:
            teacher = await _resolve_teacher(session, channel, sender_id)
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return
            teacher_id = teacher[0]

        redis = await get_redis()
        index_raw = await redis.get(_materials_index_key(teacher_id))
        if not index_raw:
            await channel.send_text(sender_id, CMD_DELETE_NEED_LIST)
            return
        ref = json.loads(index_raw).get(num)
        if not ref:
            await channel.send_text(sender_id, CMD_DELETE_NOT_FOUND.format(num=num))
            return

        count, mode = await _count_material_chunks(ref, teacher_id)
        if count == 0:
            await channel.send_text(sender_id, CMD_DELETE_EMPTY.format(path=ref["path"]))
            return

        pending = {**ref, "teacher_id": teacher_id, "mode": mode, "count": count}
        await redis.set(_delete_pending_key(sender_id), json.dumps(pending), ex=_DELETE_PENDING_TTL)
        await channel.send_buttons(
            sender_id,
            CMD_DELETE_CONFIRM.format(path=ref["path"], count=count),
            buttons=[
                {"id": "material_delete_confirm", "title": CMD_DELETE_CONFIRM_YES},
                {"id": "material_delete_cancel", "title": CMD_DELETE_CONFIRM_NO},
            ],
        )
    except Exception as e:
        logger.error("delete_material_command_failed", error=str(e)[:300])
        await channel.send_text(sender_id, f"❌ Erro ao preparar a exclusão: {str(e)[:100]}")


async def handle_material_delete_decision(channel, sender_id: str, confirmed: bool) -> None:
    """/delete passo 2: clique do botão (roteado pelo llm_worker). Executa a
    exclusão escopada (vetores + QuizBank/tópicos + marca file_sync_state)."""
    redis = await get_redis()
    pending_key = _delete_pending_key(sender_id)
    raw = await redis.get(pending_key)
    if not raw:
        await channel.send_text(sender_id, CMD_DELETE_EXPIRED)
        return
    await redis.delete(pending_key)  # consome a confirmação (idempotente)

    if not confirmed:
        await channel.send_text(sender_id, CMD_DELETE_CANCELLED)
        return

    ref = json.loads(raw)
    teacher_id = ref["teacher_id"]
    try:
        from src.rag.qdrant_client import get_qdrant
        qdrant = await get_qdrant()
        flt = (_material_primary_filter(ref["file_id"], teacher_id) if ref.get("mode") == "file_id"
               else _material_legacy_filter(ref, teacher_id))
        await qdrant.delete(collection_name=settings.QDRANT_COLLECTION, points_selector=flt)

        # Dados derivados: questões do QuizBank + cache de tópicos do escopo.
        if ref.get("course_id") and ref.get("lesson_number") is not None:
            from src.workers.ingestion_worker import invalidate_quiz_cache_for_lesson
            await invalidate_quiz_cache_for_lesson(
                course_id=ref["course_id"],
                subject_name=ref.get("subject_name"),
                lesson_number=int(ref["lesson_number"]),
                material_type=ref.get("material_type", "lesson"),
            )

        # Marca o arquivo como 'excluído' p/ a sync NÃO o ressuscitar.
        async with AsyncSessionLocal() as session:
            await session.execute(
                text("UPDATE file_sync_state SET status='excluído' WHERE file_id=:fid AND teacher_id=:tid"),
                {"fid": ref["file_id"], "tid": teacher_id},
            )
            await session.commit()

        logger.info("material_deleted", teacher_id=teacher_id, file_id=ref["file_id"],
                    chunks=ref.get("count"), mode=ref.get("mode"))
        await channel.send_text(sender_id, CMD_DELETE_SUCCESS.format(path=ref["path"], count=ref.get("count", 0)))
    except Exception as e:
        logger.error("material_delete_execution_failed", error=str(e)[:300])
        await channel.send_text(sender_id, f"❌ Erro ao apagar: {str(e)[:100]}")

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
            teacher = await _resolve_teacher(session, channel, sender_id)
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return
            
            # Posse por turma: a turma precisa pertencer a este professor, a menos que seja admin
            class_row = await _resolve_owned_class(session, teacher[0], teacher[1], class_name)
            
            if not class_row:
                await channel.send_text(sender_id, CMD_CLASS_NOT_FOUND.format(class_query=class_name))
                return
                
            class_id = class_row[0]
            
            await session.execute(
                text('UPDATE \"Classes\" SET current_lesson_number = :num, last_advanced_date = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = :cid'),
                {"num": new_lesson, "cid": class_id}
            )
            await session.commit()
            
            await channel.send_text(sender_id, f"✅ Turma '{class_name}' atualizada para a aula {new_lesson} com sucesso!")
            
    except Exception as e:
        await channel.send_text(sender_id, f"❌ Erro ao atualizar aula: {str(e)[:100]}")

async def run_zero_lesson_command(channel, sender_id: str, class_name: str):
    """/zerar_aula_Turma — Zera a aula atual (define para 0)"""
    try:
        class_name = (class_name or "").strip()
        if not class_name:
            await channel.send_text(sender_id, "❌ Formato inválido. Use: `/zerar_aula_Nome da Turma`")
            return

        async with AsyncSessionLocal() as session:
            teacher = await _resolve_teacher(session, channel, sender_id)
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return
            
            class_row = await _resolve_owned_class(session, teacher[0], teacher[1], class_name)
            
            if not class_row:
                await channel.send_text(sender_id, CMD_CLASS_NOT_FOUND.format(class_query=class_name))
                return
                
            class_id = class_row[0]
            
            await session.execute(
                text('UPDATE \"Classes\" SET current_lesson_number = 0, last_advanced_date = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = :cid'),
                {"cid": class_id}
            )
            await session.commit()
            
            await channel.send_text(sender_id, f"🔄 A turma *{class_name}* teve seu contador zerado e agora está na *Aula 0* (Aguardando Início).")
            
    except Exception as e:
        await channel.send_text(sender_id, f"❌ Erro ao zerar aula: {str(e)[:100]}")

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
            teacher = await _resolve_teacher(session, channel, sender_id, extra_fields="name, subject")
            # Row: (id, is_admin, name, subject)
                        
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return
                
            # 2. Fetch Class & Course & Subject (posse por turma: c.teacher_id, a menos que seja admin)
            teacher_id = teacher[0]
            is_admin = teacher[1]
            
            class_row = await _resolve_owned_class(session, teacher_id, is_admin, class_name)

            if not class_row:
                await channel.send_text(sender_id, CMD_CLASS_NOT_FOUND.format(class_query=class_name))
                return

            class_id = class_row[0]
            c_name = class_row[1]
            course_id = class_row[2]
            subject_id = class_row[3]      # disciplina da turma (escopo do QuizBank)
            current_lesson = class_row[4]
            subject_name = class_row[5]    # pode ser None em legado
            is_language = class_row[6]     # vertical de Idiomas (planilha)
            proficiency_level = class_row[7]
            
            # 3. Fetch Enrolled Active Students (com endereços dos dois canais + canal preferido)
            # Adiciona a data da última interação para validação da janela de 24h
            stmt_students = text("""
                SELECT s.id, s.phone, s.name, s.telegram_chat_id, s.preferred_channel,
                       (SELECT MAX(timestamp) 
                        FROM "StudentInteractions" 
                        WHERE student_id = s.id 
                          AND interaction_type IN ('message_received', 'quiz_answered')
                       ) as last_interaction
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
        
        # 4. Generate Review & Quiz
        from src.rag.review_generator import generate_review
        from src.rag.prompt_builder import resolve_lesson_label
        from src.rag.quiz_generator import QuizOutput, QuizAlternative
        
        lesson_label = await resolve_lesson_label(course_id, str(current_lesson))
        
        # Vertical de Idiomas: turma de idioma → revisão com a língua do TEXTO por
        # nível + áudio na língua-alvo (paridade com a revisão individual). Sem
        # cache (gerador dedicado); fallback à revisão normal sem conteúdo.
        from src.rag.review_generator import class_language_context, generate_language_review
        delivery, target_language = class_language_context(c_name, is_language, proficiency_level)

        review = None
        if delivery is not None and target_language is not None:
            review = await generate_language_review(
                teacher_id=teacher[0],
                student_id="broadcast",
                student_name="Turma",
                course_id=course_id,
                course_name=c_name,
                subject_name=subject_name,
                current_lesson_number=current_lesson,
                lesson_label=lesson_label,
                delivery=delivery,
                target_language=target_language,
                subject_id=subject_id,
                # Broadcast p/ turma inteira: pula a leitura per-aluno, mas o pacote
                # gerado é salvo no QuizBank (a turma reaproveita nas revisões).
                from_bank=False,
            )
            if review is None:
                delivery = None  # sem conteúdo de idioma → revisão normal

        if review is None:
            # from_bank=False: o broadcast precisa de uma questão NOVA para a turma
            # inteira (a leitura per-aluno do cache não se aplica a 'broadcast').
            # O pacote gerado é SALVO no QuizBank mesmo assim — a turma toda passa
            # a aproveitá-lo nas revisões individuais futuras.
            review = await generate_review(
                teacher_id=teacher[0],
                teacher_name=teacher[2],
                teacher_subject=teacher[3],
                student_id="broadcast",
                student_name="Turma",  # Mensagem generalizada para a turma
                course_id=course_id,
                course_name=c_name,
                subject_name=subject_name,
                current_lesson_number=current_lesson,
                lesson_label=lesson_label,
                subject_id=subject_id,
                from_bank=False,
            )

        if not review:
            await channel.send_text(sender_id, f"❌ Nenhum material encontrado no banco de dados para a aula {current_lesson}.")
            return

        # Áudio sintetizado UMA vez e reaproveitado no broadcast (evita N chamadas
        # de TTS). Revisão sempre na língua-alvo; quiz só quando o texto já é alvo
        # (níveis avançados) — assim o áudio reproduz fielmente o que está escrito.
        review_audio = None
        quiz_audio = None
        if delivery is not None and target_language is not None:
            from src.rag.review_generator import synthesize_listening
            from src.shared.language import review_audio_text
            # Iniciante: resumo em pt-BR → só narrar quando há audio_script (tradução
            # na língua-alvo); senão não narrar pt-BR com a voz da língua-alvo.
            audio_text = review_audio_text(review.audio_script, review.summary, delivery.is_beginner)
            review_audio = await synthesize_listening(audio_text, target_language) if audio_text else None
            if not delivery.is_beginner:
                opts = "\n".join(f"{a.id}) {a.text}" for a in review.quiz.alternatives)
                quiz_audio = await synthesize_listening(f"{review.quiz.question}\n{opts}", target_language)
            
        # Format the summary message to be broadcasted before the quiz
        key_points_text = "\n".join(f"  • {kp}" for kp in review.key_points)
        display_subject = subject_name if subject_name else c_name
        display_topic = review.topic if review.topic else lesson_label
        
        summary_message = (
            f"📖 *Revisão — {display_subject}*\n"
            f"📍 {display_topic}\n\n"
            f"{review.summary}\n\n"
            f"🔑 *Pontos-chave:*\n{key_points_text}\n\n"
            f"_Leia com atenção — o quiz a seguir é baseado neste resumo!_ 👇"
        )
        
        quiz_output = QuizOutput(
            question=review.quiz.question,
            alternatives=[
                QuizAlternative(id=alt.id, text=alt.text)
                for alt in review.quiz.alternatives
            ],
            correct_answer=review.quiz.correct_answer,
            explanation=review.quiz.explanation,
        )
            
        # 5. Broadcast quiz to all students — roteamento por aluno ("follow-the-user").
        # O endereço de entrega depende do canal: telegram_chat_id (Telegram) ou phone (WhatsApp).
        from src.workers.micro_learning import send_quiz_to_student
        default_ct = settings.CHANNEL_BACKEND.lower()
        success_count = 0
        skipped_unlinked = 0
        skipped_window_names = []
        twenty_four_hours_ago = _naive_utc_now() - timedelta(hours=24)

        for st in students:
            ct = (st[4] or default_ct).lower()
            addr = st[3] if ct == "telegram" else st[1]  # telegram_chat_id | phone
            if not addr:
                # Aluno sem endereço no canal preferido (ex.: não vinculou o Telegram). Não há como entregar.
                skipped_unlinked += 1
                logger.info("quiz_skip_unlinked_student", student_id=st[0], student_name=st[2], channel=ct)
                continue
                
            # Checa a janela de 24h
            last_int = _as_naive(st[5])
            window_active = bool(last_int and last_int > twenty_four_hours_ago)
                    
            if not window_active:
                skipped_window_names.append(st[2])
                logger.info("quiz_skip_window_closed", student_id=st[0], student_name=st[2], channel=ct)
                continue

            try:
                c = get_channel(ct)
                # Envia o resumo antes do quiz
                await c.send_text(addr, summary_message)
                # Vertical de Idiomas: áudio da revisão na língua-alvo (bytes
                # reaproveitados; best-effort — falha não derruba a entrega).
                if review_audio:
                    try:
                        await c.send_audio(addr, review_audio)
                    except Exception as e:
                        logger.warning("quiz_review_audio_failed", student_id=st[0], error=str(e)[:200])
                # Envia o quiz interativo — o quiz_bank_id registra o consumo
                # da questão por ESTE aluno (PendingQuizzes.quiz_bank_id),
                # garantindo que ele não a receba de novo numa revisão futura.
                await send_quiz_to_student(
                    channel=c,
                    student={
                        "id": st[0],
                        "sender_id": addr,
                        "course_id": course_id,
                        "lesson_pointer": str(current_lesson)
                    },
                    quiz=quiz_output,
                    quiz_bank_id=review.quiz_bank_id,
                )
                if quiz_audio:
                    try:
                        await c.send_audio(addr, quiz_audio)
                    except Exception as e:
                        logger.warning("quiz_audio_send_failed", student_id=st[0], error=str(e)[:200])
                success_count += 1
            except Exception as e:
                logger.error("failed_to_send_quiz_to_student", student_id=st[0], error=str(e))

        msg = f"✅ Quiz enviado com sucesso para {success_count} aluno(s) da turma '{c_name}'!"
        if skipped_unlinked:
            msg += f"\n⚠️ {skipped_unlinked} aluno(s) sem conta vinculada no bot foram pulados."
        if skipped_window_names:
            names_str = "\n".join(f"• {name}" for name in skipped_window_names)
            msg += f"\n\n⚠️ Aluno(s) que não receberam porque estão inativos há mais de 24 horas:\n{names_str}"
        await channel.send_text(sender_id, msg)
        
    except Exception as e:
        await channel.send_text(sender_id, f"❌ Erro ao enviar quiz: {str(e)[:100]}")


# ==============================================================================
# COMANDOS GERENCIAIS — /comando_[turma]_[arg]
# Todos validam a posse da turma via Classes.teacher_id (_resolve_owned_class).
# ==============================================================================

async def run_engagement_command(channel, sender_id: str, class_query: str):
    """/engajamento_[turma] — alunos ativos e total de interações em 7 dias."""
    try:
        async with AsyncSessionLocal() as session:
            teacher = await _resolve_teacher(session, channel, sender_id)
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return
            klass = await _resolve_owned_class(session, teacher[0], teacher[1], class_query)
            if not klass:
                await channel.send_text(sender_id, CMD_CLASS_NOT_FOUND.format(class_query=class_query))
                return

            class_id, c_name = klass[0], klass[1]
            cutoff = _naive_utc_now() - timedelta(days=7)

            total_students = (await session.execute(
                text('''SELECT count(*)
                        FROM "StudentEnrollment" se
                        JOIN "Students" s ON s.id = se.student_id
                        WHERE se.class_id = :cid AND s.is_active = true'''),
                {"cid": class_id}
            )).scalar() or 0

            row = (await session.execute(
                text('''SELECT count(DISTINCT si.student_id) AS ativos,
                               count(*) AS interacoes
                        FROM "StudentInteractions" si
                        JOIN "StudentEnrollment" se ON se.student_id = si.student_id
                        WHERE se.class_id = :cid AND si.timestamp >= :cutoff'''),
                {"cid": class_id, "cutoff": cutoff}
            )).fetchone()
            active_students = (row[0] if row else 0) or 0
            interactions = (row[1] if row else 0) or 0

            quizzes_answered = (await session.execute(
                text('''SELECT count(*)
                        FROM "PendingQuizzes" pq
                        JOIN "StudentEnrollment" se ON se.student_id = pq.student_id
                        WHERE se.class_id = :cid
                          AND pq.status = 'answered'
                          AND pq.answered_at >= :cutoff'''),
                {"cid": class_id, "cutoff": cutoff}
            )).scalar() or 0

        pct = round(100 * active_students / total_students) if total_students else 0
        msg = (
            f"📊 *Engajamento — {c_name}* (últimos 7 dias)\n\n"
            f"👥 Alunos ativos: *{active_students} de {total_students}* ({pct}%)\n"
            f"💬 Interações registradas: *{interactions}*\n"
            f"📝 Quizzes respondidos: *{quizzes_answered}*"
        )
        # Gamificação (Fase 1) — anexa o bloco de engajamento (vazio se a flag off).
        from src.workers.gamification import teacher_gamification_section
        msg += await teacher_gamification_section(class_id, c_name)
        await channel.send_text(sender_id, msg)
    except Exception as e:
        logger.error("engagement_command_failed", error=str(e)[:300])
        await channel.send_text(sender_id, f"❌ Erro ao calcular engajamento: {str(e)[:100]}")


async def run_quiz_performance_command(channel, sender_id: str, class_query: str):
    """/acertos_[turma] — junta PendingQuizzes ↔ QuizBank, agrupa por topic e
    retorna os 3 tópicos com pior desempenho."""
    try:
        async with AsyncSessionLocal() as session:
            teacher = await _resolve_teacher(session, channel, sender_id)
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return
            klass = await _resolve_owned_class(session, teacher[0], teacher[1], class_query)
            if not klass:
                await channel.send_text(sender_id, CMD_CLASS_NOT_FOUND.format(class_query=class_query))
                return

            class_id, c_name = klass[0], klass[1]

            overall = (await session.execute(
                text('''SELECT SUM(CASE WHEN pq.is_correct THEN 1 ELSE 0 END) AS acertos,
                               COUNT(*) AS total
                        FROM "PendingQuizzes" pq
                        JOIN "QuizBank" qb ON qb.id = pq.quiz_bank_id
                        JOIN "StudentEnrollment" se ON se.student_id = pq.student_id
                        WHERE se.class_id = :cid AND pq.status = 'answered' '''),
                {"cid": class_id}
            )).fetchone()
            total_answered = (overall[1] if overall else 0) or 0
            total_correct = (overall[0] if overall else 0) or 0

            if not total_answered:
                await channel.send_text(
                    sender_id,
                    f"📈 *Acertos — {c_name}*\n\nAinda não há quizzes do Banco de Questões "
                    "respondidos nesta turma.\n\n_Nota: respostas anteriores à ativação do "
                    "QuizBank não entram nesta análise (não têm vínculo com tópico)._"
                )
                return

            worst = (await session.execute(
                text('''SELECT qb.topic,
                               SUM(CASE WHEN pq.is_correct THEN 1 ELSE 0 END) AS acertos,
                               COUNT(*) AS total
                        FROM "PendingQuizzes" pq
                        JOIN "QuizBank" qb ON qb.id = pq.quiz_bank_id
                        JOIN "StudentEnrollment" se ON se.student_id = pq.student_id
                        WHERE se.class_id = :cid AND pq.status = 'answered'
                        GROUP BY qb.topic
                        ORDER BY (SUM(CASE WHEN pq.is_correct THEN 1 ELSE 0 END)::float / COUNT(*)) ASC,
                                 COUNT(*) DESC
                        LIMIT 3'''),
                {"cid": class_id}
            )).fetchall()

        overall_pct = round(100 * total_correct / total_answered)
        msg = (
            f"📈 *Acertos — {c_name}*\n\n"
            f"Geral: *{total_correct}/{total_answered}* respostas corretas ({overall_pct}%)\n\n"
            f"⚠️ *3 tópicos com pior desempenho:*\n"
        )
        for idx, r in enumerate(worst, 1):
            topic, hits, total = r[0], r[1] or 0, r[2] or 0
            pct = round(100 * hits / total) if total else 0
            msg += f"{idx}. *{topic}* — {hits}/{total} ({pct}%)\n"
        msg += "\n💡 _Dica: vale reforçar esses tópicos na próxima aula._"
        await channel.send_text(sender_id, msg[:4000])
    except Exception as e:
        logger.error("quiz_performance_command_failed", error=str(e)[:300])
        await channel.send_text(sender_id, f"❌ Erro ao calcular acertos: {str(e)[:100]}")


async def run_at_risk_command(channel, sender_id: str, class_query: str):
    """/risco_[turma] — alunos com max(timestamp) < NOW() - 7 dias e/ou
    aproveitamento baixo nos quizzes."""
    try:
        async with AsyncSessionLocal() as session:
            teacher = await _resolve_teacher(session, channel, sender_id)
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return
            klass = await _resolve_owned_class(session, teacher[0], teacher[1], class_query)
            if not klass:
                await channel.send_text(sender_id, CMD_CLASS_NOT_FOUND.format(class_query=class_query))
                return

            class_id, c_name = klass[0], klass[1]

            rows = (await session.execute(
                text('''SELECT s.id, s.name,
                               (SELECT MAX(si.timestamp) FROM "StudentInteractions" si
                                WHERE si.student_id = s.id) AS last_seen,
                               (SELECT COUNT(*) FROM "PendingQuizzes" pq
                                WHERE pq.student_id = s.id AND pq.status = 'answered') AS respondidos,
                               (SELECT COUNT(*) FROM "PendingQuizzes" pq
                                WHERE pq.student_id = s.id AND pq.status = 'answered'
                                  AND pq.is_correct = true) AS corretos
                        FROM "Students" s
                        JOIN "StudentEnrollment" se ON se.student_id = s.id
                        WHERE se.class_id = :cid AND s.is_active = true
                        ORDER BY s.name'''),
                {"cid": class_id}
            )).fetchall()

        now = _naive_utc_now()
        cutoff = now - timedelta(days=7)
        at_risk = []
        for r in rows:
            reasons = []
            last_seen = _as_naive(r[2])
            if last_seen is None:
                reasons.append("📵 nunca interagiu")
            elif last_seen < cutoff:
                reasons.append(f"📵 inativo há {(now - last_seen).days}d")

            respondidos = r[3] or 0
            corretos = r[4] or 0
            if respondidos >= 3:
                taxa = corretos / respondidos
                if taxa < 0.5:
                    reasons.append(f"📉 acertos {round(taxa * 100)}% ({corretos}/{respondidos})")

            if reasons:
                at_risk.append((r[1], reasons))

        if not at_risk:
            await channel.send_text(sender_id, f"🟢 *Risco — {c_name}*\n\nNenhum aluno em risco identificado. Boa!")
            return

        msg = f"🚨 *Alunos em risco — {c_name}* ({len(at_risk)})\n\n"
        for name, reasons in at_risk[:25]:
            msg += f"• *{name}* — {', '.join(reasons)}\n"
        if len(at_risk) > 25:
            msg += f"\n… e mais {len(at_risk) - 25} aluno(s)."
        msg += "\n\n_Critérios: sem interação há 7+ dias e/ou < 50% de acertos (mín. 3 quizzes)._"
        await channel.send_text(sender_id, msg[:4000])
    except Exception as e:
        logger.error("at_risk_command_failed", error=str(e)[:300])
        await channel.send_text(sender_id, f"❌ Erro ao calcular risco: {str(e)[:100]}")


async def run_frequent_doubts_command(channel, sender_id: str, class_query: str):
    """/duvidas_frequentes_[turma] — coleta message_content da última semana,
    passa pro LLM (Gemini/provider configurado) e devolve um sumário."""
    try:
        async with AsyncSessionLocal() as session:
            teacher = await _resolve_teacher(session, channel, sender_id)
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return
            klass = await _resolve_owned_class(session, teacher[0], teacher[1], class_query)
            if not klass:
                await channel.send_text(sender_id, CMD_CLASS_NOT_FOUND.format(class_query=class_query))
                return

            class_id, c_name = klass[0], klass[1]
            cutoff = _naive_utc_now() - timedelta(days=7)

            rows = (await session.execute(
                text('''SELECT si.message_content
                        FROM "StudentInteractions" si
                        JOIN "StudentEnrollment" se ON se.student_id = si.student_id
                        WHERE se.class_id = :cid
                          AND si.interaction_type = 'message_received'
                          AND si.timestamp >= :cutoff
                          AND si.message_content IS NOT NULL
                        ORDER BY si.timestamp ASC
                        LIMIT 300'''),
                {"cid": class_id, "cutoff": cutoff}
            )).fetchall()

        # Filtra ruído: comandos, respostas de botão e mensagens triviais
        candidates = []
        for r in rows:
            content = (r[0] or "").strip()
            if not content or content.startswith("/") or len(content) < 4:
                continue
            candidates.append(content)

        if not candidates:
            await channel.send_text(
                sender_id,
                f"📭 *Dúvidas Frequentes — {c_name}*\n\nNenhuma mensagem de aluno encontrada nos últimos 7 dias."
            )
            return

        await channel.send_text(sender_id, f"⏳ Analisando {len(candidates)} mensagens da turma com a IA...")

        # Limita o corpus para caber confortavelmente no contexto do LLM
        corpus_lines, total_chars = [], 0
        for m in candidates:
            if total_chars + len(m) > 12000:
                break
            corpus_lines.append(f"- {m}")
            total_chars += len(m)

        # A chamada ao LLM acontece FORA da sessão de banco (conexão liberada).
        from src.llm.factory import get_provider
        from src.shared.constants.prompts import FREQUENT_DOUBTS_PROMPT

        llm = get_provider(settings.DEFAULT_LLM_PROVIDER)
        report = await llm.agenerate(
            prompt=FREQUENT_DOUBTS_PROMPT.format(messages="\n".join(corpus_lines))
        )

        await channel.send_text(
            sender_id,
            f"💬 *Dúvidas Frequentes — {c_name}* (últimos 7 dias)\n\n{report}"[:4000]
        )
    except Exception as e:
        logger.error("frequent_doubts_command_failed", error=str(e)[:300])
        await channel.send_text(sender_id, f"❌ Erro ao analisar dúvidas: {str(e)[:100]}")


async def _resolve_owned_student(session, teacher_id: str, is_admin: bool, phone_arg: str):
    """Resolve um aluno por sufixo de telefone E valida a posse: o aluno precisa
    estar matriculado em pelo menos uma turma do professor (Classes.teacher_id),
    a menos que o professor seja admin.

    Retorna (student_row, enrollments, digits) onde:
      student_row = (id, name, phone, is_active) | None
      enrollments = [(class_name, current_lesson_number, course_name, subject_name, class_id)]
    """
    digits = re.sub(r"\D", "", phone_arg or "")
    if len(digits) < 8:
        return None, [], digits

    stu = (await session.execute(
        text('SELECT id, name, phone, is_active FROM "Students" WHERE phone LIKE :pl LIMIT 1'),
        {"pl": f"%{digits}"}
    )).fetchone()
    if not stu:
        return None, [], digits

    enroll_sql = '''
        SELECT cl.name, cl.current_lesson_number, crs.name AS course_name,
               sub.name AS subject_name, cl.id
        FROM "StudentEnrollment" se
        JOIN "Classes" cl ON cl.id = se.class_id
        JOIN "Courses" crs ON crs.id = cl.course_id
        LEFT JOIN "Subjects" sub ON sub.id = cl.subject_id
        WHERE se.student_id = :stid
    '''
    if is_admin:
        result = await session.execute(text(enroll_sql), {"stid": stu[0]})
    else:
        result = await session.execute(
            text(enroll_sql + " AND cl.teacher_id = :tid"),
            {"stid": stu[0], "tid": teacher_id}
        )
    return stu, result.fetchall(), digits


async def run_view_student_command(channel, sender_id: str, phone_arg: str):
    """/ver_aluno_[telefone] — matrícula, lição atual e últimas 10 interações."""
    try:
        async with AsyncSessionLocal() as session:
            teacher = await _resolve_teacher(session, channel, sender_id)
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return

            stu, enrollments, digits = await _resolve_owned_student(session, teacher[0], teacher[1], phone_arg)
            if len(digits) < 8:
                await channel.send_text(sender_id, CMD_STUDENT_PHONE_USAGE.format(command="/ver_aluno"))
                return
            if not stu:
                await channel.send_text(sender_id, CMD_STUDENT_NOT_FOUND.format(digits=digits[-8:]))
                return
            if not enrollments:
                # Posse negada: aluno existe mas não está em nenhuma turma do professor.
                await channel.send_text(sender_id, CMD_STUDENT_NOT_OWNED)
                return

            quiz_stats = (await session.execute(
                text('''SELECT COUNT(*) AS respondidos,
                               SUM(CASE WHEN is_correct THEN 1 ELSE 0 END) AS corretos
                        FROM "PendingQuizzes"
                        WHERE student_id = :stid AND status = 'answered' '''),
                {"stid": stu[0]}
            )).fetchone()

            interactions = (await session.execute(
                text('''SELECT interaction_type, message_content, timestamp, is_correct
                        FROM "StudentInteractions"
                        WHERE student_id = :stid
                        ORDER BY timestamp DESC
                        LIMIT 10'''),
                {"stid": stu[0]}
            )).fetchall()

        status_emoji = "🟢 ativo" if stu[3] else "🔴 inativo"
        msg = f"👤 *Ficha do Aluno*\n\n*{stu[1]}*\n📱 {stu[2]} — {status_emoji}\n\n📚 *Matrículas:*\n"
        for e in enrollments:
            subj = f" — {e[3]}" if e[3] else ""
            msg += f"• {e[2]}{subj} | Turma *{e[0]}* | Aula atual: *{e[1]}*\n"

        respondidos = (quiz_stats[0] if quiz_stats else 0) or 0
        corretos = (quiz_stats[1] if quiz_stats else 0) or 0
        if respondidos:
            msg += f"\n📝 Quizzes: *{corretos}/{respondidos}* corretos ({round(100 * corretos / respondidos)}%)\n"
        else:
            msg += "\n📝 Quizzes: nenhum respondido ainda.\n"

        msg += "\n🕓 *Últimas 10 interações:*\n"
        if not interactions:
            msg += "_Nenhuma interação registrada._"
        else:
            type_emoji = {"message_received": "💬", "quiz_answered": "📝", "review_sent": "📖"}
            for it in interactions:
                ts = _as_naive(it[2])
                ts_str = ts.strftime("%d/%m %H:%M") if ts else "--/--"
                emoji = type_emoji.get(it[0], "•")
                detail = (it[1] or "").replace("\n", " ").strip()
                if it[0] == "quiz_answered" and it[3] is not None:
                    detail = f"{detail} ({'✅' if it[3] else '❌'})".strip()
                if len(detail) > 60:
                    detail = detail[:57] + "..."
                msg += f"{emoji} {ts_str} · {detail or it[0]}\n"

        await channel.send_text(sender_id, msg[:4000])
    except Exception as e:
        logger.error("view_student_command_failed", error=str(e)[:300])
        await channel.send_text(sender_id, f"❌ Erro ao buscar aluno: {str(e)[:100]}")


async def run_reset_student_command(channel, sender_id: str, phone_arg: str):
    """/resetar_aluno_[telefone] — reinicia a jornada individual do aluno:
    zera a rotação de tópicos (Redis), limpa a memória de conversa, expira os
    quizzes pendentes e desvincula o histórico do Banco de Questões (o aluno
    volta a poder receber TODAS as questões, como se estivesse na aula 1).

    Nota de arquitetura: o ponteiro de aula é por TURMA
    (Classes.current_lesson_number) — para voltar a turma inteira à aula 1,
    o professor usa `/aula Turma = 1`.
    """
    try:
        async with AsyncSessionLocal() as session:
            teacher = await _resolve_teacher(session, channel, sender_id)
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return

            stu, enrollments, digits = await _resolve_owned_student(session, teacher[0], teacher[1], phone_arg)
            if len(digits) < 8:
                await channel.send_text(sender_id, CMD_STUDENT_PHONE_USAGE.format(command="/resetar_aluno"))
                return
            if not stu:
                await channel.send_text(sender_id, CMD_STUDENT_NOT_FOUND.format(digits=digits[-8:]))
                return
            if not enrollments:
                await channel.send_text(sender_id, CMD_STUDENT_NOT_OWNED)
                return

            student_id, student_name, student_phone = stu[0], stu[1], stu[2]

            # 1) Expira quizzes pendentes (não respondidos)
            res_exp = await session.execute(
                text('''UPDATE "PendingQuizzes"
                        SET status = 'expired', updated_at = CURRENT_TIMESTAMP
                        WHERE student_id = :stid AND status = 'pending' '''),
                {"stid": student_id}
            )
            expired = res_exp.rowcount or 0  # type: ignore[attr-defined]  # CursorResult tem rowcount

            # 2) Desvincula o histórico do QuizBank: o dedupe "esquece" o aluno
            #    e ele volta a poder receber todas as questões do banco.
            res_unlink = await session.execute(
                text('''UPDATE "PendingQuizzes"
                        SET quiz_bank_id = NULL
                        WHERE student_id = :stid AND quiz_bank_id IS NOT NULL'''),
                {"stid": student_id}
            )
            unlinked = res_unlink.rowcount or 0  # type: ignore[attr-defined]  # CursorResult tem rowcount
            await session.commit()

        # 3) Redis: zera a rotação de tópicos e a memória de conversa
        redis = await get_redis()
        deleted_keys = 0
        async for key in redis.scan_iter(match=f"used_topics:{student_id}:*"):
            await redis.delete(key)
            deleted_keys += 1
        await redis.delete(f"chat_history:{student_phone}")

        turma_hint = enrollments[0][0] if enrollments else "Turma"
        msg = (
            f"🔄 *Aluno resetado: {student_name}*\n\n"
            f"• Rotação de tópicos zerada ({deleted_keys} ciclo(s) limpos)\n"
            f"• Memória de conversa apagada\n"
            f"• {expired} quiz(zes) pendente(s) expirados\n"
            f"• Histórico do Banco de Questões desvinculado ({unlinked} registro(s)) — "
            f"o aluno voltará a receber todas as questões desde o início.\n\n"
            f"ℹ️ O ponteiro de aula é por *turma*. Para voltar a turma à aula 1, use:\n"
            f"`/aula {turma_hint} = 1`"
        )
        await channel.send_text(sender_id, msg)
        logger.info("student_reset", student_id=student_id, expired=expired, unlinked=unlinked)
    except Exception as e:
        logger.error("reset_student_command_failed", error=str(e)[:300])
        await channel.send_text(sender_id, f"❌ Erro ao resetar aluno: {str(e)[:100]}")


async def run_clear_quiz_bank_command(channel, sender_id: str, class_query: str):
    """/limpar_quizzes_[turma] — deleta FISICAMENTE os quizzes cacheados no
    QuizBank para o curso/disciplina da turma, forçando o LLM a gerar novos do
    zero na próxima rodada."""
    try:
        async with AsyncSessionLocal() as session:
            teacher = await _resolve_teacher(session, channel, sender_id)
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return
            klass = await _resolve_owned_class(session, teacher[0], teacher[1], class_query)
            if not klass:
                await channel.send_text(sender_id, CMD_CLASS_NOT_FOUND.format(class_query=class_query))
                return

        c_name, course_id, subject_id, subject_name = klass[1], klass[2], klass[3], klass[5]

        from src.rag.quiz_bank import delete_scope_quizzes
        deleted = await delete_scope_quizzes(course_id=course_id, subject_id=subject_id)

        scope_label = f"{c_name}" + (f" — {subject_name}" if subject_name else "")
        msg = (
            f"🧹 *Banco de Questões limpo!*\n\n"
            f"*{deleted}* questão(ões) apagadas para *{scope_label}*.\n\n"
            f"A próxima rodada de quizzes será gerada do zero pelo LLM a partir do material atual. "
            f"O histórico de respostas dos alunos foi preservado."
        )
        await channel.send_text(sender_id, msg)
    except Exception as e:
        logger.error("clear_quiz_bank_command_failed", error=str(e)[:300])
        await channel.send_text(sender_id, f"❌ Erro ao limpar quizzes: {str(e)[:100]}")


async def run_broadcast_notice_command(channel, sender_id: str, class_query: str, message: str):
    """/aviso_[turma]_[mensagem] — resolve todos os student_id da turma e
    publica mensagens INDIVIDUAIS na fila `outbound_messages` (a entrega real
    é feita pelo consumidor outbound, fora deste handler)."""
    try:
        if not class_query or not message:
            await channel.send_text(sender_id, CMD_AVISO_USAGE)
            return

        async with AsyncSessionLocal() as session:
            teacher = await _resolve_teacher(session, channel, sender_id)
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return
            klass = await _resolve_owned_class(session, teacher[0], teacher[1], class_query)
            if not klass:
                await channel.send_text(sender_id, CMD_CLASS_NOT_FOUND.format(class_query=class_query))
                return

            class_id, c_name = klass[0], klass[1]

            students = (await session.execute(
                text('''SELECT s.id, s.phone, s.name, s.telegram_chat_id, s.preferred_channel,
                               (SELECT MAX(timestamp) FROM "StudentInteractions"
                                WHERE student_id = s.id
                                  AND interaction_type IN ('message_received', 'quiz_answered')
                               ) as last_interaction
                        FROM "Students" s
                        JOIN "StudentEnrollment" se ON s.id = se.student_id
                        WHERE se.class_id = :cid AND s.is_active = true'''),
                {"cid": class_id}
            )).fetchall()

        if not students:
            await channel.send_text(sender_id, f"❌ Nenhum aluno ativo encontrado na turma '{c_name}'.")
            return

        notice_text = f"📣 *Aviso do professor — {c_name}*\n\n{message}"
        default_ct = settings.CHANNEL_BACKEND.lower()
        twenty_four_hours_ago = _naive_utc_now() - timedelta(hours=24)

        from src.queue.factory import get_queue
        queue = get_queue()

        queued = 0
        skipped_unlinked = 0
        skipped_window_names = []

        for st in students:
            ct = (st[4] or default_ct).lower()
            addr = st[3] if ct == "telegram" else st[1]  # telegram_chat_id | phone
            if not addr:
                skipped_unlinked += 1
                continue

            # Janela de 24h: restrição da POLÍTICA DA META — só se aplica ao
            # WhatsApp. No Telegram não existe janela, então entregamos sempre.
            if ct == "whatsapp":
                last_int = _as_naive(st[5])
                if not (last_int and last_int > twenty_four_hours_ago):
                    skipped_window_names.append(st[2])
                    continue

            await queue.enqueue("outbound_messages", {
                "recipient_id": addr,
                "channel": ct,
                "text": notice_text,
                "student_id": st[0],
                "origin": "teacher_notice",
            })
            queued += 1

        msg = f"📣 Aviso enfileirado para *{queued}* aluno(s) da turma '{c_name}'."
        if skipped_unlinked:
            msg += f"\n⚠️ {skipped_unlinked} aluno(s) sem conta vinculada no bot foram pulados."
        if skipped_window_names:
            names_str = "\n".join(f"• {n}" for n in skipped_window_names)
            msg += f"\n\n⚠️ Fora da janela de 24h do WhatsApp (não receberão):\n{names_str}"
        await channel.send_text(sender_id, msg[:4000])
        logger.info("notice_broadcast_enqueued", class_id=class_id, queued=queued)
    except Exception as e:
        logger.error("broadcast_notice_command_failed", error=str(e)[:300])
        await channel.send_text(sender_id, f"❌ Erro ao enviar aviso: {str(e)[:100]}")


# ==============================================================================
# DISPATCH
# ==============================================================================

async def run_student_engagement_command(channel, sender_id: str, days: int | None) -> None:
    """/engajamento[_7|_15|_30] do ALUNO — resumo POR DISCIPLINA: perguntas por
    iniciativa (message_received) + quizzes recebidos/respondidos/acertos/%.

    A disciplina do quiz vem do QuizBank (JOIN por quiz_bank_id); a da pergunta,
    do carimbo subject_id do ledger. Linhas sem atribuição (histórico antigo,
    quiz sem banco) caem no nome do curso ou em 'Geral'. Só dados do PRÓPRIO
    aluno — sem colegas (LGPD ok)."""
    from datetime import datetime, timezone, timedelta
    from src.shared.constants.messages import (
        STUDENT_ENGAGEMENT_HEADER, STUDENT_ENGAGEMENT_BLOCK, STUDENT_ENGAGEMENT_NONE,
    )
    try:
        query_col = channel.get_user_query_column()
        since = (
            datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
            if days else None
        )
        since_quiz = "AND pq.created_at >= :since" if since else ""
        since_msgs = "AND si.timestamp >= :since" if since else ""

        async with AsyncSessionLocal() as session:
            stu = (await session.execute(
                text(f'SELECT id FROM "Students" WHERE {query_col} = :sid AND is_active = true LIMIT 1'),
                {"sid": sender_id},
            )).fetchone()
            if not stu:
                return
            params: dict = {"sid": stu.id}
            if since:
                params["since"] = since

            quiz_rows = (await session.execute(
                text(f'''SELECT COALESCE(sub.name, c.name, 'Geral') AS label,
                                COUNT(*) AS recebidos,
                                COUNT(*) FILTER (WHERE pq.status = 'answered') AS respondidos,
                                COUNT(*) FILTER (WHERE pq.is_correct IS TRUE) AS acertos
                         FROM "PendingQuizzes" pq
                         LEFT JOIN "QuizBank" qb ON qb.id = pq.quiz_bank_id
                         LEFT JOIN "Subjects" sub ON sub.id = qb.subject_id
                         LEFT JOIN "Courses" c ON c.id = pq.course_id
                         WHERE pq.student_id = :sid {since_quiz}
                         GROUP BY label'''),
                params,
            )).fetchall()

            msg_rows = (await session.execute(
                text(f'''SELECT COALESCE(sub.name, c.name, 'Geral') AS label,
                                COUNT(*) AS perguntas
                         FROM "StudentInteractions" si
                         LEFT JOIN "Subjects" sub ON sub.id = si.subject_id
                         LEFT JOIN "Courses" c ON c.id = si.course_id
                         WHERE si.student_id = :sid
                           AND si.interaction_type = 'message_received' {since_msgs}
                         GROUP BY label'''),
                params,
            )).fetchall()

        # Merge por disciplina (uma linha pode existir só num dos lados)
        stats: dict[str, dict] = {}
        for r in quiz_rows:
            stats.setdefault(r.label, {"perguntas": 0, "recebidos": 0, "respondidos": 0, "acertos": 0})
            stats[r.label].update(recebidos=int(r.recebidos or 0),
                                  respondidos=int(r.respondidos or 0),
                                  acertos=int(r.acertos or 0))
        for r in msg_rows:
            stats.setdefault(r.label, {"perguntas": 0, "recebidos": 0, "respondidos": 0, "acertos": 0})
            stats[r.label]["perguntas"] = int(r.perguntas or 0)

        period = f"últimos {days} dias" if days else "histórico completo"
        if not stats:
            suffix = f" nos últimos {days} dias" if days else ""
            await channel.send_text(sender_id, STUDENT_ENGAGEMENT_NONE.format(period_suffix=suffix))
            return

        body = STUDENT_ENGAGEMENT_HEADER.format(period=period)
        for label in sorted(stats):
            s = stats[label]
            pct = round(100 * s["acertos"] / s["respondidos"]) if s["respondidos"] else 0
            body += STUDENT_ENGAGEMENT_BLOCK.format(label=label, pct=pct, **s)
        await channel.send_text(sender_id, body[:4000])
    except Exception as e:
        logger.error("student_engagement_failed", error=str(e)[:300])


def build_student_help_text() -> str:
    """Monta o /ajuda do ALUNO por PERFIL: só entram as seções cujos recursos
    estão ativos — mesmo gate das próprias funcionalidades (o webhook nem
    roteia /meu_ranking com GAMIFICATION_ENABLED off, então listá-lo seria
    anunciar um comando que responde ERROR_UNAUTHORIZED)."""
    parts = [CMD_STUDENT_HELP_HEADER]
    if settings.LANGUAGE_MODE_ENABLED:
        parts.append(CMD_STUDENT_HELP_LANGUAGE)
    parts.append(CMD_STUDENT_HELP_READING)
    if settings.GAMIFICATION_ENABLED:
        parts.append(CMD_STUDENT_HELP_GAMING)
        if settings.GRADE_COMPONENT_ENABLED:
            parts.append(CMD_STUDENT_HELP_GRADE)
    parts.append(CMD_STUDENT_HELP_FOOTER)
    return "".join(parts)


async def process_command_message(message_body: dict):
    action = message_body.get("action")
    if action == "class_session_start":
        from src.workers.class_session_worker import handle_class_session_start
        await handle_class_session_start(message_body)
        return

    sender_id = str(message_body.get("sender_id", message_body.get("phone", "")))
    text_content = message_body.get("text", "")
    channel_type = message_body.get("channel")
    role = message_body.get("role", "teacher")
    channel = get_channel(channel_type)

    # -1) ATIVAÇÃO POR QR CODE (H2.1/H3.1) — chave LIVRO-/PROF- ou resposta
    #     (nome) de uma ativação pendente. Roteado pelo webhook com
    #     role="activation" (inclusive de remetentes desconhecidos).
    if role == "activation":
        from src.workers.activation_worker import process_activation_message
        await process_activation_message(message_body)
        return

    # -0.5) LGPD Art. 14: mensagem do RESPONSÁVEL (botão de consentimento ou
    #       REVOGAR). Remetente desconhecido — roteado pelo webhook com
    #       role="guardian".
    if role == "guardian":
        from src.workers.guardian_consent import handle_guardian_message
        await handle_guardian_message(message_body)
        return

    # 0) Comandos de ALUNO (Addon de Livros) — o webhook só roteia para cá os
    #    comandos da allowlist (STUDENT_COMMAND_REGEX) com role="student".
    student_cmd = parse_student_command(text_content)
    if student_cmd and role == "student":
        from src.workers import reading_commands
        logger.info("process_student_command", sender_id=sender_id, command=student_cmd)

        if student_cmd == CommandType.PAUSE_READING:
            await reading_commands.run_pause_reading_command(channel, sender_id)
        elif student_cmd == CommandType.RESUME_READING:
            await reading_commands.run_resume_reading_command(channel, sender_id)
        elif student_cmd == CommandType.MY_PROGRESS:
            await reading_commands.run_my_progress_command(channel, sender_id)
        elif student_cmd == CommandType.FLASHCARDS:
            await reading_commands.run_flashcards_command(channel, sender_id)
        elif student_cmd == CommandType.PLACEMENT:
            from src.workers.placement import start_placement
            await start_placement(channel, sender_id)
        elif student_cmd == CommandType.LEAVE_BOOK:
            from src.workers.activation_worker import run_leave_book_command
            await run_leave_book_command(channel, sender_id)
        elif student_cmd == CommandType.CORRECT_ESSAY:
            from src.workers.essay import run_correct_essay_command
            await run_correct_essay_command(channel, sender_id)
        elif student_cmd == CommandType.DELETE_MY_DATA:
            # LGPD Art. 18: passo 1 (confirmação por botões). O clique é tratado
            # no fast-path do llm_worker (lgpd_delete_confirm/cancel).
            from src.workers.privacy_commands import run_delete_my_data_command
            await run_delete_my_data_command(channel, sender_id)
        # ── Gamificação (Fase 0) — sob demanda, grátis na janela ────────────
        elif student_cmd == CommandType.MY_RANKING:
            from src.workers.gamification import run_my_ranking_command
            await run_my_ranking_command(channel, sender_id)
        elif student_cmd == CommandType.ACHIEVEMENTS:
            from src.workers.gamification import run_achievements_command
            await run_achievements_command(channel, sender_id)
        elif student_cmd == CommandType.HIDE_RANKING:
            from src.workers.gamification import run_hide_ranking_command
            await run_hide_ranking_command(channel, sender_id)
        elif student_cmd == CommandType.MY_GRADE:
            from src.workers.gamification import run_my_grade_command
            await run_my_grade_command(channel, sender_id)
        elif student_cmd == CommandType.MY_XP:
            from src.workers.gamification import run_my_xp_command
            await run_my_xp_command(channel, sender_id)
        elif student_cmd == CommandType.STUDENT_ENGAGEMENT:
            await run_student_engagement_command(
                channel, sender_id, parse_engagement_days(text_content)
            )
        elif student_cmd == CommandType.STUDENT_HELP:
            await channel.send_text(sender_id, build_student_help_text())
        return

    # 1) Comandos gerenciais — sintaxe explícita /comando_[turma]_[arg].
    #    O regex é a mesma fonte da verdade usada pelo webhook para rotear.
    mgmt = parse_management_command(text_content)
    if mgmt:
        cmd_type, target, extra = mgmt
        logger.info("process_management_command", sender_id=sender_id, command=cmd_type, target=target[:60])

        if cmd_type == CommandType.ENGAGEMENT:
            await run_engagement_command(channel, sender_id, target)
        elif cmd_type == CommandType.EXPORT_ENGAGEMENT:
            from src.workers.gamification import run_export_engagement_command
            await run_export_engagement_command(channel, sender_id, target)
        elif cmd_type == CommandType.SET_GRADE_COMPONENT:
            from src.workers.gamification import run_set_grade_component_command
            await run_set_grade_component_command(channel, sender_id, target, extra)
        elif cmd_type == CommandType.QUIZ_PERFORMANCE:
            await run_quiz_performance_command(channel, sender_id, target)
        elif cmd_type == CommandType.AT_RISK:
            await run_at_risk_command(channel, sender_id, target)
        elif cmd_type == CommandType.FREQUENT_DOUBTS:
            await run_frequent_doubts_command(channel, sender_id, target)
        elif cmd_type == CommandType.VIEW_STUDENT:
            await run_view_student_command(channel, sender_id, target)
        elif cmd_type == CommandType.RESET_STUDENT:
            await run_reset_student_command(channel, sender_id, target)
        elif cmd_type == CommandType.CLEAR_QUIZ_BANK:
            await run_clear_quiz_bank_command(channel, sender_id, target)
        elif cmd_type == CommandType.ZERO_LESSON:
            await run_zero_lesson_command(channel, sender_id, target)
        elif cmd_type == CommandType.DELETE_MATERIAL:
            await run_delete_material_command(channel, sender_id, target)
        elif cmd_type == CommandType.BROADCAST_NOTICE:
            await run_broadcast_notice_command(channel, sender_id, target, extra)
        else:
            # ── Addon de Livros (professor) — handlers em reading_commands ──
            from src.workers import reading_commands
            if cmd_type == CommandType.READING_PLAN:
                await reading_commands.run_reading_plan_command(channel, sender_id, target, extra)
            elif cmd_type == CommandType.SIMULADO:
                await reading_commands.run_simulado_command(channel, sender_id, target, extra)
            elif cmd_type == CommandType.LINK_LESSON_CHAPTER:
                await reading_commands.run_link_lesson_chapter_command(channel, sender_id, target, extra)
            elif cmd_type == CommandType.ERRATA:
                await reading_commands.run_errata_command(channel, sender_id, target, extra)
            elif cmd_type == CommandType.ACTIVATE_CHAPTERS:
                await reading_commands.run_activate_chapters_command(channel, sender_id, target, extra)
            elif cmd_type == CommandType.ANSWER_KEY:
                await reading_commands.run_answer_key_command(channel, sender_id, target, extra)
            elif cmd_type == CommandType.DIFFICULTY_MAP:
                await reading_commands.run_difficulty_map_command(channel, sender_id, target)
            elif cmd_type == CommandType.COMPLETION_FUNNEL:
                await reading_commands.run_completion_funnel_command(channel, sender_id, target)
            elif cmd_type == CommandType.DOUBTS_CORPUS:
                await reading_commands.run_doubts_corpus_command(channel, sender_id, target)
            # ── Horizontes H1-H3 ────────────────────────────────────────────
            elif cmd_type == CommandType.CLASS_CHALLENGE:
                from src.workers.challenges import run_class_challenge_command
                await run_class_challenge_command(channel, sender_id, target)
            elif cmd_type == CommandType.KEY_STATS:
                from src.workers.activation_worker import run_key_stats_command
                await run_key_stats_command(channel, sender_id, target)
            elif cmd_type == CommandType.RUBRIC:
                from src.workers.onboarding_commands import run_rubric_command
                await run_rubric_command(channel, sender_id, target, extra)
        return

    # 2) Comandos legados
    command, args = parse_command(text_content)
    logger.info("process_command_message", sender_id=sender_id, command=command, args=args)
    
    if command == CommandType.SYNC_STUDENTS:
        await run_sync_alunos_command(channel, sender_id)
    elif command == CommandType.SYNC_MATERIALS:
        await run_sync_materiais_command(channel, sender_id)
    elif command == CommandType.LIST_COURSES:
        await run_list_cursos_command(channel, sender_id)
    elif command == CommandType.LIST_CLASSES:
        await run_list_classes_command(channel, sender_id)
    elif command == CommandType.LIST_MATERIALS:
        await run_list_materiais_command(channel, sender_id, args)
    elif command == CommandType.UPDATE_LESSON:
        await run_update_lesson_command(channel, sender_id, args)
    elif command == CommandType.SEND_QUIZ:
        await run_broadcast_quiz_command(channel, sender_id, args)
    elif command == CommandType.HELP:
        await channel.send_text(sender_id, CMD_HELP_TEXT)
    elif command == CommandType.WAKEUP:
        logger.info("waking_up_worker_via_hack")
        return
    else:
        await channel.send_text(sender_id, "❌ Comando desconhecido. Digite /ajuda para ver a lista de comandos.")


# ==============================================================================
# CONSUMER LOOPS
# ==============================================================================

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


async def handle_outbound_message(payload: dict, ctx: MessageContext):
    """Consome a fila `outbound_messages` e entrega a mensagem no canal do
    destinatário. Usado pelo /aviso (e por qualquer produtor futuro de
    mensagens proativas)."""
    try:
        recipient = str(payload.get("recipient_id", ""))
        text_content = payload.get("text", "")
        channel_type = payload.get("channel")

        if not recipient or not text_content:
            await ctx.dead_letter(reason="InvalidOutboundPayload", description=str(payload)[:300])
            return

        ch = get_channel(channel_type)
        await ch.send_text(recipient, text_content)
        await ctx.complete()
        logger.info("outbound_message_delivered", recipient=recipient[:8] + "***", channel=channel_type)
    except Exception as e:
        logger.error("outbound_delivery_failed", error=str(e)[:300])
        await ctx.dead_letter(reason="OutboundDeliveryError", description=str(e)[:500])


async def outbound_loop():
    logger.info("outbound_worker_starting", environment=settings.ENVIRONMENT, worker_type="outbound")

    from src.queue.factory import get_queue
    queue = get_queue()
    await queue.consume_loop("outbound_messages", handle_outbound_message, use_sessions=False)


async def _standalone():
    """Execução dedicada: `python -m src.workers.command_worker` consome
    command_messages + outbound_messages no mesmo processo."""
    import asyncio
    await asyncio.gather(command_loop(), outbound_loop())


if __name__ == "__main__":
    import asyncio
    from src.shared.telemetry import setup_telemetry
    setup_telemetry(settings.LOG_LEVEL)
    asyncio.run(_standalone())
