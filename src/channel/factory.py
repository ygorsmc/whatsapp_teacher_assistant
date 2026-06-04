from src.shared.config import settings
from src.channel.interfaces import IMessagingChannel

_channel_instance = None

def get_channel() -> IMessagingChannel:
    global _channel_instance
    if _channel_instance is None:
        if settings.CHANNEL_BACKEND.lower() == "telegram":
            from src.channel.telegram import TelegramChannel
            _channel_instance = TelegramChannel()
        elif settings.CHANNEL_BACKEND.lower() == "whatsapp":
            from src.channel.whatsapp import WhatsAppChannel
            _channel_instance = WhatsAppChannel()
        else:
            raise ValueError(f"Canal não suportado: {settings.CHANNEL_BACKEND}")
    return _channel_instance
