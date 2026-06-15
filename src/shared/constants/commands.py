"""
Constants for Teacher Commands in WhatsApp.
Maps command aliases to internal system actions.
"""

import re

class CommandType:
    SYNC_STUDENTS = "sync_students"
    SYNC_MATERIALS = "sync_materials"
    HELP = "help"
    LIST_COURSES = "list_courses"
    LIST_MATERIALS = "list_materials"
    DELETE_MATERIAL = "delete_material"  # /delete_[nº da lista do /listar_materiais]
    UPDATE_LESSON = "update_lesson"
    WAKEUP = "wakeup"
    SEND_QUIZ = "send_quiz"
    # ── Comandos gerenciais (sintaxe explícita /comando_[turma]_[arg]) ──────
    ENGAGEMENT = "engagement"            # /engajamento_[turma]
    QUIZ_PERFORMANCE = "quiz_performance"  # /acertos_[turma]
    AT_RISK = "at_risk"                  # /risco_[turma]
    FREQUENT_DOUBTS = "frequent_doubts"  # /duvidas_frequentes_[turma]
    VIEW_STUDENT = "view_student"        # /ver_aluno_[telefone]
    RESET_STUDENT = "reset_student"      # /resetar_aluno_[telefone]
    CLEAR_QUIZ_BANK = "clear_quiz_bank"  # /limpar_quizzes_[turma]
    BROADCAST_NOTICE = "broadcast_notice"  # /aviso_[turma]_[mensagem]
    # ── Addon de Livros — comandos do professor ──────────────────────────────
    READING_PLAN = "reading_plan"          # /plano_leitura_[turma]_[ritmo]
    SIMULADO = "simulado"                  # /simulado_[turma]_[até_capítulo]
    LINK_LESSON_CHAPTER = "link_lesson_chapter"  # /vincular_aula_[turma]_[aula]_[capítulo]
    ERRATA = "errata"                      # /errata_[turma]_[seção]_[correção]
    ACTIVATE_CHAPTERS = "activate_chapters"  # /ativar_capitulos_[turma]_[1,3,5]
    ANSWER_KEY = "answer_key"              # /gabarito_[turma]_[cap3_ex2]_[resposta]
    DIFFICULTY_MAP = "difficulty_map"      # /mapa_dificuldade_[turma]
    COMPLETION_FUNNEL = "completion_funnel"  # /funil_conclusao_[turma]
    DOUBTS_CORPUS = "doubts_corpus"        # /corpus_duvidas_[turma]
    # ── Addon de Livros — comandos do ALUNO (jornada de leitura) ────────────
    PAUSE_READING = "pause_reading"        # /pausar_leitura
    RESUME_READING = "resume_reading"      # /retomar_leitura
    MY_PROGRESS = "my_progress"            # /meu_progresso
    FLASHCARDS = "flashcards"              # /flashcards
    PLACEMENT = "placement"                # /nivelamento (F5 — idiomas)
    # ── Horizontes H1-H3 ─────────────────────────────────────────────────────
    CLASS_CHALLENGE = "class_challenge"    # /desafio_[turma]               (H1.1)
    KEY_STATS = "key_stats"                # /chaves_[turma]                (H2.1)
    RUBRIC = "rubric"                      # /rubrica_[turma]_[critérios]   (H2.2)
    CREATE_CLASS = "create_class"          # /criar_turma_[curso]_[turma]   (H3.1)
    ADD_STUDENTS = "add_students"          # /adicionar_alunos_[turma]_[lista] (H3.1)
    LEAVE_BOOK = "leave_book"              # /sair_livro (aluno, LGPD)      (H2.1)
    CORRECT_ESSAY = "correct_essay"        # /corrigir (aluno)              (H2.2)
    DELETE_MY_DATA = "delete_my_data"      # /apagar_meus_dados (aluno, LGPD Art. 18)
    UNKNOWN = "unknown"

# Maps human-readable inputs (with aliases and typos) to CommandTypes
COMMAND_ALIASES = {
    CommandType.SYNC_STUDENTS: [
        "/sinc_alunos", "/sync_alunos", "/sincronizar_alunos", "/atualizar_alunos"
    ],
    CommandType.SYNC_MATERIALS: [
        "/sinc_materiais", "/sync_materiais", "/sincronizar_materiais", "/atualizar_materiais",
        "/sinc_material", "/sync_material"
    ],
    CommandType.HELP: [
        "/ajuda", "/help", "/comandos"
    ],
    CommandType.LIST_COURSES: [
        "/listar_cursos", "/list_cursos", "/cursos"
    ],
    CommandType.LIST_MATERIALS: [
        "/listar_materiais", "/list_materiais", "/materiais"
    ],
    CommandType.UPDATE_LESSON: [
        "/aula"
    ],
    CommandType.WAKEUP: [
        "/wakeup"
    ],
    CommandType.SEND_QUIZ: [
        "/quiz"
    ]
}

# ==========================================
# COMANDOS GERENCIAIS — /comando_[turma]_[arg]
# ==========================================
# Regex pré-compilado e SEM I/O: é a mesma fonte da verdade usada pelo webhook
# (rotear em < 200ms, SLA da Meta) e pelo command_worker (despachar handlers).
#
# Sintaxe:
#   /engajamento_[turma]            /acertos_[turma]         /risco_[turma]
#   /duvidas_frequentes_[turma]     /limpar_quizzes_[turma]
#   /ver_aluno_[telefone]           /resetar_aluno_[telefone]
#   /aviso_[turma]_[mensagem livre — pode conter espaços, _ e quebras de linha]
#
# Observações:
#   - DOTALL: a mensagem do /aviso pode ter múltiplas linhas.
#   - IGNORECASE: /Engajamento_T1 também funciona.
#   - No /aviso, a [turma] é o trecho entre o 1º e o 2º "_" (sem underscores);
#     todo o restante é a mensagem (que PODE conter underscores).
MANAGEMENT_COMMAND_REGEX = re.compile(
    r"^/(?P<cmd>engajamento|acertos|risco|duvidas_frequentes|ver_aluno|"
    r"resetar_aluno|limpar_quizzes|aviso|"
    r"plano_leitura|simulado|vincular_aula|errata|ativar_capitulos|gabarito|"
    r"mapa_dificuldade|funil_conclusao|corpus_duvidas|"
    r"desafio|chaves|rubrica|criar_turma|adicionar_alunos|delete)"
    r"_(?P<arg>.+)$",
    re.IGNORECASE | re.DOTALL,
)

# Divide o argumento dos comandos multi-arg em (turma, resto). A turma não pode
# conter "_" (delimitador), mas pode conter espaços: "/aviso_Turma A_Olá!" é
# válido. O "resto" PODE conter underscores (mensagem do /aviso, "cap3_ex2" do
# /gabarito, "2_3" do /vincular_aula...) — cada handler faz o split fino.
_AVISO_SPLIT_REGEX = re.compile(r"^(?P<turma>[^_]+)_(?P<msg>.+)$", re.DOTALL)

_MANAGEMENT_MAP = {
    "engajamento": CommandType.ENGAGEMENT,
    "acertos": CommandType.QUIZ_PERFORMANCE,
    "risco": CommandType.AT_RISK,
    "duvidas_frequentes": CommandType.FREQUENT_DOUBTS,
    "ver_aluno": CommandType.VIEW_STUDENT,
    "resetar_aluno": CommandType.RESET_STUDENT,
    "limpar_quizzes": CommandType.CLEAR_QUIZ_BANK,
    "delete": CommandType.DELETE_MATERIAL,
    "aviso": CommandType.BROADCAST_NOTICE,
    # Addon de Livros
    "plano_leitura": CommandType.READING_PLAN,
    "simulado": CommandType.SIMULADO,
    "vincular_aula": CommandType.LINK_LESSON_CHAPTER,
    "errata": CommandType.ERRATA,
    "ativar_capitulos": CommandType.ACTIVATE_CHAPTERS,
    "gabarito": CommandType.ANSWER_KEY,
    "mapa_dificuldade": CommandType.DIFFICULTY_MAP,
    "funil_conclusao": CommandType.COMPLETION_FUNNEL,
    "corpus_duvidas": CommandType.DOUBTS_CORPUS,
    # Horizontes H1-H3
    "desafio": CommandType.CLASS_CHALLENGE,
    "chaves": CommandType.KEY_STATS,
    "rubrica": CommandType.RUBRIC,
    "criar_turma": CommandType.CREATE_CLASS,
    "adicionar_alunos": CommandType.ADD_STUDENTS,
}

# Comandos cujo argumento é (turma + arg extra): o split de turma é o mesmo do
# /aviso. Os demais comandos gerenciais têm arg único (turma ou telefone).
_MULTIARG_COMMANDS = {
    CommandType.BROADCAST_NOTICE,
    CommandType.READING_PLAN,
    CommandType.SIMULADO,
    CommandType.LINK_LESSON_CHAPTER,
    CommandType.ERRATA,
    CommandType.ACTIVATE_CHAPTERS,
    CommandType.ANSWER_KEY,
    CommandType.RUBRIC,
    CommandType.CREATE_CLASS,
    CommandType.ADD_STUDENTS,
}

# ==========================================
# COMANDOS DO ALUNO — Addon de Livros
# ==========================================
# Allowlist EXPLÍCITA e pré-compilada (sem I/O): o webhook usa este regex para
# rotear comandos de ALUNOS para a fila `command_messages` dentro do SLA de
# 200ms. Qualquer outro comando de aluno continua rejeitado na borda.
STUDENT_COMMAND_REGEX = re.compile(
    r"^/(?P<cmd>pausar_leitura|retomar_leitura|meu_progresso|flashcards|nivelamento|sair_livro|corrigir|apagar_meus_dados)\s*$",
    re.IGNORECASE,
)

_STUDENT_MAP = {
    "pausar_leitura": CommandType.PAUSE_READING,
    "retomar_leitura": CommandType.RESUME_READING,
    "meu_progresso": CommandType.MY_PROGRESS,
    "flashcards": CommandType.FLASHCARDS,
    "nivelamento": CommandType.PLACEMENT,
    "sair_livro": CommandType.LEAVE_BOOK,
    "corrigir": CommandType.CORRECT_ESSAY,
    "apagar_meus_dados": CommandType.DELETE_MY_DATA,
}

# LGPD P1.1 (Art. 14): o RESPONSÁVEL não é usuário do bot — a palavra REVOGAR
# de remetente desconhecido é roteada para o worker de consentimento (que
# valida o telefone contra guardian_phone). Regex pré-compilado, sem I/O.
GUARDIAN_REVOKE_REGEX = re.compile(r"^\s*revogar\s*$", re.IGNORECASE)

# ==========================================
# H2.1 — ATIVAÇÃO POR QR CODE
# ==========================================
# O QR do material abre o WhatsApp com a chave pré-preenchida
# (wa.me/<numero>?text=...). O webhook usa este regex pré-compilado (SLA
# 200ms, sem I/O) para rotear a mensagem — de remetente CONHECIDO ou NÃO —
# para o fluxo de ativação. Alfabeto Crockford (sem 0/O/1/I, anti-confusão).
ACTIVATION_CODE_REGEX = re.compile(
    r"\b((?:LIVRO|PROF)-[A-Z2-9]{4}-[A-Z2-9]{4})\b",
    re.IGNORECASE,
)


def extract_activation_code(text: str) -> str | None:
    """Extrai a chave de ativação normalizada (maiúscula) do texto, se houver."""
    match = ACTIVATION_CODE_REGEX.search(text or "")
    return match.group(1).upper() if match else None


def parse_student_command(text: str) -> str | None:
    """Interpreta um comando de aluno (jornada de leitura). Retorna o
    CommandType ou None se o texto não for um comando de aluno conhecido."""
    match = STUDENT_COMMAND_REGEX.match(text.strip())
    if not match:
        return None
    return _STUDENT_MAP[match.group("cmd").lower()]


def parse_management_command(text: str) -> tuple[str, str, str] | None:
    """Interpreta um comando gerencial `/comando_[turma]_[arg]`.

    Returns:
        (CommandType, alvo, extra) onde:
          - alvo  = turma (ou telefone, nos comandos de aluno);
          - extra = mensagem (apenas no /aviso; "" nos demais).
        None se o texto não for um comando gerencial.
    """
    match = MANAGEMENT_COMMAND_REGEX.match(text.strip())
    if not match:
        return None

    cmd_key = match.group("cmd").lower()
    arg = match.group("arg").strip()
    cmd_type = _MANAGEMENT_MAP[cmd_key]

    if cmd_type in _MULTIARG_COMMANDS:
        split = _AVISO_SPLIT_REGEX.match(arg)
        if not split:
            # Sem o 2º "_": o worker devolve a orientação de formato.
            return (cmd_type, arg, "")
        return (cmd_type, split.group("turma").strip(), split.group("msg").strip())

    return (cmd_type, arg, "")


def parse_command(text: str) -> tuple[str, str]:
    """
    Given a text input from WhatsApp, identifies which CommandType it represents.
    Returns (CommandType, arguments). Returns (CommandType.UNKNOWN, "") if not recognized.
    """
    text_lower = text.strip().lower()
    
    for cmd_type, aliases in COMMAND_ALIASES.items():
        for alias in aliases:
            if text_lower.startswith(alias):
                # Extract arguments after the alias
                args = text[len(alias):].strip()
                return cmd_type, args
                
    return CommandType.UNKNOWN, ""
