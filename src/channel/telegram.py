import httpx
from typing import Any, Mapping
from fastapi import Response
from src.shared.config import settings
from src.shared.telemetry import logger
from src.channel.interfaces import IMessagingChannel, InboundMessage

class TelegramChannel(IMessagingChannel):
    """Implementação de canal para Telegram Bot API."""

    def __init__(self):
        self.bot_token = settings.TELEGRAM_BOT_TOKEN
        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"
        self.secret_token = settings.TELEGRAM_WEBHOOK_SECRET

    def get_user_query_column(self) -> str:
        return "telegram_chat_id"

    async def _send_request(self, endpoint: str, payload: dict) -> dict:
        """Envio genérico para a Telegram Bot API."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url=f"{self.api_url}/{endpoint}",
                json=payload,
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()

    async def send_text(self, user_id: str, text: str, reply_to: str | None = None) -> dict:
        # Ignoramos intencionalmente o parâmetro `reply_to` no Telegram.
        # Enviar `reply_to_message_id` faz com que o cliente do Telegram agrupe
        # as mensagens em "Threads/Respostas", o que confunde a UX num chat 1-a-1.
        payload = {
            "chat_id": user_id,
            "text": text,
            "parse_mode": "Markdown",  # Suporta *bold*, _italic_, etc.
        }

        try:
            return await self._send_request("sendMessage", payload)
        except httpx.HTTPStatusError as e:
            # O parser "Markdown" legado do Telegram exige entidades perfeitamente
            # balanceadas (*, _, `, [). Respostas livres do LLM ocasionalmente
            # contêm markdown malformado (ex.: um "_" solto), o que faz o Telegram
            # rejeitar com 400 "can't parse entities". Em vez de derrubar o pipeline
            # e devolver ERROR_TECHNICAL ao aluno, reenviamos como texto puro.
            if e.response.status_code == 400:
                logger.warning(
                    "telegram_markdown_fallback",
                    user_id=user_id,
                    detail=e.response.text[:300],
                )
                payload.pop("parse_mode", None)
                return await self._send_request("sendMessage", payload)
            raise

    async def send_buttons(self, user_id: str, body: str, buttons: list[dict], footer: str = "") -> dict:
        # Telegram InlineKeyboardMarkup
        full_text = body
        if footer:
            full_text += f"\n\n_{footer}_"

        inline_keyboard = []
        # No Telegram, podemos empilhar botões horizontalmente ou verticalmente.
        # Vamos colocar um botão por linha para garantir que textos longos caibam.
        for btn in buttons:
            inline_keyboard.append([{
                "text": btn["title"],
                "callback_data": btn["id"]  # Isso voltará no callback_query
            }])

        payload = {
            "chat_id": user_id,
            "text": full_text,
            "parse_mode": "Markdown",
            "reply_markup": {
                "inline_keyboard": inline_keyboard
            }
        }
        return await self._send_request("sendMessage", payload)

    async def send_list(self, user_id: str, body: str, button_text: str, sections: list[dict], footer: str = "") -> dict:
        # Telegram não tem "List Message", emulamos com botões inline verticais.
        full_text = f"{body}\n\n*( {button_text} )*"
        if footer:
            full_text += f"\n_{footer}_"

        inline_keyboard = []
        for section in sections:
            # Adiciona o título da seção como um texto na mensagem, se necessário,
            # mas para InlineKeyboard apenas podemos botar botões.
            for row in section.get("rows", []):
                # Telegram callback_data é limitado a 64 bytes. 
                # IDs curtos são recomendados.
                btn_text = row["title"]
                if "description" in row and row["description"]:
                    btn_text += f" - {row['description']}"
                
                inline_keyboard.append([{
                    "text": btn_text,
                    "callback_data": row["id"]
                }])

        payload = {
            "chat_id": user_id,
            "text": full_text,
            "parse_mode": "Markdown",
            "reply_markup": {
                "inline_keyboard": inline_keyboard
            }
        }
        return await self._send_request("sendMessage", payload)

    async def send_template(self, user_id: str, template_name: str, params: list[str]) -> dict:
        # O Telegram não usa templates pre-aprovados pagos. 
        # Podemos simplesmente enviar os parâmetros formatados como texto.
        # Na ausência do corpo original do template, enviamos os params concatenados.
        text = "\n".join(params) if params else f"Aviso de sistema: {template_name}"
        return await self.send_text(user_id, text)

    async def send_typing(self, user_id: str) -> None:
        payload = {
            "chat_id": user_id,
            "action": "typing"
        }
        await self._send_request("sendChatAction", payload)

    def parse_inbound(self, raw_payload: dict) -> InboundMessage | None:
        try:
            # Update pode ser Message ou CallbackQuery (botões)
            if "message" in raw_payload:
                msg = raw_payload["message"]
                chat_id = str(msg["chat"]["id"])
                msg_id = str(msg["message_id"])
                
                # Nome do usuário
                first_name = msg.get("from", {}).get("first_name", "")
                last_name = msg.get("from", {}).get("last_name", "")
                sender_name = f"{first_name} {last_name}".strip()

                text = msg.get("text", "")
                
                return InboundMessage(
                    sender_id=chat_id,
                    text=text,
                    message_id=msg_id,
                    message_type="text",
                    sender_name=sender_name
                )
                
            elif "callback_query" in raw_payload:
                # Quando o usuário clica num botão inline
                cb = raw_payload["callback_query"]
                chat_id = str(cb["message"]["chat"]["id"])
                msg_id = str(cb["message"]["message_id"]) # ID da msg que tinha os botões
                
                first_name = cb.get("from", {}).get("first_name", "")
                last_name = cb.get("from", {}).get("last_name", "")
                sender_name = f"{first_name} {last_name}".strip()

                # O botão clicado
                data = cb.get("data", "")
                
                interactive_payload = {
                    "type": "button_reply",
                    "id": data,
                    "title": data # No telegram só recebemos o data no callback, não o title
                }

                return InboundMessage(
                    sender_id=chat_id,
                    text="", # Texto fica vazio em interações de botão
                    message_id=msg_id,
                    message_type="interactive",
                    interactive_payload=interactive_payload,
                    sender_name=sender_name
                )
                
        except KeyError:
            return None
        return None

    async def verify_signature(self, body: bytes, headers: Mapping[str, str]) -> bool:
        if settings.ENVIRONMENT in ["development", "dev"]:
            return True
            
        # Telegram manda o secret token no header
        token = headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not token or not self.secret_token:
            return False
            
        return token == self.secret_token

    def build_verification_response(self, query_params: Mapping[str, str]) -> Any:
        # Telegram não faz challenge get, ele só manda os POSTs.
        return Response(status_code=200)
