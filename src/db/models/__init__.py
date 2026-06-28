# Expondo todos os models para manter compatibilidade com importações antigas
# e para facilitar o registro no Base.metadata do Alembic

from .user import Teacher, Student, GuardianConsents, DataSubjectRequests
from .course import Course, Subject, CourseClass, StudentEnrollment
from .pipeline import FileSyncState
from .messaging import MessageTemplates, StudentSchedule, StudentInteractions
from .quiz import PendingQuizzes
from .quiz_bank import QuizBank
from .reading import (
    ReadingPlans,
    SpacedRepetitionQueue,
    AnswerKeys,
    LessonChapterLinks,
    Erratas,
)
from .engagement import ClassChallenges, SentinelAlerts, PronunciationAttempts
from .gamification import WeeklyEngagement, StudentBadges
from .activation import ActivationKeys, KeyActivations, Rubrics

__all__ = [
    "Teacher",
    "Student",
    "GuardianConsents",
    "DataSubjectRequests",
    "Course",
    "Subject",
    "CourseClass",
    "StudentEnrollment",
    "FileSyncState",
    "MessageTemplates",
    "StudentSchedule",
    "StudentInteractions",
    "PendingQuizzes",
    "QuizBank",
    "ReadingPlans",
    "SpacedRepetitionQueue",
    "AnswerKeys",
    "LessonChapterLinks",
    "Erratas",
    "ClassChallenges",
    "SentinelAlerts",
    "PronunciationAttempts",
    "WeeklyEngagement",
    "StudentBadges",
    "ActivationKeys",
    "KeyActivations",
    "Rubrics",
]
