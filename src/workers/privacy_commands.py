"""
LGPD — direitos dos titulares (Plano P1.2 + P1.6).

  - `/apagar_meus_dados` (Art. 18): confirmação em 2 passos → expurgo.
      • Leitor QR (plataforma = controladora): anonimização IMEDIATA
        (nome/telefone/telegram viram tombstone; histórico Redis apagado;
        conteúdo de interações anulado).
      • Aluno de escola (plataforma = operadora): pedido REGISTRADO e
        ENCAMINHADO ao controlador (professor) — aluno é desativado e para de
        receber mensagens até a confirmação.
    Todo pedido vira linha em `DataSubjectRequests` (prova de atendimento).

  - "REVISAR" pós-correção de redação (Art. 20): direito à revisão humana —
    aviso ao professor pela fila outbound.

Best-effort em volta de tudo: falha de privacidade é logada, nunca silenciosa
para o titular (em erro, o aluno recebe ERROR_TECHNICAL do chamador).
"""

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

import structlog
from sqlalchemy import text

from src.db.database import AsyncSessionLocal
from src.shared.config import settings
from src.shared.constants.messages import (
    ESSAY_REVIEW_REQUESTED_ACK,
    ESSAY_REVIEW_TEACHER_NOTICE,
    LGPD_DELETE_CANCELLED,
    LGPD_DELETE_CONFIRM_PROMPT,
    LGPD_DELETE_DONE,
    LGPD_DELETE_FORWARDED,
    LGPD_DELETE_TEACHER_NOTICE,
)
from src.shared.privacy import first_name, mask_contact
from src.shared.telemetry import track_event

logger = structlog.get_logger(__name__)

# Janela em que o "REVISAR" é aceito após uma correção de redação.
ESSAY_REVIEW_WINDOW_SECONDS = 60 * 60 * 48


def essay_review_window_key(student_id: str) -> str:
    return f"essay_review_window:{student_id}"


async def register_data_request(
    student_id: Optional[str],
    request_type: str,
    status: str,
    channel_name: Optional[str] = None,
    details: Optional[str] = None,
) -> str:
    """Trilha de auditoria (Art. 18) — registra o pedido e devolve o id."""
    request_id = str(uuid4())
    completed_at = datetime.now(timezone.utc).replace(tzinfo=None) if status == "completed" else None
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""
                INSERT INTO "DataSubjectRequests"
                    (id, student_id, request_type, status, channel, details, completed_at)
                VALUES (:id, :sid, :rtype, :status, :ch, :details, :done)
            """),
            {"id": request_id, "sid": student_id, "rtype": request_type,
             "status": status, "ch": channel_name, "details": details, "done": completed_at},
        )
        await session.commit()
    return request_id


async def erase_student_data(student_id: str, contact_keys: list[str]) -> None:
    """Expurgo/anonimização do titular (Art. 18 — esquecimento).

    PG: tombstone em nome/telefone/telegram (phone é UNIQUE NOT NULL — o
    tombstone libera o número para um eventual recadastro futuro), pausa
    planos de leitura e anula o CONTEÚDO das interações (o evento agregado
    sobrevive sem titular). Redis: histórico, role e estados de fluxo.

    NOTA: dados de aluno NÃO vivem no Qdrant (lá é material do professor) —
    não há nada vetorial a apagar aqui.
    """
    tombstone = f"removido:{student_id[:12]}"
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("""
                UPDATE "Students"
                SET name = '(removido a pedido — LGPD)',
                    phone = :tomb,
                    telegram_chat_id = NULL,
                    guardian_phone = NULL,
                    preferred_channel = NULL,
                    is_active = false,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :sid
            """),
            {"sid": student_id, "tomb": tombstone},
        )
        await session.execute(
            text("""
                UPDATE "ReadingPlans"
                SET is_paused = true, next_trigger_at = NULL, next_checkin_at = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE student_id = :sid
            """),
            {"sid": student_id},
        )
        await session.execute(
            text('UPDATE "StudentInteractions" SET message_content = NULL WHERE student_id = :sid'),
            {"sid": student_id},
        )
        await session.commit()

    try:
        from src.cache.redis_client import get_redis
        redis = await get_redis()
        for key in contact_keys:
            if not key:
                continue
            await redis.delete(
                f"chat_history:{key}", f"role:{key}", f"buffer:{key}",
                f"debounce:{key}", f"essay_pending:{key}",
            )
        await redis.delete(essay_review_window_key(student_id))
    except Exception as e:
        logger.warning("erase_redis_cleanup_failed", error=str(e)[:200])

    logger.info("lgpd_student_erased", student_id=student_id)


async def run_delete_my_data_command(channel, sender_id: str):
    """/apagar_meus_dados — passo 1: confirmação explícita por botões."""
    await channel.send_buttons(
        sender_id,
        LGPD_DELETE_CONFIRM_PROMPT,
        buttons=[
            {"id": "lgpd_delete_confirm", "title": "Sim, apagar"},
            {"id": "lgpd_delete_cancel", "title": "Cancelar"},
        ],
    )


async def handle_delete_decision(channel, sender_id: str, student_context: dict, confirmed: bool) -> None:
    """Passo 2 (clique do botão): executa ou encaminha ao controlador."""
    if not confirmed:
        await channel.send_text(sender_id, LGPD_DELETE_CANCELLED)
        return

    student = student_context["student"]
    teacher = student_context["teacher"]
    course = student_context["course"]

    contact_keys = [k for k in (student.phone, student.telegram_chat_id, sender_id) if k]

    if student.registration_source == "qr_activation":
        # Somos controladores: expurgo imediato.
        request_id = await register_data_request(
            student.id, "erasure", "completed", channel.channel_name, "auto-cadastro QR — expurgo imediato",
        )
        await erase_student_data(student.id, contact_keys)
        await channel.send_text(sender_id, LGPD_DELETE_DONE)
    else:
        # Somos operadores: registra, desativa (para de receber) e encaminha
        # ao controlador (professor/escola) concluir.
        request_id = await register_data_request(
            student.id, "erasure", "forwarded", channel.channel_name, f"aluno de planilha — encaminhado ao professor {teacher.id}",
        )
        async with AsyncSessionLocal() as session:
            await session.execute(
                text('UPDATE "Students" SET is_active = false, updated_at = CURRENT_TIMESTAMP WHERE id = :sid'),
                {"sid": student.id},
            )
            await session.commit()

        try:
            from src.queue.factory import get_queue
            default_ct = settings.CHANNEL_BACKEND.lower()
            ct = "telegram" if (default_ct == "telegram" and teacher.telegram_chat_id) else "whatsapp"
            address = teacher.telegram_chat_id if ct == "telegram" else teacher.phone
            if address:
                await get_queue().enqueue("outbound_messages", {
                    "recipient_id": address,
                    "channel": ct,
                    "text": LGPD_DELETE_TEACHER_NOTICE.format(
                        student_name=student.name,
                        masked_contact=mask_contact(sender_id),
                        request_id=request_id,
                    )[:4000],
                    "origin": "lgpd_erasure_forward",
                })
        except Exception as e:
            logger.error("lgpd_forward_notice_failed", error=str(e)[:200])

        await channel.send_text(sender_id, LGPD_DELETE_FORWARDED)

    track_event("lgpd_erasure_requested", {
        "student_id": student.id,
        "course_id": course.id,
        "self_service": student.registration_source == "qr_activation",
        "request_id": request_id,
    })


# ==============================================================================
# Art. 20 — revisão humana da correção de redação ("REVISAR")
# ==============================================================================

async def open_essay_review_window(student_id: str) -> None:
    """Chamado pelo fluxo de redação após entregar a correção da IA."""
    try:
        from src.cache.redis_client import get_redis
        redis = await get_redis()
        await redis.set(essay_review_window_key(student_id), "1", ex=ESSAY_REVIEW_WINDOW_SECONDS)
    except Exception as e:
        logger.warning("essay_review_window_failed", error=str(e)[:200])


async def request_essay_review(channel, sender_id: str, student_context: dict) -> bool:
    """Aluno respondeu REVISAR. True = pedido tratado (chamador encerra o
    fluxo); False = não havia correção recente (segue o pipeline normal —
    'revisar' pode ser uma pergunta legítima de estudo)."""
    student = student_context["student"]
    try:
        from src.cache.redis_client import get_redis
        redis = await get_redis()
        if not await redis.get(essay_review_window_key(student.id)):
            return False
        await redis.delete(essay_review_window_key(student.id))
    except Exception:
        return False

    teacher = student_context["teacher"]
    course = student_context["course"]
    subject = student_context.get("subject")
    class_label = course.name + (f" — {subject.name}" if subject else "")

    await register_data_request(
        student.id, "review_essay", "forwarded", channel.channel_name, f"professor {teacher.id}",
    )
    try:
        from src.queue.factory import get_queue
        default_ct = settings.CHANNEL_BACKEND.lower()
        ct = "telegram" if (default_ct == "telegram" and teacher.telegram_chat_id) else "whatsapp"
        address = teacher.telegram_chat_id if ct == "telegram" else teacher.phone
        if address:
            await get_queue().enqueue("outbound_messages", {
                "recipient_id": address,
                "channel": ct,
                "text": ESSAY_REVIEW_TEACHER_NOTICE.format(
                    student_name=first_name(student.name), class_name=class_label,
                )[:4000],
                "origin": "essay_human_review",
            })
    except Exception as e:
        logger.error("essay_review_notice_failed", error=str(e)[:200])

    await channel.send_text(sender_id, ESSAY_REVIEW_REQUESTED_ACK)
    track_event("essay_human_review_requested", {"student_id": student.id, "course_id": course.id})
    return True
