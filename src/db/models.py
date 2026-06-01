from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Index, Text, UniqueConstraint, Integer
from sqlalchemy.sql import func
from src.db.database import Base

class Teacher(Base):
    __tablename__ = "Teachers"
    
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    subject = Column(String, nullable=False)
    phone = Column(String, unique=True)
    is_active = Column(Boolean, default=True)
    
    assistant_persona = Column(Text, nullable=False)
    out_of_scope_message = Column(Text, nullable=False)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class Course(Base):
    __tablename__ = "Courses"
    
    id = Column(String, primary_key=True)
    teacher_id = Column(String, ForeignKey("Teachers.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class Student(Base):
    __tablename__ = "Students"
    
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    phone = Column(String, unique=True, nullable=False)
    is_active = Column(Boolean, default=True)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class CourseClass(Base):
    __tablename__ = "Classes"
    
    id = Column(String, primary_key=True)
    course_id = Column(String, ForeignKey("Courses.id"), nullable=False)
    name = Column(String, nullable=False)
    current_lesson_number = Column(Integer, default=1, nullable=False)
    
    start_date = Column(DateTime, nullable=True)  # Store as DateTime for simpler SQLite compat
    schedule_map = Column(Text, nullable=True)    # JSON string mapping weekday(0-6) -> "19:00"
    last_advanced_date = Column(DateTime, nullable=True) # To prevent double advancement on the same day
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class StudentEnrollment(Base):
    __tablename__ = "StudentEnrollment"
    
    enrollment_id = Column(String, primary_key=True)
    student_id = Column(String, ForeignKey("Students.id"), nullable=False)
    class_id = Column(String, ForeignKey("Classes.id"), nullable=False)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    __table_args__ = (
        UniqueConstraint('student_id', 'class_id', name='uq_student_class_enrollment'),
    )

class FileSyncState(Base):
    __tablename__ = "file_sync_state"
    
    file_id = Column(String, primary_key=True)
    teacher_id = Column(String, ForeignKey("Teachers.id"), nullable=False)
    file_hash = Column(String)
    last_modified = Column(DateTime)
    status = Column(String) # 'pendente' | 'processando' | 'concluído' | 'falhou'
    is_deleted = Column(Boolean, default=False)
    error_message = Column(String(500))
    last_synced = Column(DateTime)
    processing_started_at = Column(DateTime)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    __table_args__ = (
        Index('idx_sync_teacher_status', 'teacher_id', 'status'),
    )

class PendingQuizzes(Base):
    __tablename__ = "PendingQuizzes"
    
    quiz_id = Column(String, primary_key=True)
    student_id = Column(String, ForeignKey("Students.id"), nullable=False)
    course_id = Column(String, ForeignKey("Courses.id"), nullable=False)
    lesson_id = Column(String, nullable=False)
    question_text = Column(Text, nullable=False)
    alternatives = Column(Text, nullable=False) # Store JSON string
    correct_answer = Column(String, nullable=False)
    explanation = Column(Text, nullable=False)
    student_answer = Column(String, nullable=True)
    is_correct = Column(Boolean, nullable=True)
    status = Column(String, default="pending") # 'pending', 'answered', 'expired'
    
    expires_at = Column(DateTime, nullable=False)
    answered_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index('idx_pending_quiz_student', 'student_id', 'status'),
    )

class MessageTemplates(Base):
    __tablename__ = "MessageTemplates"
    
    template_name = Column(String, primary_key=True)
    language = Column(String, nullable=False)
    category = Column(String, nullable=False) # UTILITY, MARKETING, AUTHENTICATION
    status = Column(String, default="pending") # 'approved', 'rejected', 'pending'
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class StudentSchedule(Base):
    __tablename__ = "StudentSchedule"
    
    schedule_id = Column(String, primary_key=True)
    student_id = Column(String, ForeignKey("Students.id"), nullable=False)
    course_id = Column(String, ForeignKey("Courses.id"), nullable=False)
    template_name = Column(String, ForeignKey("MessageTemplates.template_name"), nullable=False)
    preferred_hour = Column(String, nullable=False)  # Time slot in BRT: "07:00", "12:00", "18:00"
    content_type = Column(String, nullable=False, default="review")  # 'review' | 'quiz' | 'preview'
    next_trigger_date = Column(DateTime, nullable=False)
    last_sent_at = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)
    status = Column(String, default="scheduled")  # 'scheduled', 'sent', 'failed'
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('student_id', 'course_id', 'content_type', 'preferred_hour', name='uq_student_course_content_hour'),
        Index('idx_schedule_trigger', 'next_trigger_date', 'status'),
    )

