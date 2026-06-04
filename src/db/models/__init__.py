# Expondo todos os models para manter compatibilidade com importações antigas
# e para facilitar o registro no Base.metadata do Alembic

from .user import Teacher, Student
from .course import Course, CourseClass, StudentEnrollment
from .pipeline import FileSyncState
from .messaging import MessageTemplates, StudentSchedule, StudentInteractions
from .quiz import PendingQuizzes

__all__ = [
    "Teacher",
    "Student",
    "Course",
    "CourseClass",
    "StudentEnrollment",
    "FileSyncState",
    "MessageTemplates",
    "StudentSchedule",
    "StudentInteractions",
    "PendingQuizzes"
]
