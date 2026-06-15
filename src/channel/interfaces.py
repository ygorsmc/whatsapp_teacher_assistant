from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Mapping

@dataclass
class InboundMessage:
    """Mensagem normalizada, independente do canal de origem."""
    sender_id: str          # phone (WhatsApp) ou chat_id (Telegram)
    text: str
    message_id: str
    message_type: str       # "text" | "interactive" | "image" | "command" | "unknown"
    interactive_payload: dict | None = None
    sender_name: str = ""
    # H2.2: id da mídia no provedor (imagem de redação). O download acontece
    # SEMPRE no worker (download_media), nunca no webhook (SLA 200ms).
    media_id: str | None = None


class IMessagingChannel(ABC):
    """Interface agnóstica para canais de mensageria (WhatsApp, Telegram, etc.)."""

    # Nome canônico do canal ('whatsapp' | 'telegram'). Usado no roteamento
    # dinâmico (Students.preferred_channel). Sobrescrito por cada implementação.
    channel_name: str = ""

    @abstractmethod
    def get_user_query_column(self) -> str:
        """
        Retorna a coluna do banco de dados usada para buscar o usuário neste canal.
        Ex: 'phone' para WhatsApp, 'telegram_chat_id' para Telegram.
        """
        pass

    @abstractmethod
    async def send_text(self, user_id: str, text: str, reply_to: str | None = None) -> dict:
        """Envia uma mensagem de texto simples."""
        pass

    @abstractmethod
    async def send_buttons(self, user_id: str, body: str, buttons: list[dict], footer: str = "") -> dict:
        """
        Envia uma mensagem com botões interativos (máx 3).
        O formato de `buttons` esperado: [{"id": "btn_id", "title": "Btn Title"}]
        """
        pass

    @abstractmethod
    async def send_list(self, user_id: str, body: str, button_text: str, sections: list[dict], footer: str = "") -> dict:
        """
        Envia uma mensagem de lista expansível.
        O formato de `sections` esperado: 
        [{"title": "Section", "rows": [{"id": "row_id", "title": "Title", "description": "Desc"}]}]
        """
        pass

    @abstractmethod
    async def send_template(self, user_id: str, template_name: str, params: list[str]) -> dict:
        """Envia um template pré-aprovado com parâmetros."""
        pass

    @abstractmethod
    async def send_typing(self, user_id: str) -> None:
        """Envia um indicador visual de 'digitando...'."""
        pass

    @abstractmethod
    async def send_audio(self, user_id: str, audio_bytes: bytes, caption: str = "") -> dict:
        """Envia um áudio (MP3) — Pílulas de Áudio (H1.3)."""
        pass

    @abstractmethod
    async def send_image(self, user_id: str, image_bytes: bytes, caption: str = "") -> dict:
        """Envia uma imagem (PNG) — ex.: fórmula matemática renderizada (M6).
        A imagem aceita caption nativo no WhatsApp/Telegram (diferente do áudio)."""
        pass

    @abstractmethod
    async def download_media(self, media_id: str) -> bytes:
        """Baixa uma mídia recebida (H2.2 — foto de redação)."""
        pass

    @abstractmethod
    def parse_inbound(self, raw_payload: dict) -> InboundMessage | None:
        """Converte o payload bruto do provedor em um objeto InboundMessage normalizado."""
        pass

    @abstractmethod
    async def verify_signature(self, body: bytes, headers: Mapping[str, str]) -> bool:
        """Verifica se a requisição de entrada é autêntica e veio do provedor."""
        pass

    @abstractmethod
    def build_verification_response(self, query_params: Mapping[str, str]) -> Any:
        """Responde ao desafio de verificação do provedor (ex: hub.challenge do Meta)."""
        pass
