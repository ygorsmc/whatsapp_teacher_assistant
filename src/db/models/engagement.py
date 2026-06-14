from sqlalchemy import Column, String, DateTime, ForeignKey, Index, Integer, Text, Float
from sqlalchemy.sql import func
from src.db.database import Base


class ClassChallenges(Base):
    """Desafio de Turma / Simulado Cronometrado (Horizontes H1.1 e H2.3).

    Accountability social SEM grupo aberto: a mesma questão (ou conjunto, no
    simulado) é enviada à turma inteira; quando `closes_at` vence, o scheduler
    agrega as respostas (via PendingQuizzes.challenge_id) e devolve a cada
    participante um placar ANÔNIMO ("73% da turma acertou — você foi um
    deles 🏅") + um resumo ao professor.

    LGPD: nada individual circula para a turma; coortes com menos de
    MIN_COHORT_SIZE participantes recebem só o feedback individual.
    """
    __tablename__ = "ClassChallenges"

    id = Column(String(255), primary_key=True)
    class_id = Column(String(255), ForeignKey("Classes.id"), nullable=False, index=True)
    # Questão âncora (desafio = 1 questão). No simulado, as N questões apontam
    # para o challenge via PendingQuizzes.challenge_id e este campo é NULL.
    quiz_bank_id = Column(
        String(255), ForeignKey("QuizBank.id", ondelete="CASCADE"), nullable=True, index=True
    )
    kind = Column(String(50), nullable=False, default="challenge")  # 'challenge' | 'simulado'
    question_count = Column(Integer, nullable=False, default=1)
    sent_at = Column(DateTime, nullable=False)
    closes_at = Column(DateTime, nullable=False)
    status = Column(String(50), nullable=False, default="open")  # 'open' | 'closed'
    participants_count = Column(Integer, nullable=False, default=0)
    correct_count = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index('idx_challenge_close', 'closes_at', 'status'),
    )


class SentinelAlerts(Base):
    """Sentinela Emocional (H1.2) — log de alertas enviados ao professor.

    Serve de DEDUPE: a mesma regra para o mesmo aluno não dispara de novo por
    7 dias (evita transformar o cuidado em spam). `details` guarda o resumo
    legível que foi enviado.
    """
    __tablename__ = "SentinelAlerts"

    id = Column(String(255), primary_key=True)
    student_id = Column(String(255), ForeignKey("Students.id"), nullable=False, index=True)
    class_id = Column(String(255), ForeignKey("Classes.id"), nullable=True, index=True)
    rule = Column(String(100), nullable=False)  # 'consecutive_errors' | 'streak_broken' | 'gone_quiet'
    details = Column(Text, nullable=True)
    notified_at = Column(DateTime, nullable=False)

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index('idx_sentinel_dedupe', 'student_id', 'rule', 'notified_at'),
    )


class PronunciationAttempts(Base):
    """Tentativas de pronúncia (F3 — Vertical de Idiomas).

    Guarda APENAS os scores da análise de fala — accuracy/fluency/completeness +
    detalhe por palavra (`details_json`). O ÁUDIO é descartado após a análise
    (LGPD: voz é dado pessoal sensível); nunca persistimos o arquivo de voz, só os
    números. FK em CASCADE para o expurgo do aluno levar os scores junto. Insumo do
    "Inventário de Erros" (F6) — agregação on-the-fly, coorte >= MIN_COHORT_SIZE.
    """
    __tablename__ = "PronunciationAttempts"

    id = Column(String(255), primary_key=True)
    student_id = Column(String(255), ForeignKey("Students.id", ondelete="CASCADE"), nullable=False, index=True)
    course_id = Column(String(255), ForeignKey("Courses.id", ondelete="CASCADE"), nullable=False)
    language = Column(String(20), nullable=True)       # ISO 639-1 do idioma-alvo
    item_ref = Column(Text, nullable=True)             # texto de referência (transcrição) — SEM áudio
    accuracy = Column(Float, nullable=True)
    fluency = Column(Float, nullable=True)
    completeness = Column(Float, nullable=True)
    details_json = Column(Text, nullable=True)         # scores por palavra (JSON) — SEM áudio

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index('idx_pron_attempts_scope', 'course_id', 'student_id'),
    )
