"""
Constants for Teacher Commands in WhatsApp.
Maps command aliases to internal system actions.
"""

class CommandType:
    SYNC_STUDENTS = "sync_students"
    SYNC_MATERIALS = "sync_materials"
    HELP = "help"
    LIST_COURSES = "list_courses"
    LIST_MATERIALS = "list_materials"
    UPDATE_LESSON = "update_lesson"
    WAKEUP = "wakeup"
    SEND_QUIZ = "send_quiz"
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
