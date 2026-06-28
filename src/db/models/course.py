from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Integer, Numeric, Text, UniqueConstraint
from sqlalchemy.sql import func
from src.db.database import Base

class Course(Base):
    __tablename__ = "Courses"

    id = Column(String(255), primary_key=True)
    teacher_id = Column(String(255), ForeignKey("Teachers.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class Subject(Base):
    """Disciplina — camada intermediária entre Curso e Turma.

    Um Curso (ex.: "Inteligência Artificial") contém várias Disciplinas
    (ex.: "Design Thinking", "PIBEP"). A posse do material e o RAG passam a
    ser isolados por Disciplina: cada Turma pertence a exatamente uma Subject
    (fonte autoritativa: aba "Turmas" da planilha).
    """
    __tablename__ = "Subjects"

    id = Column(String(255), primary_key=True)
    course_id = Column(String(255), ForeignKey("Courses.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('course_id', 'name', name='uq_subject_course_name'),
    )


class CourseClass(Base):
    __tablename__ = "Classes"

    id = Column(String(255), primary_key=True)
    course_id = Column(String(255), ForeignKey("Courses.id"), nullable=False)
    # Disciplina da turma. Uma turma pertence a UMA disciplina (chave única por
    # turma). Nullable para tolerar legado/transição antes do backfill da sync.
    subject_id = Column(String(255), ForeignKey("Subjects.id"), nullable=True, index=True)
    # Dono da turma. A posse passou a ser por TURMA (não mais só por curso):
    # um mesmo curso pode ter turmas de professores diferentes. Preenchido pela
    # sync a partir da aba "Professores" (Curso + Turma). Nullable para tolerar
    # turmas legadas antes do backfill da migration.
    teacher_id = Column(String(255), ForeignKey("Teachers.id"), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    current_lesson_number = Column(Integer, default=1, nullable=False)
    
    start_date = Column(DateTime, nullable=True)  # Store as DateTime for simpler SQLite compat
    schedule_map = Column(Text, nullable=True)    # JSON string mapping weekday(0-6) -> "19:00"
    last_advanced_date = Column(DateTime, nullable=True) # To prevent double advancement on the same day

    # Modo Professor Curador (Addon de Livros): JSON string com a lista de
    # capítulos ativos do livro/apostila (ex: "[1, 3, 5, 7]"). NULL = todos
    # ativos. Capítulos fora da lista são ignorados pelo scheduler da Jornada
    # de Leitura e pelo RAG de materiais densos.
    active_chapters = Column(Text, nullable=True)

    # Vertical de Idiomas — perfil por turma (aba "Turmas": colunas Idioma/Nível).
    # is_language: turma de curso de idioma (switch autoritativo do recurso de
    # áudio/idioma). proficiency_level: Iniciante|Intermediário|Avançado|Fluente
    # (NULL = não informado). O idioma-alvo é inferido pelo NOME do curso (§1.4).
    is_language = Column(Boolean, nullable=False, default=False, server_default="false")
    proficiency_level = Column(String(20), nullable=True)

    # ── Gamificação (Fase 2 — Engajamento como % da nota, OPT-IN) ─────────────
    # NULL = Modelo A ("relatório, não nota"): o sistema entrega os dados e a
    # decisão de virar nota é 100% do professor. Preenchidos só quando o
    # professor opta pelo Modelo C (GRADE_COMPONENT_ENABLED, a flag de maior
    # risco — última a ligar). Colunas adicionadas já na Fase 0 (baratas, NULL):
    #   - grade_weight_pct: peso do engajamento na nota (teto 5–10%).
    #   - weekly_xp_goal: piso semanal de XP (esforço por threshold, não por rank).
    grade_weight_pct = Column(Numeric(5, 2), nullable=True)
    weekly_xp_goal = Column(Integer, nullable=True)

    # Ativação da GAMIFICAÇÃO por turma (decisão professor/instituição na aba
    # "Turmas" da planilha — coluna "Gaming"). O flag global GAMIFICATION_ENABLED
    # é a chave-mestra/kill-switch; esta coluna é a opção real por turma. Default
    # false = turma fora da gamificação até a planilha optar (opt-in deliberado).
    gamification_enabled = Column(Boolean, nullable=False, default=False, server_default="false")

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class StudentEnrollment(Base):
    __tablename__ = "StudentEnrollment"
    
    enrollment_id = Column(String(255), primary_key=True)
    student_id = Column(String(255), ForeignKey("Students.id"), nullable=False)
    class_id = Column(String(255), ForeignKey("Classes.id"), nullable=False)
    # Vertical de Idiomas (F5): nível CEFR (A1–C2) do aluno NESTA turma, definido
    # pelo teste de nivelamento. NULL = ainda não nivelado.
    cefr_level = Column(String(10), nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    __table_args__ = (
        UniqueConstraint('student_id', 'class_id', name='uq_student_class_enrollment'),
    )
