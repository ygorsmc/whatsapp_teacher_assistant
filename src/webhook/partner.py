"""
Partner API — provisionamento B2B (H3.3).

Endpoints mínimos para editoras/EdTechs parceiras operarem o "Livro Vivo" sem
tocar na planilha:

  POST /v1/partner/keys   → gera e cadastra um lote de chaves de ativação
                            (retorna as chaves + links wa.me prontos p/ QR)
  GET  /v1/partner/stats  → métricas agregadas da turma-coorte (LGPD: nunca
                            dados individuais)

Segurança: header `X-API-Key` comparado em tempo constante com
settings.PARTNER_API_KEY. Sem a env var configurada, a API inteira responde
503 (desativada por padrão — opt-in explícito por ambiente).
"""

import hmac
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from src.shared.config import settings
from src.shared.telemetry import logger, track_event
from src.db.database import AsyncSessionLocal

router = APIRouter(prefix="/v1/partner")

MAX_KEYS_PER_REQUEST = 1000


def _check_api_key(x_api_key: str | None):
    if not settings.PARTNER_API_KEY:
        raise HTTPException(status_code=503, detail="Partner API disabled")
    if not x_api_key or not hmac.compare_digest(x_api_key, settings.PARTNER_API_KEY):
        raise HTTPException(status_code=401, detail="Invalid API key")


class KeyBatchRequest(BaseModel):
    course: str = Field(min_length=1, max_length=255, description="Nome do Curso (= pasta do Drive)")
    class_name: str = Field(min_length=1, max_length=255, description="Turma-coorte do material")
    qty: int = Field(ge=1, le=MAX_KEYS_PER_REQUEST)
    batch: str = Field(default="api", max_length=100)
    max_activations: int = Field(default=1, ge=1, le=10)


@router.post("/keys")
async def create_key_batch(body: KeyBatchRequest, x_api_key: str | None = Header(default=None)):
    """Gera um lote de chaves LIVRO- e o cadastra direto no banco (a API é a
    exceção autorizada à planilha). Cria Course/Class mínimos se não existirem
    — mesmos IDs determinísticos da sync (nada duplica depois)."""
    _check_api_key(x_api_key)

    from scripts.generate_activation_keys import generate_batch, build_wa_link
    from src.drive.spreadsheet_sync import generate_id
    from src.db.database import BOOTSTRAP_ADMIN_ID

    keys = generate_batch(body.qty, "LIVRO")
    course_id = generate_id("crs", body.course)
    class_id = generate_id("cls", f"{course_id}_{body.class_name}")

    async with AsyncSessionLocal() as session:
        await session.execute(
            text('''INSERT INTO "Courses" (id, teacher_id, name, is_active)
                    VALUES (:id, :tid, :name, true) ON CONFLICT(id) DO NOTHING'''),
            {"id": course_id, "tid": BOOTSTRAP_ADMIN_ID, "name": body.course},
        )
        await session.execute(
            text('''INSERT INTO "Classes" (id, course_id, teacher_id, name, current_lesson_number)
                    VALUES (:id, :cid, :tid, :name, 1) ON CONFLICT(id) DO NOTHING'''),
            {"id": class_id, "cid": course_id, "tid": BOOTSTRAP_ADMIN_ID, "name": body.class_name},
        )
        for key in keys:
            await session.execute(
                text('''INSERT INTO "ActivationKeys" (key, kind, class_id, batch, max_activations, status)
                        VALUES (:key, 'reader', :cid, :batch, :max_act, 'available')
                        ON CONFLICT(key) DO NOTHING'''),
                {"key": key, "cid": class_id, "batch": body.batch, "max_act": body.max_activations},
            )
        await session.commit()

    track_event("partner_keys_created", {"class_id": class_id, "qty": len(keys), "batch": body.batch})
    logger.info("partner_keys_created", class_id=class_id, qty=len(keys))

    bot_number = settings.PHONE_NUMBER_ID  # placeholder se o número público não estiver setado
    return {
        "class_id": class_id,
        "batch": body.batch,
        "keys": keys,
        "wa_links": [build_wa_link(bot_number, k) for k in keys],
    }


@router.get("/stats")
async def class_stats(course: str, class_name: str, x_api_key: str | None = Header(default=None)):
    """Métricas agregadas da coorte (LGPD: contagens e percentuais, nunca
    indivíduos): chaves, leitores, progresso médio e funil resumido."""
    _check_api_key(x_api_key)

    from src.drive.spreadsheet_sync import generate_id
    course_id = generate_id("crs", course)
    class_id = generate_id("cls", f"{course_id}_{class_name}")
    cutoff_30d = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=30)

    async with AsyncSessionLocal() as session:
        keys = (await session.execute(
            text('''SELECT status, COUNT(*) FROM "ActivationKeys"
                    WHERE class_id = :cid GROUP BY status'''),
            {"cid": class_id},
        )).fetchall()
        readers = (await session.execute(
            text('''SELECT COUNT(DISTINCT se.student_id) FROM "StudentEnrollment" se
                    JOIN "Students" s ON s.id = se.student_id AND s.is_active = true
                    WHERE se.class_id = :cid'''),
            {"cid": class_id},
        )).scalar() or 0
        plans = (await session.execute(
            text('''SELECT rp.current_section, rp.total_sections, rp.is_paused
                    FROM "ReadingPlans" rp
                    JOIN "StudentEnrollment" se ON se.student_id = rp.student_id
                    WHERE se.class_id = :clid AND rp.course_id = :cid'''),
            {"clid": class_id, "cid": course_id},
        )).fetchall()
        activations_30d = (await session.execute(
            text('''SELECT COUNT(*) FROM "KeyActivations" ka
                    JOIN "ActivationKeys" ak ON ak.key = ka.key
                    WHERE ak.class_id = :cid AND ka.activated_at >= :cutoff'''),
            {"cid": class_id, "cutoff": cutoff_30d},
        )).scalar() or 0

    total_plans = len(plans)
    avg_progress = round(
        100 * sum((p[0] or 0) / p[1] for p in plans if p[1]) / total_plans
    ) if total_plans else 0

    return {
        "class_id": class_id,
        "keys": {status: count for status, count in keys},
        "active_readers": readers,
        "reading_plans": total_plans,
        "avg_progress_percent": avg_progress,
        "completed": sum(1 for p in plans if (p[0] or 0) >= p[1]),
        "paused": sum(1 for p in plans if p[2]),
        "activations_last_30d": activations_30d,
    }
