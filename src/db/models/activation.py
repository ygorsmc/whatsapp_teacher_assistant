from sqlalchemy import Column, String, DateTime, ForeignKey, Index, Integer, Text, text
from sqlalchemy.sql import func
from src.db.database import Base


class ActivationKeys(Base):
    """Chaves de ativação por QR Code (H2.1 Livro Vivo / H3.1 Professor).

    O QR impresso no material abre o WhatsApp com a chave pré-preenchida
    (`wa.me/<numero>?text=...LIVRO-XXXX-XXXX`). A ativação cria o usuário
    on-the-fly:
      - kind='reader'  → cria Student (auto-cadastro + consentimento LGPD),
        matricula na turma-coorte (`class_id`) e abre o ReadingPlan.
      - kind='teacher' → cria Teacher (onboarding self-service); class_id NULL.

    Fonte das chaves: aba "Chaves" da planilha (sync ingere; o escopo Google é
    readonly, então o bot nunca ESCREVE lá) ou a API de parceiros (H3.3).
    Anti-pirataria: `max_activations` (default 1) + revogação por lote via
    planilha (`Ativa=FALSE` → status 'revoked').
    """
    __tablename__ = "ActivationKeys"

    key = Column(String(50), primary_key=True)  # "LIVRO-XXXX-XXXX" | "PROF-XXXX-XXXX"
    kind = Column(String(20), nullable=False, default="reader")  # 'reader' | 'teacher'
    # Turma-coorte do livro (obrigatória para reader; NULL para teacher).
    class_id = Column(String(255), ForeignKey("Classes.id"), nullable=True, index=True)
    batch = Column(String(100), nullable=True)  # lote (ex: "1a-edicao")
    max_activations = Column(Integer, nullable=False, default=1)
    activation_count = Column(Integer, nullable=False, default=0)
    status = Column(String(20), nullable=False, default="available")  # 'available' | 'activated' | 'revoked'
    first_activated_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index('idx_activation_key_class', 'class_id', 'status'),
    )


class KeyActivations(Base):
    """Auditoria de ativações (anti-fraude): quem ativou qual chave, quando e
    por qual canal. Permite investigar chaves vazadas e revogar lotes.

    Anti-duplo-consumo (migration b8c9d0e1f2a3): índices únicos PARCIAIS
    `(key, student_id)` e `(key, teacher_id)` garantem que o MESMO titular não
    consome a mesma chave duas vezes — `_consume_key` usa ON CONFLICT sobre eles.
    """
    __tablename__ = "KeyActivations"

    id = Column(String(255), primary_key=True)
    key = Column(String(50), ForeignKey("ActivationKeys.key"), nullable=False, index=True)
    student_id = Column(String(255), ForeignKey("Students.id"), nullable=True, index=True)
    teacher_id = Column(String(255), ForeignKey("Teachers.id"), nullable=True, index=True)
    channel = Column(String(20), nullable=False, default="whatsapp")
    activated_at = Column(DateTime, nullable=False)

    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index('uq_keyactivation_student', 'key', 'student_id',
              unique=True, postgresql_where=text("student_id IS NOT NULL")),
        Index('uq_keyactivation_teacher', 'key', 'teacher_id',
              unique=True, postgresql_where=text("teacher_id IS NOT NULL")),
    )


class Rubrics(Base):
    """Rubrica de correção dissertativa do professor (H2.2).

    A correção da redação/peça é SEMPRE ancorada nos critérios cadastrados
    pelo professor (mesma filosofia do Gabarito Verificado) — nunca em
    critérios genéricos do LLM. Uma rubrica por (curso, disciplina).
    """
    __tablename__ = "Rubrics"

    id = Column(String(255), primary_key=True)
    course_id = Column(String(255), ForeignKey("Courses.id"), nullable=False, index=True)
    subject_id = Column(String(255), ForeignKey("Subjects.id"), nullable=True, index=True)
    criteria = Column(Text, nullable=False)          # critérios numerados, texto livre do professor
    scale = Column(String(100), nullable=False, default="0 a 10")  # ex: "0-1000 estilo ENEM"

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index('idx_rubric_scope', 'course_id', 'subject_id'),
    )
