from src.shared.config import settings
from src.channel.interfaces import IMessagingChannel

# Cache de instâncias por tipo de canal. Permite que WhatsApp e Telegram
# coexistam no mesmo processo (necessário para o roteamento dinâmico do
# scheduler — "follow-the-user").
_channels: dict[str, IMessagingChannel] = {}


def get_channel(channel_type: str | None = None) -> IMessagingChannel:
    """Retorna a instância do canal.

    - `get_channel()` → usa o canal global (settings.CHANNEL_BACKEND).
    - `get_channel("telegram")` / `get_channel("whatsapp")` → canal explícito,
      usado para entregar a cada aluno no seu `preferred_channel`.
    """
    ct = (channel_type or settings.CHANNEL_BACKEND).lower()
    if ct not in _channels:
        if ct == "telegram":
            from src.channel.telegram import TelegramChannel
            _channels[ct] = TelegramChannel()
        elif ct == "whatsapp":
            from src.channel.whatsapp import WhatsAppChannel
            _channels[ct] = WhatsAppChannel()
        else:
            raise ValueError(f"Canal não suportado: {ct}")
    return _channels[ct]
