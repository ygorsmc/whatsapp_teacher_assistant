import structlog
import logging
from typing import Any, Dict, Optional
import json

def setup_telemetry(log_level: str = "INFO"):
    """
    Configures structlog to output JSON logs.
    In production, this allows Application Insights to parse dimensions easily.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)
    
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer()
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
    
    logging.basicConfig(
        format="%(message)s",
        level=level,
    )

logger = structlog.get_logger()

def track_event(event_name: str, dimensions: Optional[Dict[str, Any]] = None):
    """
    Helper function to log structured events.
    Required dimensions usually include: correlation_id, teacher_id, student_id, course_id, operation.
    """
    if dimensions is None:
        dimensions = {}
    
    logger.info(event_name, **dimensions)
