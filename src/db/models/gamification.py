from sqlalchemy import (
    Column, String, DateTime, ForeignKey, Index, Integer, Boolean,
    UniqueConstraint,
)
from sqlalchemy.sql import func
from src.db.database import Base


class WeeklyEngagement(Base):
    """Snapshot competitivo semanal (Gamificação — Fase 0, §4/§10 do esquema).

    Uma linha por (aluno, curso, semana): o resultado DETERMINÍSTICO do batch
    `process_weekly_leaderboard` rodado pelo scheduler (custo $0 — sem LLM). É a
    "verdade" da liga da semana: a coorte a que o aluno pertenceu, o pseudônimo
    ANÔNIMO que recebeu (animal+cor, reatribuído a cada semana para impedir
    rastreio longitudinal por colegas), a posição e o XP somado.

    Escopo INVIOLÁVEL: toda leitura de ranking filtra por `teacher_id` E
    `course_id` (regra de segurança multi-tenant). `cohort_id` é estável dentro
    da semana (mesma coorte ⇒ mesmo placar comparável). Coorte com menos de
    MIN_COHORT_SIZE participantes não circula placar (reúso da regra de
    ClassChallenges) — `rank_in_cohort` ainda é gravado, mas o aluno só vê
    progresso pessoal.

    Persistência: o snapshot da semana fica para histórico/auditoria; o número
    EXIBIDO na competição é sempre o da semana corrente (zera segunda 00:00 BRT).
    """
    __tablename__ = "WeeklyEngagement"

    id = Column(String(255), primary_key=True)
    student_id = Column(String(255), ForeignKey("Students.id", ondelete="CASCADE"), nullable=False, index=True)
    course_id = Column(String(255), ForeignKey("Courses.id", ondelete="CASCADE"), nullable=False, index=True)
    teacher_id = Column(String(255), ForeignKey("Teachers.id"), nullable=True, index=True)
    # ISO week BRT: "2026-W26". Fronteira da liga (zera segunda 00:00 BRT).
    week_id = Column(String(20), nullable=False)
    # Coorte = turma (≤ ~30) ou fatia pareada por engajamento (> 30). Estável na semana.
    cohort_id = Column(String(255), nullable=True, index=True)
    cohort_size = Column(Integer, nullable=False, default=0)
    xp_week = Column(Integer, nullable=False, default=0)
    rank_in_cohort = Column(Integer, nullable=True)
    # Pseudônimo do sistema (animal+cor). Reatribuído por semana — nunca o nome real.
    pseudonym = Column(String(100), nullable=True)
    # Ligas (Fase 1): divisão (bronze→diamante) para a PRÓXIMA semana, resultado do
    # movimento (promo/rebaixamento). NULL = ranking flat (Fase 0, LEAGUES off).
    division = Column(String(20), nullable=True)
    # Espelha Students.ranking_opted_out no momento do snapshot (auditoria).
    opted_out_ranking = Column(Boolean, nullable=False, default=False, server_default="false")

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('student_id', 'course_id', 'week_id',
                         name='uq_weekly_engagement_scope'),
        Index('idx_weekly_engagement_cohort', 'course_id', 'week_id', 'cohort_id'),
    )


class StudentBadges(Base):
    """Conquistas persistentes (Gamificação — Fase 0, §6/§10 do esquema).

    Uma linha por (aluno, curso, badge): o nível MAIS ALTO já desbloqueado de
    cada badge. Checagem 100% DETERMINÍSTICA (contadores sobre interações que já
    existem) — sem LLM. Escopada por `teacher_id`+`course_id` (regra inviolável).

    `level` é o rótulo do degrau ('bronze'|'prata'|'ouro'|'lendaria' ou um nível
    livre para badges não-empilhados). `seen_at` NULL = ainda não revelado ao
    aluno (badges acumulam em silêncio e aparecem no resumo semanal / `/conquistas`;
    só Ouro/Lendária notificam na hora, e só dentro da janela de 24h). Badges
    NUNCA expiram.
    """
    __tablename__ = "StudentBadges"

    id = Column(String(255), primary_key=True)
    student_id = Column(String(255), ForeignKey("Students.id", ondelete="CASCADE"), nullable=False, index=True)
    course_id = Column(String(255), ForeignKey("Courses.id", ondelete="CASCADE"), nullable=False, index=True)
    teacher_id = Column(String(255), ForeignKey("Teachers.id"), nullable=True, index=True)
    badge_code = Column(String(50), nullable=False)   # 'chama_viva', 'na_mosca', ...
    level = Column(String(20), nullable=False, default="bronze")
    unlocked_at = Column(DateTime, server_default=func.now())
    seen_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('student_id', 'course_id', 'badge_code',
                         name='uq_student_badge_scope'),
        Index('idx_student_badge_lookup', 'student_id', 'course_id'),
    )
