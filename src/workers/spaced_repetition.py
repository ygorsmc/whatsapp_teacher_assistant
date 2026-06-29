"""
Spaced Repetition — Revisão Espaçada Automática (Fase 4 do Addon de Livros).

A técnica de aprendizagem com maior respaldo em evidência científica: reaplicar
quizzes do QuizBank em D+3, D+10 e D+30 após a primeira resposta.

Escada de intervalos:
    1ª resposta (acerto OU erro) → agenda D+3
    acertou em D+3  → D+10
    acertou em D+10 → D+30
    acertou em D+30 → status 'completed'
    errou em qualquer revisão → volta para D+3 (recomeça o ciclo)

Custo $0 por design: a reentrega lê a questão pronta do QuizBank (cache hit
puro — zero LLM, zero RAG). O `ease_factor` é registrado no espírito do SM-2
(2.5 inicial, +0.1 acerto, -0.2 erro, piso 1.3) para evoluções futuras; a
escada atual é fixa por simplicidade e previsibilidade.

Integração:
  - `register_quiz_answer()`     ← llm_worker.handle_quiz_response (toda
    resposta de quiz com quiz_bank_id passa por aqui).
  - `process_spaced_repetition()` ← scheduler_worker (loop de 5 min).
"""

import json
import random
from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import uuid4

import structlog
from sqlalchemy import text

from src.shared.config import settings
from src.shared.telemetry import track_event
from src.db.database import AsyncSessionLocal
from src.channel.factory import get_channel
from src.shared.constants.messages import SPACED_REVIEW_INTRO

logger = structlog.get_logger(__name__)

# Escada D+3 → D+10 → D+30 (depois 'completed')
SRS_INTERVALS = [3, 10, 30]
EASE_START, EASE_MIN, EASE_MAX = 2.5, 1.3, 3.0


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ==============================================================================
# HELPERS PUROS (testáveis sem I/O)
# ==============================================================================

def next_interval_on_correct(interval_days: int) -> Optional[int]:
    """Próximo degrau da escada após um ACERTO. None = ciclo completo (D+30 ok)."""
    for i, step in enumerate(SRS_INTERVALS):
        if interval_days <= step:
            return SRS_INTERVALS[i + 1] if i + 1 < len(SRS_INTERVALS) else None
    return None


def adaptive_next_interval(interval_days: int, ease_factor: float) -> Optional[int]:
    """H3.2 — SM-2 pleno (flag SRS_ADAPTIVE_ENABLED): o intervalo cresce pelo
    ease_factor INDIVIDUAL do aluno em vez da escada fixa.

    Quem domina (ease alto) espaça rápido: 3 → 8 → 20 → 30 → completed.
    Quem patina (ease no piso 1.3) revisa mais: 3 → 4 → 5 → 7 → 9 → ... → 30.
    Ciclo completa quando o aluno acerta uma revisão de 30+ dias (mesmo
    critério pedagógico da escada fixa).
    """
    if interval_days >= 30:
        return None  # acertou a revisão de 30 dias → memória de longo prazo
    nxt = max(interval_days + 1, round(interval_days * ease_factor))
    return min(nxt, 30)


def adjust_ease(ease_factor: float, is_correct: bool) -> float:
    """Ajuste SM-2 simplificado: +0.1 acerto, -0.2 erro, clamp [1.3, 3.0]."""
    ease = ease_factor + (0.1 if is_correct else -0.2)
    return max(EASE_MIN, min(EASE_MAX, round(ease, 2)))


# ==============================================================================
# REGISTRO DE RESPOSTA (chamado a CADA quiz respondido com quiz_bank_id)
# ==============================================================================

async def register_quiz_answer(student_id: str, quiz_bank_id: str, is_correct: bool) -> None:
    """Atualiza a fila de revisão espaçada após uma resposta de quiz.

    - Primeira resposta da dupla (aluno, questão): INSERE com D+3 (acertando
      ou errando — D+3 já é o início do ciclo, então o "reset" do erro coincide
      com o estado inicial).
    - Resposta de uma REVISÃO agendada: sobe o degrau (acerto) ou reseta para
      D+3 (erro). Acertou em D+30 → 'completed'.
    - Questão 'completed' que reaparece (ex.: simulado) e o aluno ERRA →
      reativa o ciclo em D+3 (a memória decaiu; a evidência manda revisar).

    Best-effort: falha aqui NUNCA quebra o feedback do quiz ao aluno.
    """
    now = _naive_utc_now()
    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                text("""
                    SELECT id, interval_days, repetition_count, ease_factor, status
                    FROM "SpacedRepetitionQueue"
                    WHERE student_id = :sid AND quiz_bank_id = :qbid
                    FOR UPDATE
                """),
                {"sid": student_id, "qbid": quiz_bank_id},
            )
            row = res.fetchone()

            if not row:
                # Primeira vez: entra no ciclo com D+3.
                await session.execute(
                    text("""
                        INSERT INTO "SpacedRepetitionQueue"
                            (id, student_id, quiz_bank_id, next_review_at,
                             interval_days, repetition_count, ease_factor, status)
                        VALUES
                            (:id, :sid, :qbid, :next, 3, 1, :ease, 'pending')
                    """),
                    {
                        "id": str(uuid4()),
                        "sid": student_id,
                        "qbid": quiz_bank_id,
                        "next": now + timedelta(days=3),
                        "ease": adjust_ease(EASE_START, is_correct),
                    },
                )
                await session.commit()
                track_event("srs_enrolled", {"student_id": student_id, "quiz_bank_id": quiz_bank_id})
                return

            new_ease = adjust_ease(row.ease_factor, is_correct)

            if row.status == "completed" and is_correct:
                # Ciclo já fechado e o aluno continua acertando: nada a fazer.
                return

            if is_correct:
                # Escada fixa (default) ou intervalos individuais (H3.2, flag)
                if settings.SRS_ADAPTIVE_ENABLED:
                    nxt = adaptive_next_interval(row.interval_days, row.ease_factor)
                else:
                    nxt = next_interval_on_correct(row.interval_days)
                if nxt is None:
                    await session.execute(
                        text("""
                            UPDATE "SpacedRepetitionQueue"
                            SET status = 'completed', repetition_count = repetition_count + 1,
                                ease_factor = :ease, updated_at = CURRENT_TIMESTAMP
                            WHERE id = :id
                        """),
                        {"ease": new_ease, "id": row.id},
                    )
                    track_event("srs_completed", {"student_id": student_id, "quiz_bank_id": quiz_bank_id})
                else:
                    await session.execute(
                        text("""
                            UPDATE "SpacedRepetitionQueue"
                            SET interval_days = :interval, next_review_at = :next,
                                repetition_count = repetition_count + 1,
                                ease_factor = :ease, status = 'pending',
                                updated_at = CURRENT_TIMESTAMP
                            WHERE id = :id
                        """),
                        {"interval": nxt, "next": now + timedelta(days=nxt), "ease": new_ease, "id": row.id},
                    )
            else:
                # Errou: recomeça o ciclo em D+3 (inclusive se estava completed).
                await session.execute(
                    text("""
                        UPDATE "SpacedRepetitionQueue"
                        SET interval_days = 3, next_review_at = :next,
                            repetition_count = repetition_count + 1,
                            ease_factor = :ease, status = 'pending',
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = :id
                    """),
                    {"next": now + timedelta(days=3), "ease": new_ease, "id": row.id},
                )

            await session.commit()
    except Exception as e:
        logger.error("srs_register_failed", student_id=student_id, error=str(e)[:300])


# ==============================================================================
# SCHEDULER — REENTREGA DAS REVISÕES VENCIDAS (cache hit puro)
# ==============================================================================

def _shuffled_quiz_from_bank(bank: dict):
    """Monta um QuizOutput a partir da linha do QuizBank com randomização
    algorítmica das alternativas (anti-cola: a correta muda de letra a cada
    entrega — mesma regra do review_generator)."""
    from src.rag.quiz_generator import QuizOutput, QuizAlternative

    alts_raw = json.loads(bank["alternatives"]) if bank.get("alternatives") else []
    correct_text = next((a["text"] for a in alts_raw if a["id"] == bank["correct_answer"]), None)

    random.shuffle(alts_raw)
    new_ids = ["A", "B", "C", "D", "E"]
    correct_id = bank["correct_answer"]
    alternatives = []
    for i, alt in enumerate(alts_raw):
        alt_id = new_ids[i]
        alternatives.append(QuizAlternative(id=alt_id, text=alt["text"]))
        if correct_text is not None and alt["text"] == correct_text:
            correct_id = alt_id

    return QuizOutput(
        question=bank["question_text"],
        alternatives=alternatives,
        correct_answer=correct_id,
        explanation=bank["explanation"],
    )


async def process_spaced_repetition():
    """Varre a SpacedRepetitionQueue vencida e reentrega o quiz salvo no
    QuizBank (sem LLM). Se o aluno estiver fora da janela de 24h do WhatsApp,
    empurra a revisão em +1 dia (a janela do Telegram não existe)."""
    now_utc = _naive_utc_now()
    default_ct = settings.CHANNEL_BACKEND.lower()

    async with AsyncSessionLocal() as session:
        from src.shared.privacy import guardian_consent_sql_clause, messaging_optout_clause
        consent_clause = guardian_consent_sql_clause("s") + messaging_optout_clause("s")  # P1.1 + F9 opt-out
        result = await session.execute(
            text(f"""
                SELECT srq.id AS srq_id, srq.student_id, srq.quiz_bank_id, srq.interval_days,
                       s.name AS student_name, s.phone, s.telegram_chat_id, s.preferred_channel,
                       qb.course_id, qb.lesson_number, qb.material_type, c.name AS course_name,
                       (SELECT MAX(si.timestamp) FROM "StudentInteractions" si
                        WHERE si.student_id = srq.student_id
                          AND si.interaction_type IN ('message_received', 'quiz_answered')
                       ) AS last_interaction
                FROM "SpacedRepetitionQueue" srq
                JOIN "Students" s ON s.id = srq.student_id
                JOIN "QuizBank" qb ON qb.id = srq.quiz_bank_id
                LEFT JOIN "Courses" c ON c.id = qb.course_id
                WHERE srq.status = 'pending'
                  AND srq.next_review_at <= :now
                  AND s.is_active = true
                  {consent_clause}
                FOR UPDATE OF srq SKIP LOCKED
            """),
            {"now": now_utc},
        )
        due = result.fetchall()

        if not due:
            return

        logger.info("srs_reviews_due", count=len(due))
        twenty_four_hours_ago = now_utc - timedelta(hours=24)

        for item in due:
            try:
                ct = (item.preferred_channel or default_ct).lower()
                sender_id = item.telegram_chat_id if ct == "telegram" else item.phone

                def _postpone_sql():
                    return text("""
                        UPDATE "SpacedRepetitionQueue"
                        SET next_review_at = :next, updated_at = CURRENT_TIMESTAMP
                        WHERE id = :id
                    """)

                if not sender_id:
                    await session.execute(_postpone_sql(), {"next": now_utc + timedelta(days=1), "id": item.srq_id})
                    await session.commit()
                    continue

                # Janela de 24h: política da Meta — só restringe o WhatsApp.
                if ct == "whatsapp":
                    last_int = item.last_interaction
                    if isinstance(last_int, str):
                        last_int = datetime.fromisoformat(last_int)
                    if last_int is not None and last_int.tzinfo is not None:
                        last_int = last_int.replace(tzinfo=None)
                    if not (last_int and last_int > twenty_four_hours_ago):
                        await session.execute(_postpone_sql(), {"next": now_utc + timedelta(days=1), "id": item.srq_id})
                        await session.commit()
                        logger.info("srs_postponed_window_closed", srq_id=item.srq_id)
                        continue

                from src.rag.quiz_bank import get_quiz_package, find_unseen_quiz
                bank = await get_quiz_package(item.quiz_bank_id)
                if not bank:
                    # Questão invalidada entre o agendamento e a entrega
                    # (corrida com o CASCADE) — marca como pulada.
                    await session.execute(
                        text("""
                            UPDATE "SpacedRepetitionQueue"
                            SET status = 'skipped', updated_at = CURRENT_TIMESTAMP
                            WHERE id = :id
                        """),
                        {"id": item.srq_id},
                    )
                    await session.commit()
                    continue

                # Generalização de Tópicos: tenta achar uma variação irmã (mesmo tópico) inédita pro aluno
                sister_bank = await find_unseen_quiz(
                    student_id=item.student_id,
                    course_id=bank["course_id"],
                    subject_id=bank.get("subject_id"),
                    lesson_number=bank["lesson_number"],
                    topic=bank["topic"],
                    material_type=bank["material_type"],
                )
                
                quiz_bank_id_to_send = item.quiz_bank_id
                if sister_bank:
                    bank = sister_bank
                    quiz_bank_id_to_send = bank["id"]
                    # Atualiza a fila para apontar para a nova variação (para consistência futura)
                    await session.execute(
                        text('UPDATE "SpacedRepetitionQueue" SET quiz_bank_id = :qid WHERE id = :id'),
                        {"qid": quiz_bank_id_to_send, "id": item.srq_id}
                    )

                channel = get_channel(ct)
                quiz_output = _shuffled_quiz_from_bank(bank)

                await channel.send_text(sender_id, SPACED_REVIEW_INTRO)

                from src.workers.micro_learning import send_quiz_to_student
                await send_quiz_to_student(
                    channel=channel,
                    student={
                        "id": item.student_id,
                        "sender_id": sender_id,
                        "course_id": item.course_id,
                        "lesson_pointer": str(item.lesson_number),
                    },
                    quiz=quiz_output,
                    quiz_bank_id=quiz_bank_id_to_send,
                )

                # Vertical de Idiomas: reentrega de VOCABULÁRIO refala a palavra-alvo
                # (áudio persistido no QuizBank). Best-effort — não derruba o SRS.
                if item.material_type == "vocab" and bank.get("audio_script") and settings.LANGUAGE_MODE_ENABLED:
                    from src.shared.language import detect_language_course
                    from src.rag.review_generator import synthesize_listening
                    spec = detect_language_course(item.course_name, default_target=settings.DEFAULT_TARGET_LANGUAGE)
                    target_lang = spec.target_language if spec else settings.DEFAULT_TARGET_LANGUAGE
                    try:
                        audio = await synthesize_listening(bank["audio_script"], target_lang)
                        if audio:
                            # Transcrição logo abaixo da nota de voz (flashcard de
                            # pronúncia — ver a palavra/exemplo é o objetivo).
                            await channel.send_audio(sender_id, audio, caption=bank["audio_script"])
                    except Exception as e:
                        logger.warning("srs_vocab_audio_failed", srq_id=item.srq_id, error=str(e)[:200])

                # Empurra +1 dia até a RESPOSTA chegar: quem fecha o degrau é o
                # register_quiz_answer. Se o aluno ignorar, reentrega amanhã.
                await session.execute(_postpone_sql(), {"next": now_utc + timedelta(days=1), "id": item.srq_id})
                await session.commit()

                track_event("srs_review_sent", {
                    "student_id": item.student_id,
                    "quiz_bank_id": item.quiz_bank_id,
                    "interval_days": item.interval_days,
                })
                logger.info("srs_review_sent", srq_id=item.srq_id, interval=item.interval_days, channel=ct)

            except Exception as e:
                logger.error("srs_delivery_failed", srq_id=item.srq_id, error=str(e)[:400])
