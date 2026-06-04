from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.sql import func
from src.db.database import Base

class MessageTemplates(Base):
    __tablename__ = "MessageTemplates"
    
    template_name = Column(String(255), primary_key=True)
    language = Column(String(255), nullable=False)
    category = Column(String(255), nullable=False) # UTILITY, MARKETING, AUTHENTICATION
    status = Column(String(255), default="pending") # 'approved', 'rejected', 'pending'
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class StudentSchedule(Base):
    __tablename__ = "StudentSchedule"
    
    schedule_id = Column(String(255), primary_key=True)
    student_id = Column(String(255), ForeignKey("Students.id"), nullable=False)
    course_id = Column(String(255), ForeignKey("Courses.id"), nullable=False)
    template_name = Column(String(255), ForeignKey("MessageTemplates.template_name"), nullable=False)
    preferred_hour = Column(String(255), nullable=False)  # Time slot in Local TZ: "07:00", "12:00", "18:00"
    content_type = Column(String(255), nullable=False, default="review")  # 'review' | 'quiz' | 'preview'
    next_trigger_date = Column(DateTime, nullable=False)
    last_sent_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)
    status = Column(String(255), default="scheduled")  # 'scheduled', 'sent', 'failed'
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('student_id', 'course_id', 'content_type', 'preferred_hour', name='uq_student_course_content_hour'),
        Index('idx_schedule_trigger', 'next_trigger_date', 'status'),
    )

class StudentInteractions(Base):
    __tablename__ = "StudentInteractions"
    
    id = Column(String(255), primary_key=True)
    student_id = Column(String(255), ForeignKey("Students.id"), nullable=False)
    course_id = Column(String(255), ForeignKey("Courses.id"), nullable=True)
    interaction_type = Column(String(255), nullable=False) # 'message_received', 'quiz_answered', 'review_sent'
    is_correct = Column(Boolean, nullable=True)
    message_content = Column(Text, nullable=True)
    timestamp = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index('idx_interaction_student_type_time', 'student_id', 'interaction_type', 'timestamp'),
    )
