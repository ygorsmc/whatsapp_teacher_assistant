import structlog
import logging
from typing import Any, Dict, Optional

# LGPD P1.4: campos de contato (PII) que NUNCA devem ir em claro para o
# Application Insights (terceiro). Mascarados automaticamente em TODO log.
_PII_LOG_FIELDS = {"sender_id", "phone", "recipient_id", "guardian_phone", "telegram_chat_id", "addr"}


def _mask_value(value: Any) -> Any:
    """Mantém os 4 últimos dígitos: '5541999887766' → '…7766'."""
    if value is None:
        return value
    text = str(value)
    if len(text) <= 4:
        return "…" + text
    return "…" + text[-4:]


def mask_pii_processor(logger, method_name, event_dict):
    """Processor structlog: mascara campos de contato em qualquer log/contextvar."""
    for field in _PII_LOG_FIELDS:
        if field in event_dict and event_dict[field] is not None:
            event_dict[field] = _mask_value(event_dict[field])
    return event_dict


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
            mask_pii_processor,  # LGPD P1.4 — mascara PII antes de serializar
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
