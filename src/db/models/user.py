from sqlalchemy import Column, String, Boolean, DateTime, Date, Text, ForeignKey, Index, Integer
from sqlalchemy.sql import func
from src.db.database import Base

class Teacher(Base):
    __tablename__ = "Teachers"
    
    id = Column(String(255), primary_key=True)
    name = Column(String(255), nullable=False)
    subject = Column(String(255), nullable=False)
    phone = Column(String(255), unique=True, nullable=False)
    telegram_chat_id = Column(String(255), unique=True, nullable=True)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False, server_default="false", nullable=False)
    
    assistant_persona = Column(Text, nullable=False)
    out_of_scope_message = Column(Text, nullable=False)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class Student(Base):
    __tablename__ = "Students"

    id = Column(String(255), primary_key=True)
    name = Column(String(255), nullable=False)
    phone = Column(String(255), unique=True, nullable=False)
    telegram_chat_id = Column(String(255), unique=True, nullable=True)
    # Roteamento dinâmico "follow-the-user": canal onde o aluno mandou a última
    # mensagem ('whatsapp' | 'telegram'). O scheduler entrega o próximo quiz por
    # aqui. NULL = usa o canal global (settings.CHANNEL_BACKEND).
    preferred_channel = Column(String(50), nullable=True)
    # Origem do cadastro (H2.1): 'spreadsheet' (sync clássica) |
    # 'qr_activation' (leitor auto-cadastrado via chave do livro) |
    # 'teacher_command' (valor legado — onboarding self-service descontinuado).
    # Leitores QR podem se autoexcluir via /sair_livro (LGPD); alunos de
    # planilha não.
    registration_source = Column(String(50), nullable=False, default="spreadsheet", server_default="spreadsheet")
    # Consentimento LGPD registrado no auto-cadastro (NULL para alunos de
    # planilha, cujo vínculo é gerido pelo professor).
    consent_at = Column(DateTime, nullable=True)
    # LGPD P1.1 (Art. 14): criança (<12) exige opt-in do responsável antes de
    # QUALQUER interação. Informado pela escola (controladora) na planilha —
    # colunas opcionais "Responsável" e "Menor". O gate só atua com a flag
    # GUARDIAN_CONSENT_ENABLED (política por contrato).
    guardian_phone = Column(String(255), nullable=True)
    is_child = Column(Boolean, default=False, server_default="false", nullable=False)
    is_active = Column(Boolean, default=True)
    # F9 (Quality Rating Meta): opt-out de mensagens proativas ("Envie SAIR").
    # As filas proativas respeitam esta flag; default false = comportamento atual.
    messaging_opted_out = Column(Boolean, default=False, server_default="false", nullable=False)

    # ── Gamificação (Fase 0) — streak de engajamento (número PESSOAL) ─────────
    # Streak "guarda-chuva" por aluno: conta QUALQUER ato que gere XP (quiz, SRS,
    # microdose, dúvida, redação/voz). Mora aqui (não em ReadingPlans, que é por
    # livro) porque é exibido só ao próprio aluno ⇒ sem vazamento entre tenants.
    # Com STREAK_ENABLED OFF, nunca são escritas ⇒ comportamento idêntico.
    #   - engagement_streak: dias consecutivos ativos.
    #   - engagement_streak_best: recorde (PERSISTE mesmo ao quebrar).
    #   - streak_shields: escudos guardados (ganhos a cada 7 dias, máx. 2);
    #     um dia perdido consome 1 e preserva o streak — sem culpa, sem penalidade.
    #   - streak_last_active_date: último dia (BRT) com ato válido.
    engagement_streak = Column(Integer, nullable=False, default=0, server_default="0")
    engagement_streak_best = Column(Integer, nullable=False, default=0, server_default="0")
    streak_shields = Column(Integer, nullable=False, default=0, server_default="0")
    streak_last_active_date = Column(Date, nullable=True)
    # Opt-out SÓ do ranking (≠ `messaging_opted_out`, que silencia TUDO via "sair").
    # Acionado por /ocultar_ranking: o aluno segue ganhando XP/streak/badges
    # (privados), mas não aparece em coorte nenhuma nem recebe a seção de liga.
    ranking_opted_out = Column(Boolean, default=False, server_default="false", nullable=False)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class GuardianConsents(Base):
    """Opt-in do responsável legal (LGPD Art. 14, Plano P1.1).

    Uma linha por pedido de consentimento enviado ao responsável; `status`:
    'pending' → 'granted' | 'denied' | 'revoked'. `term_version` registra QUAL
    texto foi aceito (accountability). O bot não interage com a criança
    enquanto não houver linha 'granted'.
    """
    __tablename__ = "GuardianConsents"

    id = Column(String(255), primary_key=True)
    student_id = Column(String(255), ForeignKey("Students.id"), nullable=False, index=True)
    guardian_phone = Column(String(255), nullable=False)
    status = Column(String(50), nullable=False, default="pending")  # pending|granted|denied|revoked
    term_version = Column(String(50), nullable=False, default="v1")
    requested_at = Column(DateTime, server_default=func.now())
    consented_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index('idx_guardian_consent_status', 'student_id', 'status'),
    )


class DataSubjectRequests(Base):
    """Registro de pedidos de titulares (LGPD Art. 18, Plano P1.2).

    Trilha de auditoria do atendimento: quando o pedido chegou, o que era e
    quando foi concluído. Para alunos de escola (plataforma = operadora), o
    pedido fica 'forwarded' até o controlador confirmar.
    """
    __tablename__ = "DataSubjectRequests"

    id = Column(String(255), primary_key=True)
    student_id = Column(String(255), nullable=True, index=True)  # sem FK: sobrevive à anonimização
    request_type = Column(String(50), nullable=False)  # 'erasure' | 'review_essay' | 'consent_revoke'
    status = Column(String(50), nullable=False, default="received")  # received|forwarded|completed
    channel = Column(String(50), nullable=True)
    details = Column(Text, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
