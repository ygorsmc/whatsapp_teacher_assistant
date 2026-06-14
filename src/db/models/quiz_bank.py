from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Index, Text
from sqlalchemy.sql import func
from src.db.database import Base


class QuizBank(Base):
    """Banco de Questões Emergente (Cache Semântico "Lazy").

    Cada linha é um "pacote pedagógico" completo gerado pelo LLM para a tripla
    (Tópico + Lente Pedagógica + Aula). Uma vez gerado para UM aluno, o pacote
    fica disponível para a turma inteira a custo $0 (bypass do LLM).

    Escopo de isolamento: (course_id, subject_id, lesson_number). O subject_id
    é nullable para tolerar turmas legadas sem Disciplina — nesses casos o
    lookup filtra explicitamente por `subject_id IS NULL`.

    Ciclo de vida:
      - Criado on-demand pelo `review_generator` (cache miss).
      - Invalidado pelo `ingestion_worker` quando um novo PDF da aula é
        processado (material mudou → questões antigas podem estar obsoletas).
      - Apagado manualmente pelo professor via `/limpar_quizzes_[turma]`.

    O vínculo de consumo por aluno fica em `PendingQuizzes.quiz_bank_id`
    (cada entrega a um aluno gera uma linha lá) — é essa junção que garante
    que o mesmo aluno nunca receba a mesma dupla (Tópico + Lente) duas vezes.
    """
    __tablename__ = "QuizBank"

    id = Column(String(255), primary_key=True)
    course_id = Column(String(255), ForeignKey("Courses.id"), nullable=False, index=True)
    # Disciplina dona do material. Nullable para legado (turmas sem Subject).
    subject_id = Column(String(255), ForeignKey("Subjects.id"), nullable=True, index=True)
    lesson_number = Column(Integer, nullable=False)
    # Tipo do material de origem (Addon de Livros): 'lesson' | 'book' | 'workbook'.
    # Para livros/apostilas, lesson_number representa o capítulo/seção.
    material_type = Column(String(50), nullable=False, default="lesson", server_default="lesson")

    # A dupla que define a variação pedagógica da questão
    topic = Column(String(500), nullable=False, index=True)
    pedagogical_lens = Column(String(500), nullable=False)

    # Conteúdo do quiz
    question_text = Column(Text, nullable=False)
    alternatives = Column(Text, nullable=False)   # JSON string: [{"id": "A", "text": "..."}]
    correct_answer = Column(String(255), nullable=False)
    explanation = Column(Text, nullable=False)

    # O "pacote" completo: o fluxo de revisão entrega resumo + pontos-chave
    # ANTES do quiz. Cacheá-los junto torna o Cache Hit 100% offline (sem LLM).
    summary = Column(Text, nullable=True)
    key_points = Column(Text, nullable=True)      # JSON string: ["ponto 1", "ponto 2"]
    # Vertical de Idiomas: roteiro de ÁUDIO na língua-alvo (vocab: palavra+exemplo;
    # revisão traduzida). Persistido p/ sobreviver ao Cache Hit e à reentrega SRS.
    audio_script = Column(Text, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        # Índice composto que cobre exatamente o lookup do Cache Hit:
        # WHERE course_id = X AND subject_id = Y AND lesson_number = Z AND topic = T
        Index('idx_quizbank_scope', 'course_id', 'subject_id', 'lesson_number', 'topic'),
    )
