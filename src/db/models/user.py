from sqlalchemy import Column, String, Boolean, DateTime, Text
from sqlalchemy.sql import func
from src.db.database import Base

class Teacher(Base):
    __tablename__ = "Teachers"
    
    id = Column(String(255), primary_key=True)
    name = Column(String(255), nullable=False)
    subject = Column(String(255), nullable=False)
    phone = Column(String(255), unique=True)
    is_active = Column(Boolean, default=True)
    
    assistant_persona = Column(Text, nullable=False)
    out_of_scope_message = Column(Text, nullable=False)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class Student(Base):
    __tablename__ = "Students"
    
    id = Column(String(255), primary_key=True)
    name = Column(String(255), nullable=False)
    phone = Column(String(255), unique=True, nullable=False)
    is_active = Column(Boolean, default=True)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
