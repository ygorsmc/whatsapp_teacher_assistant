"""
Activation Worker — Ativação por QR Code (H2.1 Livro Vivo / H3.1 Professor).

Fluxo (sempre WhatsApp — o QR é um deep link wa.me):
  1. O leitor escaneia o QR do material → WhatsApp abre com
     "Quero ativar meu material: LIVRO-XXXX-XXXX" → envia.
  2. O webhook (regex pré-compilado, SLA 200ms) roteia para cá com
     role="activation" — inclusive de remetentes DESCONHECIDOS (que o edge
     normalmente descarta).
  3. Chave válida → pedimos o NOME com o termo LGPDzinho. O estado fica no
     Redis (`activation_pending:{sender}`, TTL 1h) — é ele que faz o webhook
     deixar passar a PRÓXIMA mensagem (o nome) de um desconhecido.
  4. Nome recebido → cria o Student on-the-fly (registration_source=
     'qr_activation', consent_at=NOW), matricula na turma-coorte da chave,
     abre o ReadingPlan e consome a chave (auditoria em KeyActivations).

Decisão de arquitetura: o leitor NÃO é uma entidade nova — é um Student.
Todo o pipeline (ReadingPlans, QuizBank, revisão espaçada, janela de 24h,
telemetria) é herdado sem fork.

Chave PROF- (H3.1): mesmo fluxo, mas cria um Teacher e envia o guia de
onboarding self-service (compartilhar Drive + /criar_turma + /adicionar_alunos).
"""

import json
from datetime import datetime, timezone
from typing import cast
from uuid import uuid4

from sqlalchemy import text

from src.shared.config import settings
from src.shared.telemetry import logger, track_event
from src.db.database import AsyncSessionLocal
from src.cache.redis_client import get_redis
from src.shared.constants.commands import extract_activation_code
from src.shared.constants.messages import (
    ACTIVATION_ASK_NAME,
    ACTIVATION_WELCOME,
    ACTIVATION_WELCOME_BACK,
    ACTIVATION_KEY_INVALID,
    ACTIVATION_KEY_USED,
    ACTIVATION_KEY_REVOKED,
    ACTIVATION_NAME_TOO_SHORT,
    TEACHER_ACTIVATION_ASK_NAME,
    TEACHER_ACTIVATION_WELCOME,
    LEAVE_BOOK_CONFIRMED,
    LEAVE_BOOK_NOT_ALLOWED,
)

PENDING_TTL_SECONDS = 3600  # 1h para o usuário responder com o nome

MATERIAL_LABELS = {"lesson": "material", "book": "livro", "workbook": "apostila"}


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _pending_key(sender_id: str) -> str:
    return f"activation_pending:{sender_id}"


async def has_pending_activation(sender_id: str) -> bool:
    """Consultado pelo webhook para deixar passar a resposta (nome) de um
    remetente desconhecido em processo de ativação."""
    try:
        redis = await get_redis()
        return bool(await redis.exists(_pending_key(sender_id)))
    except Exception:
        return False


async def _load_key(session, key: str):
    res = await session.execute(
        text('''SELECT ak.key, ak.kind, ak.class_id, ak.max_activations,
                       ak.activation_count, ak.status,
                       cl.course_id, cl.subject_id, cl.name AS class_name,
                       c.name AS course_name,
                       sub.name AS subject_name
                FROM "ActivationKeys" ak
                LEFT JOIN "Classes" cl ON cl.id = ak.class_id
                LEFT JOIN "Courses" c ON c.id = cl.course_id
                LEFT JOIN "Subjects" sub ON sub.id = cl.subject_id
                WHERE ak.key = :key
                FOR UPDATE OF ak'''),
        {"key": key},
    )
    return res.fetchone()


async def _discover_material(course_id: str, course_name: str, subject_name: str | None) -> tuple[str, int]:
    """Descobre (material_type, total_sections) do livro/apostila da turma.

    1º: copia de um ReadingPlan existente do mesmo escopo (outros leitores);
    2º: varre a árvore do Drive (mesma lógica do /plano_leitura);
    Fallback: ('book', 1) — o plano nasce e o professor pode recriar depois.
    """
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            text('''SELECT material_type, total_sections FROM "ReadingPlans"
                    WHERE course_id = :cid
                    ORDER BY updated_at DESC LIMIT 1'''),
            {"cid": course_id},
        )
        row = res.fetchone()
        if row:
            return row.material_type, row.total_sections

    try:
        from src.drive.factory import get_material_source
        files = await get_material_source().list_materials(settings.GOOGLE_DRIVE_FOLDER_ID)
        for wanted in ("book", "workbook"):
            sections = [
                f.lesson_number for f in files
                if f.material_type == wanted
                and f.course_name == course_name
                and (not subject_name or f.subject_name == subject_name)
            ]
            if sections:
                return wanted, max(sections)
    except Exception as e:
        logger.warning("activation_material_discovery_failed", error=str(e)[:200])

    return "book", 1


async def process_activation_message(payload: dict):
    """Entrada única do fluxo de ativação (chamado pelo command_worker quando
    role='activation'). Decide entre: validar chave / receber o nome."""
    from src.channel.factory import get_channel

    sender_id = str(payload.get("sender_id", ""))
    text_content = payload.get("text", "") or ""
    channel = get_channel(payload.get("channel"))

    code = extract_activation_code(text_content)
    redis = await get_redis()

    if code:
        await _handle_key_submission(channel, redis, sender_id, code)
        return

    # Sem código: só faz sentido se houver ativação pendente (resposta = nome)
    raw = await redis.get(_pending_key(sender_id))
    if not raw:
        # Estado expirou (TTL 1h): orienta a escanear de novo.
        await channel.send_text(sender_id, ACTIVATION_KEY_INVALID)
        return
    pending = json.loads(raw)
    await _handle_name_submission(channel, redis, sender_id, text_content.strip(), pending)


async def _handle_key_submission(channel, redis, sender_id: str, code: str):
    async with AsyncSessionLocal() as session:
        key_row = await _load_key(session, code)

        if not key_row:
            await channel.send_text(sender_id, ACTIVATION_KEY_INVALID)
            track_event("activation_key_invalid", {"sender": sender_id[:8] + "***"})
            return
        if key_row.status == "revoked":
            await channel.send_text(sender_id, ACTIVATION_KEY_REVOKED)
            return
        if key_row.activation_count >= key_row.max_activations:
            await channel.send_text(sender_id, ACTIVATION_KEY_USED)
            return
        if key_row.kind == "reader" and not key_row.class_id:
            logger.error("activation_key_without_class", key=code)
            await channel.send_text(sender_id, ACTIVATION_KEY_INVALID)
            return

        # Leitor JÁ cadastrado (2º livro, recompra): pula a etapa do nome.
        existing = (await session.execute(
            text('SELECT id, name FROM "Students" WHERE phone = :p AND is_active = true'),
            {"p": sender_id},
        )).fetchone()

    if key_row.kind == "reader" and existing:
        material_label = await _finalize_reader(sender_id, existing[0], key_row, channel_name=channel.channel_name)
        await channel.send_text(sender_id, ACTIVATION_WELCOME_BACK.format(
            student_name=existing[1].split(" ")[0], material_label=material_label,
        ))
        return

    # 1º contato: guarda o estado e pede o nome (consentimento LGPD no texto).
    await redis.set(
        _pending_key(sender_id),
        json.dumps({"key": code, "kind": key_row.kind}),
        ex=PENDING_TTL_SECONDS,
    )
    if key_row.kind == "teacher":
        await channel.send_text(sender_id, TEACHER_ACTIVATION_ASK_NAME.format(key=code))
    else:
        # subject/course podem estar no key_row; material amigável genérico
        await channel.send_text(sender_id, ACTIVATION_ASK_NAME.format(
            key=code, material_label="material",
        ))
    track_event("activation_started", {"key": code, "kind": key_row.kind})


async def _handle_name_submission(channel, redis, sender_id: str, name: str, pending: dict):
    if len(name) < 2 or name.startswith("/"):
        await channel.send_text(sender_id, ACTIVATION_NAME_TOO_SHORT)
        return

    code = pending["key"]
    kind = pending.get("kind", "reader")
    now = _naive_utc_now()

    async with AsyncSessionLocal() as session:
        key_row = await _load_key(session, code)
        if not key_row or key_row.status == "revoked" or key_row.activation_count >= key_row.max_activations:
            await redis.delete(_pending_key(sender_id))
            await channel.send_text(sender_id, ACTIVATION_KEY_INVALID)
            return

        if kind == "teacher":
            # H3.1 — cria o professor self-service
            teacher_id = f"tea_{uuid4().hex[:8]}"
            res = await session.execute(
                text('''INSERT INTO "Teachers"
                            (id, name, subject, phone, assistant_persona, out_of_scope_message, is_active)
                        VALUES (:id, :name, 'Diversos', :phone,
                                'Seja amigável, didático e direto.', 'Isso está fora do escopo do curso.', true)
                        ON CONFLICT(phone) DO UPDATE SET name = excluded.name, is_active = true
                        RETURNING id'''),
                {"id": teacher_id, "name": name, "phone": sender_id},
            )
            teacher_id = cast(str, res.scalar())  # RETURNING id após INSERT/UPSERT
            await _consume_key(session, code, teacher_id=teacher_id, channel_name=channel.channel_name, now=now)
            await session.commit()

        else:
            # H2.1 — cria o leitor (Student) com consentimento LGPD
            student_id = f"stu_{uuid4().hex[:8]}"
            res = await session.execute(
                text('''INSERT INTO "Students"
                            (id, name, phone, is_active, registration_source, consent_at, preferred_channel)
                        VALUES (:id, :name, :phone, true, 'qr_activation', :consent, :channel)
                        ON CONFLICT(phone) DO UPDATE SET
                            name = excluded.name, is_active = true,
                            consent_at = COALESCE("Students".consent_at, excluded.consent_at)
                        RETURNING id'''),
                {"id": student_id, "name": name, "phone": sender_id,
                 "consent": now, "channel": channel.channel_name},
            )
            student_id = cast(str, res.scalar())  # RETURNING id após INSERT/UPSERT
            await session.commit()

    await redis.delete(_pending_key(sender_id))

    if kind == "teacher":
        await channel.send_text(sender_id, TEACHER_ACTIVATION_WELCOME.format(
            teacher_name=name.split(" ")[0],
            service_account_email=_service_account_email(),
        ))
        track_event("teacher_activated", {"key": code})
        return

    material_label = await _finalize_reader(sender_id, student_id, key_row, channel_name=channel.channel_name)

    # Limpa o cache de role: o próximo webhook deste número já o vê como aluno.
    try:
        await redis.delete(f"role:{sender_id}")
    except Exception:
        pass

    await channel.send_text(sender_id, ACTIVATION_WELCOME.format(
        student_name=name.split(" ")[0], material_label=material_label,
    ))
    track_event("reader_activated", {"key": code, "student_id": student_id})


async def _finalize_reader(sender_id: str, student_id: str, key_row, channel_name: str) -> str:
    """Matrícula na turma-coorte + ReadingPlan + consumo da chave. Retorna o
    rótulo do material ('livro'/'apostila') para a mensagem de boas-vindas."""
    now = _naive_utc_now()
    material_type, total_sections = await _discover_material(
        key_row.course_id, key_row.course_name, key_row.subject_name
    )

    from src.workers.reading_journey import compute_reading_next_trigger
    next_trigger = compute_reading_next_trigger("daily", "07:00")

    async with AsyncSessionLocal() as session:
        # Matrícula (idempotente)
        await session.execute(
            text('''INSERT INTO "StudentEnrollment" (enrollment_id, student_id, class_id)
                    VALUES (:id, :sid, :cid)
                    ON CONFLICT(student_id, class_id) DO NOTHING'''),
            {"id": f"enr_{uuid4().hex[:8]}", "sid": student_id, "cid": key_row.class_id},
        )

        # ReadingPlan (SELECT→UPDATE/INSERT: subject_id NULL não deduplica via UNIQUE)
        subject_clause = "subject_id = :sub" if key_row.subject_id else "subject_id IS NULL"
        res = await session.execute(
            text(f'''SELECT id FROM "ReadingPlans"
                     WHERE student_id = :sid AND course_id = :cid
                       AND {subject_clause} AND material_type = :mat'''),
            {"sid": student_id, "cid": key_row.course_id, "mat": material_type,
             **({"sub": key_row.subject_id} if key_row.subject_id else {})},
        )
        plan_id = res.scalar_one_or_none()
        if plan_id:
            await session.execute(
                text('''UPDATE "ReadingPlans"
                        SET is_paused = false, next_trigger_at = :next,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = :pid'''),
                {"next": next_trigger, "pid": plan_id},
            )
        else:
            await session.execute(
                text('''INSERT INTO "ReadingPlans"
                            (id, student_id, course_id, subject_id, material_type,
                             total_sections, current_section, pace, preferred_time,
                             streak_days, is_paused, next_trigger_at)
                        VALUES (:id, :sid, :cid, :sub, :mat, :total, 0, 'daily', '07:00',
                                0, false, :next)'''),
                {"id": str(uuid4()), "sid": student_id, "cid": key_row.course_id,
                 "sub": key_row.subject_id, "mat": material_type,
                 "total": total_sections, "next": next_trigger},
            )

        await _consume_key(session, key_row.key, student_id=student_id, channel_name=channel_name, now=now)
        await session.commit()

    return MATERIAL_LABELS.get(material_type, "material")


async def _consume_key(session, key: str, now: datetime, channel_name: str,
                       student_id: str | None = None, teacher_id: str | None = None):
    """Consome UMA ativação da chave — IDEMPOTENTE por titular.

    Anti-duplo-consumo: o MESMO número (Student) ou professor (Teacher) não
    consome a mesma chave duas vezes. A auditoria (`KeyActivations`) é inserida
    com `ON CONFLICT DO NOTHING` sobre o índice único parcial `(key, student_id)`
    / `(key, teacher_id)`; o `activation_count` só é incrementado quando a
    inserção REALMENTE acontece (uma re-ativação do mesmo titular é no-op). O
    índice no banco também blinda contra corrida entre dois pods.
    """
    if student_id:
        res = await session.execute(
            text('''INSERT INTO "KeyActivations" (id, key, student_id, teacher_id, channel, activated_at)
                    VALUES (:id, :key, :sid, NULL, :channel, :now)
                    ON CONFLICT (key, student_id) WHERE student_id IS NOT NULL DO NOTHING
                    RETURNING id'''),
            {"id": str(uuid4()), "key": key, "sid": student_id, "channel": channel_name, "now": now},
        )
    elif teacher_id:
        res = await session.execute(
            text('''INSERT INTO "KeyActivations" (id, key, student_id, teacher_id, channel, activated_at)
                    VALUES (:id, :key, NULL, :tid, :channel, :now)
                    ON CONFLICT (key, teacher_id) WHERE teacher_id IS NOT NULL DO NOTHING
                    RETURNING id'''),
            {"id": str(uuid4()), "key": key, "tid": teacher_id, "channel": channel_name, "now": now},
        )
    else:
        res = await session.execute(
            text('''INSERT INTO "KeyActivations" (id, key, student_id, teacher_id, channel, activated_at)
                    VALUES (:id, :key, NULL, NULL, :channel, :now) RETURNING id'''),
            {"id": str(uuid4()), "key": key, "channel": channel_name, "now": now},
        )

    if res.scalar_one_or_none() is None:
        # Já consumida por este titular — não incrementa (idempotente).
        logger.info("activation_key_already_consumed_by_holder", key=key,
                    student_id=student_id, teacher_id=teacher_id)
        return

    await session.execute(
        text('''UPDATE "ActivationKeys"
                SET activation_count = activation_count + 1,
                    status = 'activated',
                    first_activated_at = COALESCE(first_activated_at, :now),
                    updated_at = CURRENT_TIMESTAMP
                WHERE key = :key'''),
        {"key": key, "now": now},
    )


# ==============================================================================
# /sair_livro — autoexclusão LGPD do leitor auto-cadastrado
# ==============================================================================

async def run_leave_book_command(channel, sender_id: str):
    """Encerra a jornada e ANONIMIZA o leitor (LGPD). Alunos de planilha (cuja
    matrícula é gerida pelo professor) são orientados a falar com ele."""
    try:
        query_col = channel.get_user_query_column()
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                text(f'''SELECT id, registration_source FROM "Students"
                         WHERE {query_col} = :sid AND is_active = true'''),
                {"sid": sender_id},
            )
            student = res.fetchone()
            if not student:
                return
            if student.registration_source != "qr_activation":
                await channel.send_text(sender_id, LEAVE_BOOK_NOT_ALLOWED)
                return

            await session.execute(
                text('''UPDATE "Students"
                        SET is_active = false, name = '(removido a pedido — LGPD)',
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = :sid'''),
                {"sid": student.id},
            )
            await session.execute(
                text('''UPDATE "ReadingPlans"
                        SET is_paused = true, next_trigger_at = NULL, next_checkin_at = NULL,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE student_id = :sid'''),
                {"sid": student.id},
            )
            await session.commit()

        # Higieniza caches (role + memória de conversa)
        redis = await get_redis()
        await redis.delete(f"role:{sender_id}")
        await redis.delete(f"chat_history:{sender_id}")

        await channel.send_text(sender_id, LEAVE_BOOK_CONFIRMED)
        track_event("reader_left", {"student_id": student.id})
    except Exception as e:
        logger.error("leave_book_failed", error=str(e)[:300])


def _service_account_email() -> str:
    """E-mail da Service Account (instrução de compartilhamento do Drive)."""
    try:
        with open(settings.GOOGLE_SERVICE_ACCOUNT_FILE, encoding="utf-8") as f:
            return json.load(f).get("client_email", "(service account não configurada)")
    except Exception:
        return "(service account não configurada)"


# ==============================================================================
# /chaves_[turma] — estatísticas do lote para o professor/autor
# ==============================================================================

async def run_key_stats_command(channel, sender_id: str, class_query: str):
    try:
        from src.workers.reading_commands import _resolve_teacher_and_class
        from src.shared.constants.messages import CMD_TEACHER_NOT_FOUND, CMD_CLASS_NOT_FOUND

        async with AsyncSessionLocal() as session:
            teacher, klass = await _resolve_teacher_and_class(session, channel, sender_id, class_query)
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return
            if not klass:
                await channel.send_text(sender_id, CMD_CLASS_NOT_FOUND.format(class_query=class_query))
                return

            class_id, c_name = klass[0], klass[1]
            rows = (await session.execute(
                text('''SELECT status, COUNT(*), COALESCE(SUM(activation_count), 0)
                        FROM "ActivationKeys"
                        WHERE class_id = :cid
                        GROUP BY status'''),
                {"cid": class_id},
            )).fetchall()

            recent = (await session.execute(
                text('''SELECT COUNT(*) FROM "KeyActivations" ka
                        JOIN "ActivationKeys" ak ON ak.key = ka.key
                        WHERE ak.class_id = :cid
                          AND ka.activated_at > CURRENT_TIMESTAMP - INTERVAL '7 days' '''),
                {"cid": class_id},
            )).scalar() or 0

        by_status = {r[0]: (r[1], r[2]) for r in rows}
        total = sum(c for c, _ in by_status.values())
        if not total:
            await channel.send_text(
                sender_id,
                f"🔑 *Chaves — {c_name}*\n\nNenhuma chave cadastrada para esta turma.\n\n"
                "_Gere com `scripts/generate_activation_keys.py`, cole na aba 'Chaves' da planilha e rode /sinc_alunos._",
            )
            return

        msg = (
            f"🔑 *Chaves de Ativação — {c_name}*\n\n"
            f"📦 Total: *{total}*\n"
            f"🟢 Disponíveis: *{by_status.get('available', (0, 0))[0]}*\n"
            f"✅ Ativadas: *{by_status.get('activated', (0, 0))[0]}*\n"
            f"🚫 Revogadas: *{by_status.get('revoked', (0, 0))[0]}*\n\n"
            f"📈 Ativações nos últimos 7 dias: *{recent}*"
        )
        await channel.send_text(sender_id, msg)
    except Exception as e:
        logger.error("key_stats_failed", error=str(e)[:300])
        await channel.send_text(sender_id, f"❌ Erro ao consultar chaves: {str(e)[:100]}")
