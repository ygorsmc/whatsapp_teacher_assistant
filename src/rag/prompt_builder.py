from typing import cast

from src.db.models.user import Teacher, Student

from src.shared.config import settings
from src.shared.constants.prompts import (
    SYSTEM_PROMPT_TEMPLATE,
    LANGUAGE_TUTOR_PERSONA,
    L1_SAFETY_DIRECTIVE,
    MATH_REASONING_DIRECTIVE,
    CHART_DIRECTIVE,
)
from src.shared.language import detect_language_course, language_name
from src.math.routing import detect_exatas_course
from src.shared.privacy import llm_safe_name

async def resolve_lesson_label(course_id: str, lesson_id: str) -> str:
    """
    Converts a stable lesson_id (e.g. 'les_04') to a readable ordinal position.
    In a real implementation, this would fetch from sequencia.json or database.
    """
    # Placeholder
    return f"Aula atual ({lesson_id})"

async def build_system_prompt(teacher: Teacher, student: Student, course_id: str, course_name: str, current_lesson_id: str, subject_name: str | None = None) -> str:
    """
    Builds the system prompt asynchronously.
    Anchors the current lesson to mitigate Recency Bias.
    """
    lesson_label = await resolve_lesson_label(course_id, current_lesson_id)
    subject_text = f" na disciplina '{subject_name}'" if subject_name else ""

    base_prompt = SYSTEM_PROMPT_TEMPLATE.format(
        teacher_name=teacher.name,
        teacher_subject=teacher.subject,
        teacher_persona=teacher.assistant_persona,
        # LGPD P0.1 (minimização): só o primeiro nome do aluno vai ao provedor
        # de IA — sobrenome/telefone ficam restritos ao banco local. (cast: a
        # coluna SQLAlchemy é str em runtime; sem plugin o mypy a vê como Column.)
        student_name=llm_safe_name(cast(str, student.name)),
        current_lesson_number=current_lesson_id,
        current_lesson_label=lesson_label,
        course_name=course_name,
        subject_text=subject_text,
        out_of_scope=teacher.out_of_scope_message
    )

    # Vertical de Idiomas (§1.4): único switch de comportamento do bot. SOMENTE
    # com a flag ligada E o curso detectado como de idioma (pelo NOME) anexamos o
    # persona de tutor. Flag off OU curso normal ⇒ prompt base intocado.
    if settings.LANGUAGE_MODE_ENABLED:
        spec = detect_language_course(course_name, default_target=settings.DEFAULT_TARGET_LANGUAGE)
        if spec is not None:
            # CEFR/L1 vêm dos defaults; o perfil por turma (F5) poderá sobrescrever.
            target_name = language_name(spec.target_language)
            native_name = language_name(settings.DEFAULT_NATIVE_LANGUAGE)
            base_prompt += LANGUAGE_TUTOR_PERSONA.format(
                target_language_name=target_name,
                cefr_level=settings.DEFAULT_CEFR_LEVEL,
                native_language_name=native_name,
            )
            # F7 — rede de segurança L1 + inteligência emocional (flag própria).
            if settings.L1_SAFETY_NET_ENABLED:
                base_prompt += L1_SAFETY_DIRECTIVE.format(
                    target_language_name=target_name,
                    native_language_name=native_name,
                )

    # Vertical de Exatas (M5): SOMENTE com a flag ligada E a disciplina detectada
    # como de exatas (pelo NOME/tag) anexa a diretiva de raciocínio matemático.
    # Flag off OU disciplina não-exatas ⇒ prompt base intocado (a vertical SÓ
    # anexa, nunca reescreve). Diretriz fixa: anexada sem .format (LaTeX literal).
    is_exatas = detect_exatas_course(subject_name or "") or detect_exatas_course(course_name)
    if settings.MATH_MODE_ENABLED and is_exatas:
        base_prompt += MATH_REASONING_DIRECTIVE

    # Vertical de Exatas (M7): gráfico de apoio. SOMENTE com a flag própria ligada
    # E disciplina de exatas anexa a diretiva que AUTORIZA o bot a emitir um bloco
    # ```grafico``` quando ajudar. Flag independente do M5 (entrega gráficos sem
    # exigir o modo de raciocínio). Diretriz fixa: anexada sem .format (JSON literal).
    if settings.CHART_GENERATION_ENABLED and is_exatas:
        base_prompt += CHART_DIRECTIVE

    return base_prompt
