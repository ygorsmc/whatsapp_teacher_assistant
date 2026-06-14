from sqlalchemy import (
    Column, String, Boolean, DateTime, ForeignKey, Index, Integer, Float, Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func
from src.db.database import Base


class ReadingPlans(Base):
    """Jornada de Leitura Guiada (Addon de Livros e Apostilas).

    Uma linha por (aluno, curso, disciplina, material): controla o ponteiro de
    leitura individual sobre um material denso (livro/apostila), o ritmo
    (microdoses diárias/semanais), o streak e o estado de pausa ("modo férias").

    Semântica do ponteiro:
      - `current_section` = número de seções CONCLUÍDAS (0 = ainda não começou).
      - A próxima sugestão de leitura sempre aponta para `current_section + 1`.
      - O avanço acontece quando o aluno PASSA no quiz de check-in (portão),
        nunca por incremento cego do scheduler — replanejamento sem culpa.

    Colunas operacionais (além do spec):
      - `next_trigger_at`     → quando o scheduler deve enviar a próxima sugestão.
      - `last_suggestion_at`  → quando a última sugestão foi enviada (detecta o
                                aluno que "não acumula": sugere recalcular).
      - `next_checkin_at`     → quando o quiz de check-in da seção sugerida deve
                                ser disparado (ex.: sugestão 07:00 → quiz 19:00).
    """
    __tablename__ = "ReadingPlans"

    id = Column(String(255), primary_key=True)
    student_id = Column(String(255), ForeignKey("Students.id"), nullable=False, index=True)
    course_id = Column(String(255), ForeignKey("Courses.id"), nullable=False, index=True)
    # Nullable para turmas legadas sem Disciplina (mesmo padrão do QuizBank).
    subject_id = Column(String(255), ForeignKey("Subjects.id"), nullable=True, index=True)

    material_type = Column(String(50), nullable=False, default="book")  # 'book' | 'workbook'
    total_sections = Column(Integer, nullable=False, default=1)
    current_section = Column(Integer, nullable=False, default=0)
    pace = Column(String(50), nullable=False, default="daily")  # 'daily' | 'weekly'
    preferred_time = Column(String(5), nullable=False, default="07:00")  # "HH:MM" local (BRT)
    streak_days = Column(Integer, nullable=False, default=0)
    last_read_at = Column(DateTime, nullable=True)
    is_paused = Column(Boolean, default=False, server_default="false", nullable=False)

    next_trigger_at = Column(DateTime, nullable=True)
    last_suggestion_at = Column(DateTime, nullable=True)
    next_checkin_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        # NOTA: subject_id NULL não deduplica via UNIQUE no Postgres (NULLs são
        # distintos) — o upsert do /plano_leitura faz SELECT→UPDATE/INSERT
        # explícito. A constraint cobre o caso comum (turma com Disciplina).
        UniqueConstraint('student_id', 'course_id', 'subject_id', 'material_type',
                         name='uq_reading_plan_scope'),
        Index('idx_reading_plan_trigger', 'next_trigger_at', 'is_paused'),
        Index('idx_reading_plan_checkin', 'next_checkin_at', 'is_paused'),
    )


class SpacedRepetitionQueue(Base):
    """Fila de Revisão Espaçada (D+3 → D+10 → D+30).

    Uma linha por (aluno, questão do QuizBank). Criada na PRIMEIRA resposta do
    aluno a uma questão do banco; o scheduler reentrega a questão (cache hit
    puro, custo $0) quando `next_review_at` vence.

    Escada de intervalos (ajustada a cada resposta de revisão):
      acertou → 3 → 10 → 30 → 'completed'
      errou   → volta para 3 (recomeça o ciclo)

    `ease_factor` é registrado no espírito do SM-2 (2.5 inicial, +0.1 acerto,
    -0.2 erro, piso 1.3) para evoluções futuras — a escada atual é fixa.

    FK com ondelete=CASCADE: se a questão for invalidada/apagada do QuizBank
    (material atualizado ou /limpar_quizzes), a revisão agendada morre junto —
    não existe como reentregar uma questão que não existe mais.
    """
    __tablename__ = "SpacedRepetitionQueue"

    id = Column(String(255), primary_key=True)
    student_id = Column(String(255), ForeignKey("Students.id"), nullable=False, index=True)
    quiz_bank_id = Column(
        String(255),
        ForeignKey("QuizBank.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    next_review_at = Column(DateTime, nullable=False)
    interval_days = Column(Integer, nullable=False, default=3)  # 3, 10, 30
    repetition_count = Column(Integer, nullable=False, default=0)
    ease_factor = Column(Float, nullable=False, default=2.5)
    status = Column(String(50), nullable=False, default="pending")  # 'pending' | 'completed' | 'skipped'

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('student_id', 'quiz_bank_id', name='uq_srq_student_quiz'),
        Index('idx_srq_due', 'next_review_at', 'status'),
    )


class AnswerKeys(Base):
    """Gabarito Verificado (Fase 5 — Explicação Sob Demanda).

    Quando o professor/autor cadastra a resposta oficial de um exercício
    (`/gabarito_[turma]_cap3_ex2_42J`), o LLM usa essa resposta como ÂNCORA da
    explicação socrática em vez de resolver do zero — elimina alucinação.

    `section_ref` é canônico: "cap{capítulo}_ex{exercício}" (ex.: "cap3_ex2").
    """
    __tablename__ = "AnswerKeys"

    id = Column(String(255), primary_key=True)
    course_id = Column(String(255), ForeignKey("Courses.id"), nullable=False, index=True)
    subject_id = Column(String(255), ForeignKey("Subjects.id"), nullable=True, index=True)
    material_type = Column(String(50), nullable=False, default="book")
    section_ref = Column(String(100), nullable=False)
    correct_answer = Column(Text, nullable=False)
    explanation = Column(Text, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index('idx_answer_key_lookup', 'course_id', 'subject_id', 'section_ref'),
    )


class LessonChapterLinks(Base):
    """Mapeamento Aula ↔ Capítulo (Fase 6.5).

    Criado por `/vincular_aula_[turma]_[aula]_[capítulo]`. Quando o aluno
    responde um quiz da aula N, o bot sugere revisar o capítulo vinculado.
    """
    __tablename__ = "LessonChapterLinks"

    id = Column(String(255), primary_key=True)
    course_id = Column(String(255), ForeignKey("Courses.id"), nullable=False, index=True)
    subject_id = Column(String(255), ForeignKey("Subjects.id"), nullable=True, index=True)
    lesson_number = Column(Integer, nullable=False)
    chapter_number = Column(Integer, nullable=False)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('course_id', 'subject_id', 'lesson_number',
                         name='uq_lesson_chapter_link'),
    )


class Erratas(Base):
    """Errata Viva (Fase 6.6).

    Cadastrada por `/errata_[turma]_[seção]_[correção]`. Quando o aluno estiver
    lendo ou perguntando sobre a seção, o bot injeta automaticamente o aviso:
    "⚠️ Atenção: há uma correção nesta seção...".

    `section_number` é o número extraído de `section_ref` ("cap3" → 3) — é por
    ele que a sugestão de leitura e o RAG fazem o match da seção corrente.
    """
    __tablename__ = "Erratas"

    id = Column(String(255), primary_key=True)
    course_id = Column(String(255), ForeignKey("Courses.id"), nullable=False, index=True)
    subject_id = Column(String(255), ForeignKey("Subjects.id"), nullable=True, index=True)
    material_type = Column(String(50), nullable=False, default="book")
    section_ref = Column(String(100), nullable=False)
    section_number = Column(Integer, nullable=True)
    correction = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True, server_default="true", nullable=False)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index('idx_errata_lookup', 'course_id', 'subject_id', 'section_number'),
    )
