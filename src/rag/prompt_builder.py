from src.db.models.user import Teacher, Student

from src.shared.constants.prompts import SYSTEM_PROMPT_TEMPLATE

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
    
    return SYSTEM_PROMPT_TEMPLATE.format(
        teacher_name=teacher.name,
        teacher_subject=teacher.subject,
        teacher_persona=teacher.assistant_persona,
        student_name=student.name,
        current_lesson_number=current_lesson_id,
        current_lesson_label=lesson_label,
        course_name=course_name,
        subject_text=subject_text,
        out_of_scope=teacher.out_of_scope_message
    )
