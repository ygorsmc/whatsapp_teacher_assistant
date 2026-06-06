from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.sql import func
from src.db.database import Base

class Course(Base):
    __tablename__ = "Courses"
    
    id = Column(String(255), primary_key=True)
    teacher_id = Column(String(255), ForeignKey("Teachers.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class CourseClass(Base):
    __tablename__ = "Classes"

    id = Column(String(255), primary_key=True)
    course_id = Column(String(255), ForeignKey("Courses.id"), nullable=False)
    # Dono da turma. A posse passou a ser por TURMA (não mais só por curso):
    # um mesmo curso pode ter turmas de professores diferentes. Preenchido pela
    # sync a partir da aba "Professores" (Curso + Turma). Nullable para tolerar
    # turmas legadas antes do backfill da migration.
    teacher_id = Column(String(255), ForeignKey("Teachers.id"), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    current_lesson_number = Column(Integer, default=1, nullable=False)
    
    start_date = Column(DateTime, nullable=True)  # Store as DateTime for simpler SQLite compat
    schedule_map = Column(Text, nullable=True)    # JSON string mapping weekday(0-6) -> "19:00"
    last_advanced_date = Column(DateTime, nullable=True) # To prevent double advancement on the same day
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class StudentEnrollment(Base):
    __tablename__ = "StudentEnrollment"
    
    enrollment_id = Column(String(255), primary_key=True)
    student_id = Column(String(255), ForeignKey("Students.id"), nullable=False)
    class_id = Column(String(255), ForeignKey("Classes.id"), nullable=False)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    __table_args__ = (
        UniqueConstraint('student_id', 'class_id', name='uq_student_class_enrollment'),
    )
