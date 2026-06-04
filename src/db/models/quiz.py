from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Index, Text
from sqlalchemy.sql import func
from src.db.database import Base

class PendingQuizzes(Base):
    __tablename__ = "PendingQuizzes"
    
    quiz_id = Column(String(255), primary_key=True)
    student_id = Column(String(255), ForeignKey("Students.id"), nullable=False)
    course_id = Column(String(255), ForeignKey("Courses.id"), nullable=False)
    lesson_id = Column(String(255), nullable=False)
    question_text = Column(Text, nullable=False)
    alternatives = Column(Text, nullable=False) # Store JSON string
    correct_answer = Column(String(255), nullable=False)
    explanation = Column(Text, nullable=False)
    student_answer = Column(String(255), nullable=True)
    is_correct = Column(Boolean, nullable=True)
    status = Column(String(255), default="pending") # 'pending', 'answered', 'expired'
    
    expires_at = Column(DateTime, nullable=False)
    answered_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index('idx_pending_quiz_student', 'student_id', 'status'),
    )
