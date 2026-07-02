"""Gamificação — orquestração de I/O (Fase 0).

A lógica DETERMINÍSTICA mora em `src/gamification/` (pura, testável); aqui ficam
os efeitos colaterais: escrever o ledger de XP (StudentInteractions), atualizar
o streak (Students), desbloquear badges (StudentBadges), montar a coorte/digest
semanal (WeeklyEngagement) e responder aos comandos do aluno.

INVARIANTES (esquema §1):
  - Mestre OFF (GAMIFICATION_ENABLED=false) ⇒ `award_xp` é no-op e devolve None;
    nenhuma escrita, comportamento byte a byte idêntico ao atual.
  - Cada hook é DEFENSIVO (try/except próprio): uma falha de gamificação NUNCA
    derruba quiz/revisão/RAG (falha isolada).
  - Escopo INVIOLÁVEL: toda leitura/escrita filtra por student + course
    (e teacher quando aplicável).
  - $0/mensagem: zero LLM. XP é if/else + timestamps + contadores.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta, time, date
from uuid import uuid4

import structlog
from sqlalchemy import text

from src.shared.config import settings
from src.shared.telemetry import track_event
from src.db.database import AsyncSessionLocal
from src.gamification import xp as xp_logic
from src.gamification import anti_gaming, streak as streak_logic, badges as badge_logic
from src.gamification import pseudonyms, leaderboard
from src.gamification import export as gami_export
from src.gamification import grade as grade_logic
from src.shared.constants import messages as M

logger = structlog.get_logger(__name__)


# ==============================================================================
# TEMPO (BRT) — helpers determinísticos
# ==============================================================================

def _now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _offset() -> timedelta:
    return timedelta(hours=settings.TIMEZONE_OFFSET_HOURS)


def now_local() -> datetime:
    return _now_utc() + _offset()


def today_local() -> date:
    return now_local().date()


def _today_start_utc() -> datetime:
    """Meia-noite BRT de hoje, expressa em UTC naive (p/ comparar com timestamp)."""
    start_local = datetime.combine(today_local(), time.min)
    return start_local - _offset()


def current_week_id() -> str:
    return xp_logic.week_id_for(now_local())


# ==============================================================================
# FLAG MESTRE
# ==============================================================================

def gamification_on() -> bool:
    """Chave-mestra/kill-switch global. A ativação REAL é por turma (planilha →
    Classes.gamification_enabled) — ver `_student_class_gami_on`."""
    return bool(settings.GAMIFICATION_ENABLED)


async def _student_class_gami_on(session, student_id: str, course_id: str | None) -> bool:
    """A turma do aluno NESTE curso tem gamificação ativada (planilha)?

    `BOOL_OR`: se qualquer turma do aluno no curso optou, vale. Sem curso (raro)
    ou sem turma ⇒ False (não pontua) — a ativação é opt-in deliberado."""
    if not course_id:
        return False
    val = (await session.execute(
        text('''SELECT COALESCE(BOOL_OR(cl.gamification_enabled), false)
                FROM "StudentEnrollment" se
                JOIN "Classes" cl ON cl.id = se.class_id
                WHERE se.student_id = :sid AND cl.course_id = :cid'''),
        {"sid": student_id, "cid": course_id},
    )).scalar()
    return bool(val)


# ==============================================================================
# CONCESSÃO DE XP (o hook central)
# ==============================================================================

@dataclass
class _DayStats:
    awarded_today: int
    acts_today: int
    rag_doubts_today: int


async def _read_day_stats(session, student_id: str) -> _DayStats:
    row = (await session.execute(
        text('''
            SELECT
              COALESCE(SUM(CASE WHEN xp_suspect = false THEN xp_awarded ELSE 0 END), 0) AS awarded,
              COUNT(*) AS acts,
              COALESCE(SUM(CASE WHEN message_content = 'rag_doubt' AND COALESCE(xp_awarded,0) > 0
                                THEN 1 ELSE 0 END), 0) AS rag_doubts
            FROM "StudentInteractions"
            WHERE student_id = :sid
              AND interaction_type = 'xp_event'
              AND timestamp >= :start
        '''),
        {"sid": student_id, "start": _today_start_utc()},
    )).fetchone()
    return _DayStats(int(row.awarded or 0), int(row.acts or 0), int(row.rag_doubts or 0))


async def award_xp(
    student_id: str,
    course_id: str | None,
    action: str,
    *,
    is_correct: bool | None = None,
    suspect: bool = False,
    teacher_id: str | None = None,
    subject_id: str | None = None,
) -> str | None:
    """Concede XP de uma ação e devolve a LINHA a anexar à confirmação (ou None).

    Self-contained e self-protecting: abre a própria sessão, roda tudo em
    try/except e nunca propaga exceção (falha isolada). Mestre/sub-flag OFF =>
    no-op (None).
    """
    if not gamification_on() or not settings.XP_LEDGER_ENABLED:
        return None
    if action not in xp_logic.XP_ACTIONS:
        return None
    try:
        async with AsyncSessionLocal() as session:
            # Ativação POR TURMA (planilha): turma sem gamificação ⇒ no-op.
            if not await _student_class_gami_on(session, student_id, course_id):
                return None
            stats = await _read_day_stats(session, student_id)
            first_act = stats.acts_today == 0

            base = xp_logic.base_xp_for(action)
            bonus = xp_logic.consistency_bonus(first_act)
            gross = base + bonus

            # Anti-gaming + teto: o evento SEMPRE entra (conta p/ streak), mas o
            # XP COMPETITIVO pode ser zerado (retido).
            anti_on = settings.ANTI_GAMING_ENABLED
            farmed = (
                anti_on and action == "rag_doubt"
                and anti_gaming.rag_doubt_over_cap(stats.rag_doubts_today)
            )
            if suspect and anti_on:
                competitive = 0
            elif farmed:
                competitive = 0
            else:
                competitive = xp_logic.clamp_to_cap(gross, stats.awarded_today)

            week_id = current_week_id()
            await session.execute(
                text('''
                    INSERT INTO "StudentInteractions"
                        (id, student_id, course_id, subject_id, interaction_type, is_correct,
                         message_content, xp_awarded, xp_week_id, xp_suspect)
                    VALUES (:id, :sid, :cid, :sub, 'xp_event', :corr, :act, :xp, :wk, :susp)
                '''),
                {"id": str(uuid4()), "sid": student_id, "cid": course_id,
                 "sub": subject_id, "corr": is_correct, "act": action,
                 "xp": competitive, "wk": week_id, "susp": bool(suspect and anti_on)},
            )

            # Streak (o dia conta mesmo com XP retido/over-cap).
            outcome, old_best = await _update_streak(session, student_id)

            # Badges (determinístico, escopado).
            big_badges = await _sync_badges(session, student_id, course_id, teacher_id)

            # Fênix (secreta): bateu 30 de novo tendo já tido 30+ e perdido.
            if (settings.SECRET_BADGES_ENABLED and outcome and outcome.milestone == 30
                    and old_best is not None and old_best >= 30):
                await _unlock_secret(session, student_id, course_id, "fenix", teacher_id)

            await session.commit()

            track_event("xp_awarded", {
                "student_id": student_id, "action": action,
                "xp": competitive, "suspect": bool(suspect and anti_on),
            })
            return _build_append_line(competitive, outcome, big_badges)
    except Exception as e:
        logger.error("award_xp_failed", action=action, error=str(e)[:300])
        return None


async def award_quiz_xp(
    student_id: str,
    course_id: str | None,
    *,
    is_correct: bool,
    quiz_bank_id: str | None,
    quiz_sent_at: datetime | None,
    quiz_answered_at: datetime | None,
    teacher_id: str | None = None,
) -> str | None:
    """Wrapper do hook de quiz: classifica a ação (revisão SRS no prazo vs quiz
    de aula), detecta resposta-relâmpago e a 'Volta por Cima' (recovery)."""
    if not gamification_on() or not settings.XP_LEDGER_ENABLED:
        return None
    try:
        action = "quiz_correct" if is_correct else "quiz_wrong"
        recovery = False
        subject_id = await subject_for_quiz_bank(quiz_bank_id)
        if quiz_bank_id:
            async with AsyncSessionLocal() as session:
                # Era uma reentrega SRS DEVIDA (revisão no prazo)?
                due = (await session.execute(
                    text('''SELECT 1 FROM "SpacedRepetitionQueue"
                            WHERE student_id = :sid AND quiz_bank_id = :qb
                              AND status = 'pending' AND next_review_at <= :now
                            LIMIT 1'''),
                    {"sid": student_id, "qb": quiz_bank_id, "now": _now_utc()},
                )).fetchone()
                if due:
                    action = "srs_ontime"
                    # 'Volta por Cima': acertou agora a reentrega de algo que já errou.
                    if is_correct:
                        prev_wrong = (await session.execute(
                            text('''SELECT 1 FROM "PendingQuizzes"
                                    WHERE student_id = :sid AND quiz_bank_id = :qb
                                      AND is_correct = false AND status = 'answered'
                                    LIMIT 1'''),
                            {"sid": student_id, "qb": quiz_bank_id},
                        )).fetchone()
                        recovery = bool(prev_wrong)

        suspect = (
            settings.ANTI_GAMING_ENABLED
            and anti_gaming.is_flash_answer(quiz_sent_at, quiz_answered_at)
        )

        line = await award_xp(
            student_id, course_id, action,
            is_correct=is_correct, suspect=suspect, teacher_id=teacher_id,
            subject_id=subject_id,
        )

        # Marcador da 'Volta por Cima' (alimenta o badge 🌱, sem XP próprio).
        if recovery and settings.BADGES_ENABLED:
            try:
                async with AsyncSessionLocal() as session:
                    await session.execute(
                        text('''INSERT INTO "StudentInteractions"
                                    (id, student_id, course_id, subject_id, interaction_type,
                                     message_content, xp_awarded, xp_suspect)
                                VALUES (:id, :sid, :cid, :sub, 'xp_event', 'srs_recovery', 0, false)'''),
                        {"id": str(uuid4()), "sid": student_id, "cid": course_id, "sub": subject_id},
                    )
                    await session.commit()
            except Exception as e:
                logger.error("srs_recovery_marker_failed", error=str(e)[:200])
        return line
    except Exception as e:
        logger.error("award_quiz_xp_failed", error=str(e)[:300])
        return None


async def subject_for_quiz_bank(quiz_bank_id: str | None) -> str | None:
    """Disciplina de origem de uma questão do QuizBank (carimbo do ledger).
    Best-effort: falha/None ⇒ None (o evento entra sem disciplina = 'Geral')."""
    if not quiz_bank_id:
        return None
    try:
        async with AsyncSessionLocal() as session:
            row = (await session.execute(
                text('SELECT subject_id FROM "QuizBank" WHERE id = :qb'),
                {"qb": quiz_bank_id},
            )).fetchone()
            return row.subject_id if row else None
    except Exception:
        return None


# ==============================================================================
# STREAK
# ==============================================================================

async def _update_streak(session, student_id: str):
    """Aplica o dia ao streak e devolve (outcome, old_best). old_best alimenta a
    detecção da Fênix (recuperar 30+ depois de perder). (None, None) se off/sem aluno."""
    if not settings.STREAK_ENABLED:
        return None, None
    row = (await session.execute(
        text('''SELECT engagement_streak, engagement_streak_best, streak_shields,
                       streak_last_active_date
                FROM "Students" WHERE id = :sid'''),
        {"sid": student_id},
    )).fetchone()
    if not row:
        return None, None

    old_best = int(row.engagement_streak_best or 0)
    state = streak_logic.StreakState(
        streak=int(row.engagement_streak or 0),
        best=old_best,
        shields=int(row.streak_shields or 0),
        last_active_date=row.streak_last_active_date,
    )
    outcome = streak_logic.apply_activity(
        state, today_local(), shields_enabled=settings.STREAK_SHIELD_ENABLED,
    )
    s = outcome.state
    await session.execute(
        text('''UPDATE "Students"
                SET engagement_streak = :st, engagement_streak_best = :best,
                    streak_shields = :sh, streak_last_active_date = :lad,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :sid'''),
        {"st": s.streak, "best": s.best, "sh": s.shields,
         "lad": s.last_active_date, "sid": student_id},
    )
    return outcome, old_best


# ==============================================================================
# BADGES
# ==============================================================================

@dataclass
class _UnlockedBadge:
    code: str
    level: str
    big: bool   # ouro/lendária → notifica na hora (dentro da janela)


async def _compute_counters(session, student_id: str, course_id: str | None) -> dict:
    """Contadores DETERMINÍSTICOS (escopados por curso) que alimentam os badges.

    Em sua maioria derivam do PRÓPRIO ledger de XP (reúso de StudentInteractions).
    """
    scope = 'AND course_id = :cid' if course_id else ''
    params = {"sid": student_id}
    if course_id:
        params["cid"] = course_id

    by_action = dict((r.act, int(r.n)) for r in (await session.execute(
        text(f'''SELECT message_content AS act, COUNT(*) AS n
                 FROM "StudentInteractions"
                 WHERE student_id = :sid AND interaction_type = 'xp_event' {scope}
                 GROUP BY message_content'''),
        params,
    )).fetchall())

    streak_row = (await session.execute(
        text('SELECT engagement_streak_best FROM "Students" WHERE id = :sid'),
        {"sid": student_id},
    )).fetchone()

    # Maior corrida de acertos consecutivos numa janela recente (aproximação Fase 0).
    recents = (await session.execute(
        text(f'''SELECT is_correct FROM "PendingQuizzes"
                 WHERE student_id = :sid AND status = 'answered'
                   {("AND course_id = :cid" if course_id else "")}
                 ORDER BY answered_at DESC NULLS LAST LIMIT 50'''),
        params,
    )).fetchall()
    best_run = 0
    run = 0
    for r in recents:
        if r.is_correct:
            run += 1
            best_run = max(best_run, run)
        else:
            run = 0

    return {
        "streak_best": int(streak_row.engagement_streak_best or 0) if streak_row else 0,
        "best_correct_streak": best_run,
        "rag_doubts": by_action.get("rag_doubt", 0),
        "reading_sections": by_action.get("reading", 0),
        "srs_ontime": by_action.get("srs_ontime", 0),
        "srs_recoveries": by_action.get("srs_recovery", 0),
        "essays": by_action.get("essay", 0),
        "voice_above_threshold": by_action.get("voice", 0),
    }


async def _sync_badges(session, student_id: str, course_id: str | None,
                       teacher_id: str | None) -> list[_UnlockedBadge]:
    if not settings.BADGES_ENABLED or not course_id:
        return []
    counters = await _compute_counters(session, student_id, course_id)
    target = badge_logic.evaluate(counters)
    if not target:
        return []

    existing = dict((r.badge_code, r.level) for r in (await session.execute(
        text('''SELECT badge_code, level FROM "StudentBadges"
                WHERE student_id = :sid AND course_id = :cid'''),
        {"sid": student_id, "cid": course_id},
    )).fetchall())

    newly: list[_UnlockedBadge] = []
    for code, level in target:
        cur = existing.get(code)
        if cur is not None and badge_logic.level_rank(level) <= badge_logic.level_rank(cur):
            continue  # já tem nível igual ou superior
        if cur is None:
            await session.execute(
                text('''INSERT INTO "StudentBadges"
                            (id, student_id, course_id, teacher_id, badge_code, level)
                        VALUES (:id, :sid, :cid, :tid, :code, :lvl)'''),
                {"id": str(uuid4()), "sid": student_id, "cid": course_id,
                 "tid": teacher_id, "code": code, "lvl": level},
            )
        else:
            await session.execute(
                text('''UPDATE "StudentBadges" SET level = :lvl, unlocked_at = CURRENT_TIMESTAMP,
                            seen_at = NULL, updated_at = CURRENT_TIMESTAMP
                        WHERE student_id = :sid AND course_id = :cid AND badge_code = :code'''),
                {"lvl": level, "sid": student_id, "cid": course_id, "code": code},
            )
        newly.append(_UnlockedBadge(code, level, big=level in ("ouro", "lendaria")))
    return newly


# ==============================================================================
# BADGES SECRETOS (Fase 1) — por EVENTO, desbloqueados uma única vez
# ==============================================================================

async def _unlock_secret(session, student_id: str, course_id: str | None,
                         code: str, teacher_id: str | None) -> str | None:
    """Insere o badge secreto (ON CONFLICT DO NOTHING) NESTA sessão (sem commit).
    Devolve a mensagem de revelação se foi REALMENTE novo, senão None."""
    if not course_id or code not in badge_logic.SECRET_BADGE_BY_CODE:
        return None
    res = await session.execute(
        text('''INSERT INTO "StudentBadges"
                    (id, student_id, course_id, teacher_id, badge_code, level)
                VALUES (:id, :sid, :cid, :tid, :code, :lvl)
                ON CONFLICT (student_id, course_id, badge_code) DO NOTHING
                RETURNING id'''),
        {"id": str(uuid4()), "sid": student_id, "cid": course_id, "tid": teacher_id,
         "code": code, "lvl": badge_logic.SECRET_LEVEL},
    )
    if res.fetchone() is None:
        return None  # já tinha
    bd = badge_logic.SECRET_BADGE_BY_CODE[code]
    return M.SECRET_BADGE_UNLOCKED.format(emoji=bd.emoji, name=bd.name, hint=bd.hint)


async def award_secret_badge(student_id: str, course_id: str | None, code: str,
                             *, teacher_id: str | None = None) -> str | None:
    """Hook público (RAG/leitura): desbloqueia um badge secreto e devolve a
    revelação p/ anexar (ou None). Self-protecting; flag OFF ⇒ no-op."""
    if not gamification_on() or not settings.SECRET_BADGES_ENABLED:
        return None
    try:
        async with AsyncSessionLocal() as session:
            if not await _student_class_gami_on(session, student_id, course_id):
                return None
            msg = await _unlock_secret(session, student_id, course_id, code, teacher_id)
            await session.commit()
            if msg:
                track_event("secret_badge_unlocked", {"student_id": student_id, "code": code})
            return msg
    except Exception as e:
        logger.error("award_secret_badge_failed", code=code, error=str(e)[:200])
        return None


# ==============================================================================
# RENDERIZAÇÃO DA LINHA ANEXADA
# ==============================================================================

def _build_append_line(competitive: int, outcome, big_badges: list[_UnlockedBadge]) -> str | None:
    lines: list[str] = []
    streak_days = outcome.state.streak if outcome else 0
    milestone = outcome.milestone if outcome else None

    if competitive > 0 and milestone:
        lines.append(M.XP_APPEND_STREAK_MILESTONE.format(xp=competitive, streak=streak_days))
    elif competitive > 0 and settings.STREAK_ENABLED and streak_days > 0:
        lines.append(M.XP_APPEND_STREAK.format(xp=competitive, streak=streak_days))
    elif competitive > 0:
        lines.append(M.XP_APPEND_LINE.format(xp=competitive))

    if outcome and outcome.shield_earned:
        lines.append(M.XP_APPEND_SHIELD_EARNED.format(shields=outcome.state.shields))

    for b in big_badges:
        if not b.big:
            continue
        bd = badge_logic.BADGE_BY_CODE.get(b.code)
        if bd:
            lines.append(M.BADGE_UNLOCKED_NOW.format(
                emoji=bd.emoji, name=bd.name, level=badge_logic.LEVEL_LABEL[b.level],
            ))
    return "\n".join(lines) if lines else None


# ==============================================================================
# SLOT DE SALVAR STREAK (reúso do nudge 23:30 — chamado por process_window_extensions)
# ==============================================================================

async def maybe_streak_save_copy(session, student_id: str, class_id: str) -> tuple[str, list] | None:
    """Se o aluno tem streak ≥ 3 e ainda não agiu hoje, devolve (body, buttons)
    do 'salvar streak' para SUBSTITUIR a cópia genérica do nudge de janela.
    None ⇒ usa a cópia padrão (e, com a flag OFF, é sempre None: byte-idêntico)."""
    if not gamification_on() or not settings.STREAK_ENABLED:
        return None
    try:
        # Ativação POR TURMA: turma sem gamificação não recebe o nudge de streak.
        cls_on = (await session.execute(
            text('SELECT gamification_enabled FROM "Classes" WHERE id = :cid'),
            {"cid": class_id},
        )).scalar()
        if not cls_on:
            return None
        row = (await session.execute(
            text('''SELECT engagement_streak, streak_last_active_date
                    FROM "Students" WHERE id = :sid'''),
            {"sid": student_id},
        )).fetchone()
        if not row:
            return None
        if int(row.engagement_streak or 0) < 3:
            return None
        if row.streak_last_active_date == today_local():
            return None  # já agiu hoje, sequência garantida
        body = M.STREAK_SAVE_BODY.format(streak=int(row.engagement_streak or 0))
        buttons = [
            {"id": f"review_accept_{class_id}", "title": M.STREAK_SAVE_BTN_GO},
            {"id": "window_warning_later", "title": M.STREAK_SAVE_BTN_NO},
        ]
        return body, buttons
    except Exception as e:
        logger.error("streak_save_copy_failed", error=str(e)[:200])
        return None


# ==============================================================================
# COMANDOS DO ALUNO (sob demanda, grátis na janela de 24h)
# ==============================================================================

# Comandos de gamificação na allowlist do aluno — gateados pela flag mestre na
# borda (webhook) para preservar o byte-idêntico com a flag OFF.
GAMIFICATION_STUDENT_COMMANDS = frozenset({"meu_ranking", "conquistas", "ocultar_ranking"})


async def _resolve_student_scope(session, channel, sender_id: str):
    """(student_id, name, course_id, teacher_id, ranking_opted_out, gami_on) do
    aluno. PREFERE uma turma com gamificação ativa (planilha) quando houver."""
    query_col = channel.get_user_query_column()
    row = (await session.execute(
        text(f'''SELECT s.id, s.name, c.id AS course_id, c.teacher_id, s.ranking_opted_out,
                        cl.gamification_enabled AS gami_on
                 FROM "Students" s
                 JOIN "StudentEnrollment" se ON se.student_id = s.id
                 JOIN "Classes" cl ON cl.id = se.class_id
                 JOIN "Courses" c ON c.id = cl.course_id
                 WHERE s.{query_col} = :sid AND s.is_active = true
                 ORDER BY cl.gamification_enabled DESC, se.created_at LIMIT 1'''),
        {"sid": sender_id},
    )).fetchone()
    return row


async def run_hide_ranking_command(channel, sender_id: str) -> None:
    """Toggle do opt-out SÓ do ranking (/ocultar_ranking). Não silencia XP/streak/badges."""
    if not gamification_on():
        return
    try:
        async with AsyncSessionLocal() as session:
            scope = await _resolve_student_scope(session, channel, sender_id)
            if not scope or not scope.gami_on:
                return
            new_val = not bool(scope.ranking_opted_out)
            await session.execute(
                text('UPDATE "Students" SET ranking_opted_out = :v, updated_at = CURRENT_TIMESTAMP WHERE id = :sid'),
                {"v": new_val, "sid": scope.id},
            )
            await session.commit()
        await channel.send_text(sender_id, M.RANKING_HIDDEN if new_val else M.RANKING_SHOWN)
    except Exception as e:
        logger.error("hide_ranking_failed", error=str(e)[:200])


async def run_achievements_command(channel, sender_id: str) -> None:
    """/conquistas — vitrine das conquistas + o próximo badge 'quase lá'."""
    if not gamification_on() or not settings.BADGES_ENABLED:
        return
    try:
        async with AsyncSessionLocal() as session:
            scope = await _resolve_student_scope(session, channel, sender_id)
            if not scope or not scope.gami_on:
                return
            rows = (await session.execute(
                text('''SELECT badge_code, level FROM "StudentBadges"
                        WHERE student_id = :sid AND course_id = :cid'''),
                {"sid": scope.id, "cid": scope.course_id},
            )).fetchall()
            # Marca como vistas (não notificar de novo).
            await session.execute(
                text('''UPDATE "StudentBadges" SET seen_at = CURRENT_TIMESTAMP
                        WHERE student_id = :sid AND course_id = :cid AND seen_at IS NULL'''),
                {"sid": scope.id, "cid": scope.course_id},
            )
            counters = await _compute_counters(session, scope.id, scope.course_id)
            await session.commit()

        if not rows:
            await channel.send_text(sender_id, M.ACHIEVEMENTS_NONE)
            return

        owned = {r.badge_code: r.level for r in rows}
        lines = [M.ACHIEVEMENTS_HEADER]
        for bd in badge_logic.BADGE_CATALOG:
            if bd.code in owned:
                lines.append(M.ACHIEVEMENTS_ROW.format(
                    emoji=bd.emoji, name=bd.name,
                    level=badge_logic.LEVEL_LABEL[owned[bd.code]],
                ))
        # Conquistas secretas já descobertas (Fase 1).
        for sb in badge_logic.SECRET_BADGES:
            if sb.code in owned:
                lines.append(M.ACHIEVEMENTS_SECRET_ROW.format(emoji=sb.emoji, name=sb.name))
        # Próximo a desbloquear (o primeiro com gap positivo).
        for bd in badge_logic.BADGE_CATALOG:
            value = int(counters.get(bd.counter, 0))
            nxt = badge_logic.next_threshold(bd, value)
            if nxt and bd.code not in owned:
                level, thr = nxt
                lines.append(M.ACHIEVEMENTS_NEXT.format(
                    emoji=bd.emoji, name=bd.name,
                    level=badge_logic.LEVEL_LABEL[level], gap=thr - value,
                ))
                break
        await channel.send_text(sender_id, "\n".join(lines)[:4000])
    except Exception as e:
        logger.error("achievements_command_failed", error=str(e)[:300])


async def run_my_xp_command(channel, sender_id: str) -> None:
    """/minha_xp — XP POR DISCIPLINA (acumulado + semana), em todas as turmas
    do aluno. Só dados do PRÓPRIO aluno (sem colegas — LGPD ok); exclui eventos
    suspeitos (anti-gaming), igual ao ranking/nota. Eventos antigos sem carimbo
    de disciplina caem em 'Geral' (nome do curso quando conhecido)."""
    if not gamification_on() or not settings.XP_LEDGER_ENABLED:
        return
    try:
        async with AsyncSessionLocal() as session:
            scope = await _resolve_student_scope(session, channel, sender_id)
            if not scope or not scope.gami_on:
                return
            week_id = current_week_id()
            rows = (await session.execute(
                text('''SELECT COALESCE(sub.name, c.name, 'Geral') AS label,
                               COALESCE(SUM(CASE WHEN si.xp_suspect = false
                                                 THEN si.xp_awarded ELSE 0 END), 0) AS total,
                               COALESCE(SUM(CASE WHEN si.xp_suspect = false AND si.xp_week_id = :wk
                                                 THEN si.xp_awarded ELSE 0 END), 0) AS week
                        FROM "StudentInteractions" si
                        LEFT JOIN "Subjects" sub ON sub.id = si.subject_id
                        LEFT JOIN "Courses" c ON c.id = si.course_id
                        WHERE si.interaction_type = 'xp_event'
                          AND si.student_id = :sid
                        GROUP BY label
                        ORDER BY total DESC, label'''),
                {"sid": scope.id, "wk": week_id},
            )).fetchall()

        rows = [r for r in rows if int(r.total or 0) > 0 or int(r.week or 0) > 0]
        if not rows:
            await channel.send_text(sender_id, M.MY_XP_NONE)
            return

        body = M.MY_XP_HEADER + "".join(
            M.MY_XP_ROW.format(label=r.label, total=int(r.total or 0), week=int(r.week or 0))
            for r in rows
        ) + M.MY_XP_FOOTER
        await channel.send_text(sender_id, body[:4000])
    except Exception as e:
        logger.error("my_xp_failed", error=str(e)[:300])


async def run_my_ranking_command(channel, sender_id: str) -> None:
    """/meu_ranking — recorte anônimo da liga (top 3 + vizinhança + meta)."""
    if not gamification_on() or not settings.LEADERBOARD_ENABLED:
        return
    try:
        async with AsyncSessionLocal() as session:
            scope = await _resolve_student_scope(session, channel, sender_id)
            if not scope or not scope.gami_on:
                return
            if bool(scope.ranking_opted_out):
                await channel.send_text(sender_id, M.RANKING_OPTED_OUT)
                return

            week_id = current_week_id()
            # XP da semana por aluno NESTE curso (escopo inviolável). O xp_event já
            # carrega course_id — sem JOIN de matrícula (evita multiplicar linhas
            # quando o aluno tem mais de uma turma no mesmo curso).
            rows = (await session.execute(
                text('''SELECT si.student_id,
                               COALESCE(SUM(CASE WHEN si.xp_suspect = false THEN si.xp_awarded ELSE 0 END), 0) AS xp
                        FROM "StudentInteractions" si
                        JOIN "Students" s ON s.id = si.student_id
                        WHERE si.interaction_type = 'xp_event'
                          AND si.xp_week_id = :wk
                          AND si.course_id = :cid
                          AND s.is_active = true
                          AND s.ranking_opted_out = false
                        GROUP BY si.student_id'''),
                {"wk": week_id, "cid": scope.course_id},
            )).fetchall()

        ranked_pairs = [(r.student_id, int(r.xp or 0)) for r in rows]
        my_xp = next((xp for sid, xp in ranked_pairs if sid == scope.id), 0)

        if not ranked_pairs or scope.id not in {sid for sid, _ in ranked_pairs}:
            await channel.send_text(sender_id, M.RANKING_NONE_YET)
            return

        # Coorte por engajamento + rank; recorte da vizinhança.
        ranked_pairs.sort(key=lambda t: (-t[1], t[0]))
        cohorts = leaderboard.partition_into_cohorts(ranked_pairs, settings.LEADERBOARD_COHORT_SIZE)
        my_cohort = next((c for c in cohorts if scope.id in {sid for sid, _ in c}), cohorts[0])
        ranked = leaderboard.rank_cohort(my_cohort)
        view = leaderboard.build_league_view(ranked, scope.id, min_cohort=settings.GAMIFICATION_MIN_COHORT)

        if not view.circulates:
            streak_days = await _student_streak(scope.id)
            await channel.send_text(sender_id, M.RANKING_SMALL_COHORT.format(xp=my_xp, streak=streak_days))
            return

        pseudo = pseudonyms.assign_unique([e.student_id for e in ranked], week_id)
        body = _render_league(view, pseudo, scope.id)
        # Ligas (Fase 1): prefixa a divisão atual (do último snapshot) quando ON.
        if settings.LEAGUES_ENABLED:
            division = await _student_current_division(scope.id, scope.course_id)
            if division:
                label = leaderboard.DIVISION_LABEL.get(division, division)
                body = M.RANKING_DIVISION_LINE.format(label=label) + "\n" + body
        await channel.send_text(sender_id, body)
    except Exception as e:
        logger.error("my_ranking_failed", error=str(e)[:300])


async def _student_current_division(student_id: str, course_id: str) -> str | None:
    """Divisão vigente do aluno (do snapshot mais recente). None se ainda não há."""
    try:
        async with AsyncSessionLocal() as session:
            row = (await session.execute(
                text('''SELECT division FROM "WeeklyEngagement"
                        WHERE student_id = :sid AND course_id = :cid AND division IS NOT NULL
                        ORDER BY week_id DESC LIMIT 1'''),
                {"sid": student_id, "cid": course_id},
            )).fetchone()
            return row.division if row else None
    except Exception:
        return None


async def _student_streak(student_id: str) -> int:
    try:
        async with AsyncSessionLocal() as session:
            row = (await session.execute(
                text('SELECT engagement_streak FROM "Students" WHERE id = :sid'),
                {"sid": student_id},
            )).fetchone()
            return int(row.engagement_streak or 0) if row else 0
    except Exception:
        return 0


_MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def _render_league(view: leaderboard.LeagueView, pseudo: dict[str, str], me_id: str) -> str:
    """Renderiza a seção de liga (top 3 + ⋯ + vizinhança + meta). Pura formatação."""
    lines = [M.RANKING_HEADER.format(cohort_size=view.cohort_size)]
    top_ids = {e.student_id for e in view.top3}
    for e in view.top3:
        lines.append(M.RANKING_ROW_TOP.format(
            medal=_MEDALS.get(e.rank, "•"), pseudonym=pseudo.get(e.student_id, "—"), xp=e.xp,
        ))
    # Vizinhança (só se houver um salto do top 3 para ela).
    neighbor_ids = [e.student_id for e in view.neighbors]
    if any(nid not in top_ids for nid in neighbor_ids):
        if view.my_rank and view.my_rank > 4:
            lines.append(M.RANKING_ROW_ELLIPSIS)
        for e in view.neighbors:
            if e.student_id in top_ids:
                continue
            if e.student_id == me_id:
                lines.append(M.RANKING_ROW_ME.format(pseudonym=pseudo.get(e.student_id, "você"), xp=e.xp))
            else:
                lines.append(M.RANKING_ROW_NEIGHBOR.format(pseudonym=pseudo.get(e.student_id, "—"), xp=e.xp))
    if view.xp_to_next is not None and view.xp_to_next > 0:
        lines.append(M.RANKING_TO_NEXT.format(gap=view.xp_to_next))
    elif view.my_rank == 1:
        lines.append(M.RANKING_AT_TOP)
    return "\n".join(lines)


# ==============================================================================
# BATCH SEMANAL — snapshot da liga + resumo (a única proativa garantida)
# ==============================================================================

async def process_weekly_leaderboard() -> None:
    """Constrói o WeeklyEngagement da semana e envia o resumo semanal (1 template).

    Determinístico, $0, isolado. Gateado por dia/hora (WEEKLY_DIGEST_WEEKDAY/HOUR)
    e idempotente: se já há snapshot da semana para o curso, não repete.
    """
    if not gamification_on() or not settings.WEEKLY_DIGEST_ENABLED:
        return
    nl = now_local()
    if nl.weekday() != settings.WEEKLY_DIGEST_WEEKDAY or nl.hour < settings.WEEKLY_DIGEST_HOUR:
        return

    week_id = current_week_id()
    default_ct = settings.CHANNEL_BACKEND.lower()
    from src.channel.factory import get_channel

    async with AsyncSessionLocal() as session:
        # Cursos com XP nesta semana que AINDA não têm snapshot (idempotência).
        courses = (await session.execute(
            text('''SELECT DISTINCT si.course_id
                    FROM "StudentInteractions" si
                    WHERE si.interaction_type = 'xp_event' AND si.xp_week_id = :wk
                      AND si.course_id IS NOT NULL
                      AND NOT EXISTS (
                          SELECT 1 FROM "WeeklyEngagement" we
                          WHERE we.course_id = si.course_id AND we.week_id = :wk)'''),
            {"wk": week_id},
        )).fetchall()

    for crow in courses:
        try:
            await _snapshot_and_digest_course(crow.course_id, week_id, default_ct, get_channel)
        except Exception as e:
            logger.error("weekly_digest_course_failed", course_id=crow.course_id, error=str(e)[:300])


async def _load_prev_divisions(session, course_id: str, week_id: str) -> dict[str, str]:
    """Divisão mais recente de cada aluno NESTE curso ANTES da semana corrente
    (a divisão em que ele compete agora). Vazio na 1ª semana → todos em bronze."""
    rows = (await session.execute(
        text('''SELECT DISTINCT ON (student_id) student_id, division
                FROM "WeeklyEngagement"
                WHERE course_id = :cid AND week_id < :wk AND division IS NOT NULL
                ORDER BY student_id, week_id DESC'''),
        {"cid": course_id, "wk": week_id},
    )).fetchall()
    return {r.student_id: r.division for r in rows}


async def _snapshot_and_digest_course(course_id: str, week_id: str, default_ct: str, get_channel) -> None:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(
            text('''SELECT si.student_id,
                           COALESCE(SUM(CASE WHEN si.xp_suspect = false THEN si.xp_awarded ELSE 0 END), 0) AS xp,
                           st.phone, st.telegram_chat_id, st.preferred_channel,
                           st.engagement_streak, st.engagement_streak_best, st.streak_shields,
                           st.ranking_opted_out, st.messaging_opted_out
                    FROM "StudentInteractions" si
                    JOIN "Students" st ON st.id = si.student_id
                    WHERE si.interaction_type = 'xp_event' AND si.xp_week_id = :wk
                      AND si.course_id = :cid AND st.is_active = true
                    GROUP BY si.student_id, st.phone, st.telegram_chat_id, st.preferred_channel,
                             st.engagement_streak, st.engagement_streak_best, st.streak_shields,
                             st.ranking_opted_out, st.messaging_opted_out'''),
            {"wk": week_id, "cid": course_id},
        )).fetchall()
        teacher_row = (await session.execute(
            text('SELECT teacher_id FROM "Courses" WHERE id = :cid'), {"cid": course_id},
        )).fetchone()
        teacher_id = teacher_row.teacher_id if teacher_row else None

    if not rows:
        return

    # Coorte só com quem participa do ranking (opt-out fica fora da competição).
    competing = [(r.student_id, int(r.xp or 0)) for r in rows if not r.ranking_opted_out]

    # Ligas (Fase 1): agrupa por DIVISÃO carregada da semana anterior; senão, um
    # grupo flat por curso (Fase 0). LEAGUES off ⇒ `division` fica NULL em tudo.
    if settings.LEAGUES_ENABLED:
        from collections import defaultdict
        async with AsyncSessionLocal() as session:
            prev_div = await _load_prev_divisions(session, course_id, week_id)
        groups: dict[str | None, list] = defaultdict(list)
        for sid, xp in competing:
            groups[prev_div.get(sid) or leaderboard.DIVISIONS[0]].append((sid, xp))
    else:
        groups = {None: competing}

    # Mapa student_id -> snapshot da semana (coorte, rank, pseudônimos, divisão).
    snapshot: dict[str, dict] = {}
    ci = 0
    for division, members in groups.items():
        members.sort(key=lambda t: (-t[1], t[0]))
        for cohort in leaderboard.partition_into_cohorts(members, settings.LEADERBOARD_COHORT_SIZE):
            ranked = leaderboard.rank_cohort(cohort)
            pseudo = pseudonyms.assign_unique([e.student_id for e in ranked], week_id)
            cohort_id = f"{course_id}:{week_id}:c{ci}"
            ci += 1
            for e in ranked:
                stored_division, event = None, "stay"
                if division is not None:
                    stored_division, event = leaderboard.apply_league_movement(
                        division, e.rank, len(ranked),
                        promote_top=settings.LEAGUE_PROMOTE_TOP,
                        relegate_bottom=settings.LEAGUE_RELEGATE_BOTTOM,
                    )
                snapshot[e.student_id] = {
                    "cohort_id": cohort_id, "rank": e.rank, "size": len(ranked),
                    "pseudo": pseudo.get(e.student_id), "ranked": ranked, "pseudo_map": pseudo,
                    "competed_division": division, "division": stored_division, "event": event,
                }

    # Persiste WeeklyEngagement (todos, inclusive opt-out, p/ auditoria) e envia digest.
    async with AsyncSessionLocal() as session:
        for r in rows:
            snap = snapshot.get(r.student_id)
            await session.execute(
                text('''INSERT INTO "WeeklyEngagement"
                            (id, student_id, course_id, teacher_id, week_id, cohort_id,
                             cohort_size, xp_week, rank_in_cohort, pseudonym, division, opted_out_ranking)
                        VALUES (:id, :sid, :cid, :tid, :wk, :coh, :size, :xp, :rank, :pseu, :div, :opt)
                        ON CONFLICT (student_id, course_id, week_id) DO NOTHING'''),
                {"id": str(uuid4()), "sid": r.student_id, "cid": course_id, "tid": teacher_id,
                 "wk": week_id, "coh": snap["cohort_id"] if snap else None,
                 "size": snap["size"] if snap else 0, "xp": int(r.xp or 0),
                 "rank": snap["rank"] if snap else None,
                 "pseu": snap["pseudo"] if snap else None,
                 "div": snap["division"] if snap else None,
                 "opt": bool(r.ranking_opted_out)},
            )
        await session.commit()

    # Envio do resumo (respeita opt-out TOTAL de mensagens; janela/template).
    sent = 0
    for r in rows:
        if r.messaging_opted_out:
            continue
        ct = (r.preferred_channel or default_ct).lower()
        addr = r.telegram_chat_id if ct == "telegram" else r.phone
        if not addr:
            continue
        try:
            digest = _render_weekly_digest(r, snapshot.get(r.student_id))
            await get_channel(ct).send_text(addr, digest)
            sent += 1
        except Exception as e:
            logger.error("weekly_digest_send_failed", student_id=r.student_id, error=str(e)[:200])

    track_event("weekly_digest_sent", {"course_id": course_id, "week_id": week_id, "sent": sent})


def _render_weekly_digest(r, snap: dict | None) -> str:
    lines = [M.WEEKLY_DIGEST_HEADER, ""]
    lines.append(M.WEEKLY_DIGEST_XP.format(xp=int(r.xp or 0), best=int(r.engagement_streak_best or 0)))
    if settings.STREAK_ENABLED:
        lines.append(M.WEEKLY_DIGEST_STREAK.format(
            streak=int(r.engagement_streak or 0), shields=int(r.streak_shields or 0)))

    # Seção de liga (só se a coorte circula e o aluno não optou por sair).
    if (settings.LEADERBOARD_ENABLED and snap and not r.ranking_opted_out
            and snap["size"] >= settings.GAMIFICATION_MIN_COHORT):
        view = leaderboard.build_league_view(
            snap["ranked"], r.student_id, min_cohort=settings.GAMIFICATION_MIN_COHORT)
        if view.circulates:
            lines.append("")
            mv = _render_league_movement(snap)
            if mv:
                lines.append(mv)
            lines.append(_render_league(view, snap["pseudo_map"], r.student_id))

    lines.append(M.WEEKLY_DIGEST_OUTRO)
    return "\n".join(lines)[:4000]


def _render_league_movement(snap: dict) -> str | None:
    """Linha de promoção/rebaixamento da liga (Fase 1). None se LEAGUES off."""
    if not settings.LEAGUES_ENABLED:
        return None
    competed = snap.get("competed_division")
    if not competed:
        return None
    event = snap.get("event", "stay")
    to_div = snap.get("division") or competed
    from_label = leaderboard.DIVISION_LABEL.get(competed, competed)
    to_label = leaderboard.DIVISION_LABEL.get(to_div, to_div)
    if event == "promoted":
        return M.LEAGUE_LINE_PROMOTED.format(from_label=from_label, to_label=to_label)
    if event == "relegated":
        return M.LEAGUE_LINE_RELEGATED.format(from_label=from_label, to_label=to_label)
    return M.LEAGUE_LINE_STAY.format(label=from_label)


# ==============================================================================
# DASHBOARD DO PROFESSOR (Fase 1, TEACHER_DASHBOARD_ENABLED — Modelo A)
# ==============================================================================

async def teacher_gamification_section(class_id: str, class_name: str = "") -> str:
    """Bloco de gamificação anexado ao /engajamento (vazio se a flag estiver off).

    Agregados da TURMA (nomes reais nunca circulam para colegas — aqui é o
    professor, controlador): XP da semana, sequência média, alunos ocultos do
    ranking. Determinístico; isolado."""
    if not gamification_on() or not settings.TEACHER_DASHBOARD_ENABLED:
        return ""
    try:
        week_id = current_week_id()
        async with AsyncSessionLocal() as session:
            crow = (await session.execute(
                text('SELECT course_id FROM "Classes" WHERE id = :cid'), {"cid": class_id},
            )).fetchone()
            course_id = crow.course_id if crow else None
            if not course_id:
                return ""
            row = (await session.execute(
                text('''SELECT
                          COALESCE(SUM(CASE WHEN si.xp_suspect = false THEN si.xp_awarded ELSE 0 END), 0) AS week_xp,
                          COUNT(DISTINCT si.student_id) AS xp_students
                        FROM "StudentInteractions" si
                        JOIN "StudentEnrollment" se ON se.student_id = si.student_id
                        WHERE se.class_id = :cid AND si.interaction_type = 'xp_event'
                          AND si.xp_week_id = :wk AND si.course_id = :course'''),
                {"cid": class_id, "wk": week_id, "course": course_id},
            )).fetchone()
            streak_row = (await session.execute(
                text('''SELECT COALESCE(AVG(s.engagement_streak), 0) AS avg_streak,
                               SUM(CASE WHEN s.ranking_opted_out THEN 1 ELSE 0 END) AS hidden
                        FROM "Students" s
                        JOIN "StudentEnrollment" se ON se.student_id = s.id
                        WHERE se.class_id = :cid AND s.is_active = true'''),
                {"cid": class_id},
            )).fetchone()

        week_xp = int(row.week_xp or 0) if row else 0
        avg_streak = float(streak_row.avg_streak or 0) if streak_row else 0.0
        hidden = int(streak_row.hidden or 0) if streak_row else 0
        return M.DASHBOARD_GAMIFICATION_SECTION.format(
            week_xp=week_xp, avg_streak=round(avg_streak, 1), hidden=hidden,
            class_hint=class_name or "[turma]",
        )
    except Exception as e:
        logger.error("teacher_gamification_section_failed", error=str(e)[:200])
        return ""


async def run_export_engagement_command(channel, sender_id: str, class_query: str) -> None:
    """/exportar_engajamento_[turma] — gera o CSV de engajamento por aluno (Modelo
    A: o professor integra ao diário). Entrega como texto (CSV) — robusto e sem
    dependência externa; a escrita direta em Sheets é um follow-up (precisa de
    escopo de escrita + modelo de compartilhamento)."""
    if not gamification_on() or not settings.TEACHER_DASHBOARD_ENABLED:
        return
    try:
        from src.workers.reading_commands import _resolve_teacher_and_class
        from src.shared.constants.messages import CMD_TEACHER_NOT_FOUND, CMD_CLASS_NOT_FOUND

        week_id = current_week_id()
        cutoff7 = _now_utc() - timedelta(days=7)
        async with AsyncSessionLocal() as session:
            teacher, klass = await _resolve_teacher_and_class(session, channel, sender_id, class_query)
            if not teacher:
                await channel.send_text(sender_id, CMD_TEACHER_NOT_FOUND)
                return
            if not klass:
                await channel.send_text(sender_id, CMD_CLASS_NOT_FOUND.format(class_query=class_query))
                return
            class_id, c_name, course_id = klass[0], klass[1], klass[2]

            # Componente de nota (Modelo C) desta turma — acrescenta coluna no CSV.
            grade_cfg = (await session.execute(
                text('SELECT grade_weight_pct AS weight, weekly_xp_goal AS goal FROM "Classes" WHERE id = :cid'),
                {"cid": class_id},
            )).fetchone()
            grade_on = bool(grade_cfg and grade_logic.is_grade_enabled(grade_cfg.weight, grade_cfg.goal))

            records_rows = (await session.execute(
                text('''
                    SELECT s.name,
                      (SELECT COUNT(DISTINCT (si.timestamp)::date) FROM "StudentInteractions" si
                        WHERE si.student_id = s.id AND si.timestamp >= :cut) AS days_active,
                      (SELECT COUNT(*) FROM "PendingQuizzes" pq
                        WHERE pq.student_id = s.id AND pq.status = 'answered' AND pq.answered_at >= :cut) AS quizzes,
                      (SELECT COUNT(*) FROM "StudentInteractions" si
                        WHERE si.student_id = s.id AND si.interaction_type = 'xp_event'
                          AND si.message_content = 'srs_ontime' AND si.course_id = :course) AS srs,
                      (SELECT COUNT(*) FROM "StudentInteractions" si
                        WHERE si.student_id = s.id AND si.interaction_type = 'xp_event'
                          AND si.message_content = 'reading' AND si.course_id = :course) AS reading,
                      s.engagement_streak AS streak,
                      (SELECT COALESCE(SUM(CASE WHEN si.xp_suspect = false THEN si.xp_awarded ELSE 0 END), 0)
                        FROM "StudentInteractions" si
                        WHERE si.student_id = s.id AND si.interaction_type = 'xp_event'
                          AND si.xp_week_id = :wk AND si.course_id = :course) AS week_xp
                    FROM "Students" s
                    JOIN "StudentEnrollment" se ON se.student_id = s.id
                    WHERE se.class_id = :cid AND s.is_active = true
                    ORDER BY s.name'''),
                {"cid": class_id, "course": course_id, "wk": week_id, "cut": cutoff7},
            )).fetchall()

        if not records_rows:
            await channel.send_text(sender_id, f"ℹ️ Nenhum aluno ativo na turma '{c_name}'.")
            return

        records = [
            gami_export.EngagementRecord(
                name=r.name, days_active=int(r.days_active or 0), quizzes=int(r.quizzes or 0),
                srs_reviews=int(r.srs or 0), reading_sections=int(r.reading or 0),
                streak=int(r.streak or 0), week_xp=int(r.week_xp or 0),
                grade_points=(grade_logic.grade_points(int(r.week_xp or 0), int(grade_cfg.goal),
                                                        float(grade_cfg.weight))
                              if grade_on and grade_cfg is not None else None),
            ) for r in records_rows
        ]
        csv_text = gami_export.rows_to_csv(
            gami_export.build_engagement_export_rows(records, include_grade=grade_on))

        await channel.send_text(
            sender_id,
            f"📎 *Engajamento — {c_name}* (semana {week_id})\n"
            f"CSV abaixo (copie para o seu diário/planilha — Modelo A, a nota é sua decisão):",
        )
        # Entrega o CSV em blocos dentro do limite de mensagem.
        for chunk in _chunk_text(csv_text, 3500):
            await channel.send_text(sender_id, chunk)
        track_event("engagement_exported", {"class_id": class_id, "students": len(records)})
    except Exception as e:
        logger.error("export_engagement_failed", error=str(e)[:300])
        await channel.send_text(sender_id, f"❌ Erro ao exportar engajamento: {str(e)[:100]}")


def _chunk_text(s: str, size: int) -> list[str]:
    """Quebra por LINHAS para não cortar uma linha de CSV no meio."""
    chunks: list[str] = []
    cur = ""
    for line in s.split("\n"):
        if len(cur) + len(line) + 1 > size and cur:
            chunks.append(cur)
            cur = ""
        cur += (("\n" if cur else "") + line)
    if cur:
        chunks.append(cur)
    return chunks or [""]


# ==============================================================================
# COMPONENTE DE NOTA (Fase 2, Modelo C — GRADE_COMPONENT_ENABLED, opt-in/turma)
# ⚠️ O ponto mais sensível: peso ≤ 10%, só adiciona, por esforço/threshold,
# fórmula transparente, anti-gaming aplicado ANTES (week_xp já exclui suspect).
# ==============================================================================

async def run_set_grade_component_command(channel, sender_id: str, class_query: str, extra: str) -> None:
    """/nota_engajamento_[turma]_[peso]_[meta] (ou _off) — opt-in do Modelo C.

    O sistema FORÇA o teto de peso (`MAX_GRADE_WEIGHT_PCT`) e devolve a fórmula
    EXATA que os alunos verão (transparência é requisito). Determinístico."""
    if not gamification_on() or not settings.GRADE_COMPONENT_ENABLED:
        return
    try:
        from src.workers.reading_commands import _resolve_teacher_and_class
        max_weight = grade_logic.MAX_GRADE_WEIGHT_PCT

        async with AsyncSessionLocal() as session:
            teacher, klass = await _resolve_teacher_and_class(session, channel, sender_id, class_query)
            if not teacher:
                await channel.send_text(sender_id, M.CMD_TEACHER_NOT_FOUND)
                return
            if not klass:
                await channel.send_text(sender_id, M.CMD_CLASS_NOT_FOUND.format(class_query=class_query))
                return
            class_id, class_name = klass[0], klass[1]

            arg = (extra or "").strip().lower()
            if arg in ("off", "desligar", "0"):
                await session.execute(
                    text('''UPDATE "Classes" SET grade_weight_pct = NULL, weekly_xp_goal = NULL,
                                updated_at = CURRENT_TIMESTAMP WHERE id = :cid'''),
                    {"cid": class_id},
                )
                await session.commit()
                await channel.send_text(sender_id, M.GRADE_DISABLED_OK.format(class_name=class_name))
                return

            parts = arg.replace(" ", "").split("_")
            if len(parts) != 2:
                await channel.send_text(sender_id, M.GRADE_SET_USAGE.format(max_weight=max_weight))
                return
            try:
                weight = float(parts[0].replace("%", "").replace(",", "."))
                goal = int(parts[1])
            except ValueError:
                await channel.send_text(sender_id, M.GRADE_INVALID_VALUES)
                return
            if weight <= 0 or goal <= 0:
                await channel.send_text(sender_id, M.GRADE_INVALID_VALUES)
                return

            capped = grade_logic.clamp_weight(weight)
            await session.execute(
                text('''UPDATE "Classes" SET grade_weight_pct = :w, weekly_xp_goal = :g,
                            updated_at = CURRENT_TIMESTAMP WHERE id = :cid'''),
                {"w": capped, "g": goal, "cid": class_id},
            )
            await session.commit()

        # weight legível (sem casas se inteiro).
        weight_label = int(capped) if float(capped).is_integer() else capped
        if capped < weight:
            await channel.send_text(sender_id, M.GRADE_WEIGHT_CAPPED.format(max_weight=max_weight))
        await channel.send_text(sender_id, M.GRADE_SET_CONFIRM.format(
            class_name=class_name, goal=goal, weight=weight_label))
        track_event("grade_component_set", {"class_id": class_id, "weight": capped, "goal": goal})
    except Exception as e:
        logger.error("set_grade_component_failed", error=str(e)[:300])
        await channel.send_text(sender_id, f"❌ Erro ao configurar componente de nota: {str(e)[:100]}")


async def run_my_grade_command(channel, sender_id: str) -> None:
    """/minha_nota — transparência total do componente de nota (Modelo C)."""
    if not gamification_on() or not settings.GRADE_COMPONENT_ENABLED:
        return
    try:
        query_col = channel.get_user_query_column()
        week_id = current_week_id()
        async with AsyncSessionLocal() as session:
            row = (await session.execute(
                text(f'''SELECT s.id, cl.course_id, cl.grade_weight_pct AS weight, cl.weekly_xp_goal AS goal
                         FROM "Students" s
                         JOIN "StudentEnrollment" se ON se.student_id = s.id
                         JOIN "Classes" cl ON cl.id = se.class_id
                         WHERE s.{query_col} = :sid AND s.is_active = true
                           AND cl.gamification_enabled = true
                           AND cl.grade_weight_pct IS NOT NULL AND cl.weekly_xp_goal IS NOT NULL
                         ORDER BY se.created_at LIMIT 1'''),
                {"sid": sender_id},
            )).fetchone()
            if not row:
                await channel.send_text(sender_id, M.MY_GRADE_NONE)
                return
            week_xp = (await session.execute(
                text('''SELECT COALESCE(SUM(CASE WHEN xp_suspect = false THEN xp_awarded ELSE 0 END), 0)
                        FROM "StudentInteractions"
                        WHERE student_id = :sid AND interaction_type = 'xp_event'
                          AND xp_week_id = :wk AND course_id = :cid'''),
                {"sid": row.id, "wk": week_id, "cid": row.course_id},
            )).scalar() or 0

        weight = float(row.weight)
        goal = int(row.goal)
        week_xp = int(week_xp)
        pct = round(grade_logic.engagement_fraction(week_xp, goal) * 100)
        points = grade_logic.grade_points(week_xp, goal, weight)
        gap = grade_logic.xp_to_full(week_xp, goal)
        remaining = M.MY_GRADE_FULL if gap == 0 else M.MY_GRADE_REMAINING.format(gap=gap)
        weight_label = int(weight) if weight.is_integer() else weight
        await channel.send_text(sender_id, M.MY_GRADE_BODY.format(
            goal=goal, week_xp=week_xp, pct=pct, points=points,
            weight=weight_label, remaining=remaining))
    except Exception as e:
        logger.error("my_grade_failed", error=str(e)[:300])
