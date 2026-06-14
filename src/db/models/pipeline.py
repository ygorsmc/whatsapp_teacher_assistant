from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Index, Integer
from sqlalchemy.sql import func
from src.db.database import Base

class FileSyncState(Base):
    __tablename__ = "file_sync_state"
    
    file_id = Column(String(255), primary_key=True)
    teacher_id = Column(String(255), ForeignKey("Teachers.id"), nullable=False)
    file_hash = Column(String(255))
    last_modified = Column(DateTime)
    status = Column(String(255)) # 'pendente' | 'processando' | 'concluído' | 'falhou'
    is_deleted = Column(Boolean, default=False)
    error_message = Column(String(500))
    last_synced = Column(DateTime)
    processing_started_at = Column(DateTime)
    attempts = Column(Integer, default=0)
    
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    __table_args__ = (
        Index('idx_sync_teacher_status', 'teacher_id', 'status'),
    )
