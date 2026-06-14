from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Index, Text
from sqlalchemy.sql import func
from src.db.database import Base

class PendingQuizzes(Base):
    __tablename__ = "PendingQuizzes"
    
    quiz_id = Column(String(255), primary_key=True)
    student_id = Column(String(255), ForeignKey("Students.id"), nullable=False)
    course_id = Column(String(255), ForeignKey("Courses.id"), nullable=False)
    lesson_id = Column(String(255), nullable=False)
    question_text = Column(Text, nullable=False)
    alternatives = Column(Text, nullable=False) # Store JSON string
    correct_answer = Column(String(255), nullable=False)
    explanation = Column(Text, nullable=False)
    student_answer = Column(String(255), nullable=True)
    is_correct = Column(Boolean, nullable=True)
    status = Column(String(255), default="pending") # 'pending', 'answered', 'expired'

    # ── Rastreio do Banco de Questões (Cache Semântico) ─────────────────────
    # Aponta para a questão de origem no QuizBank. É esta FK que:
    #   1. Impede que o mesmo aluno receba a mesma dupla (Tópico+Lente) 2x
    #      (a query de Cache Hit usa NOT EXISTS sobre esta coluna);
    #   2. Permite ao comando /acertos agregar acertos/erros por `topic`
    #      via JOIN PendingQuizzes ↔ QuizBank.
    # ondelete="SET NULL": o /limpar_quizzes deleta FISICAMENTE o QuizBank,
    # mas o histórico de respostas do aluno sobrevive (PendingQuizzes já
    # denormaliza a questão completa nas colunas acima).
    quiz_bank_id = Column(
        String(255),
        ForeignKey("QuizBank.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Addon de Livros: quiz de check-in da Jornada de Leitura ─────────────
    # Quando preenchido, este quiz é o "portão" da seção sugerida: acertar
    # avança ReadingPlans.current_section (+ streak); errar oferece reler o
    # trecho. ondelete="SET NULL": apagar o plano não apaga o histórico.
    reading_plan_id = Column(
        String(255),
        ForeignKey("ReadingPlans.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── H1.1/H2.3: vínculo com Desafio de Turma / Simulado Cronometrado ─────
    # Quando preenchido, esta entrega faz parte de um ClassChallenge; o
    # fechamento agrega as respostas por aqui (placar anônimo / percentil).
    challenge_id = Column(
        String(255),
        ForeignKey("ClassChallenges.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    expires_at = Column(DateTime, nullable=False)
    answered_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index('idx_pending_quiz_student', 'student_id', 'status'),
    )
