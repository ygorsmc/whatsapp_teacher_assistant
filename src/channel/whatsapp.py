import httpx
import asyncio
import hmac
import hashlib
from typing import Any, Mapping
from fastapi import Response
from src.shared.config import settings
from src.channel.interfaces import IMessagingChannel, InboundMessage
from src.channel.markdown import to_whatsapp_markdown

class WhatsAppChannel(IMessagingChannel):
    """Implementação de canal para WhatsApp Cloud API."""

    channel_name = "whatsapp"
    # Corpo de mensagem interativa (botões/lista): limite RÍGIDO da Meta. Acima
    # disso a API rejeita com 400 — o excedente vai como send_text antes.
    INTERACTIVE_BODY_LIMIT = 1024

    def __init__(self):
        self.api_url = settings.WHATSAPP_API_URL
        self.phone_number_id = settings.PHONE_NUMBER_ID
        self.access_token = settings.WHATSAPP_TOKEN
        self.app_secret = settings.META_APP_SECRET
        self.verify_token = settings.META_VERIFY_TOKEN
        self.max_length = settings.MAX_WHATSAPP_MESSAGE_LENGTH

    def get_user_query_column(self) -> str:
        return "phone"

    async def _send_request(self, message: dict) -> dict:
        """Envio interno para a Graph API."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url=f"{self.api_url}/{self.phone_number_id}/messages",
                headers={"Authorization": f"Bearer {self.access_token}"},
                json=message,
                timeout=30.0,
            )
            response.raise_for_status()
            return response.json()

    def _chunk_text(self, text: str) -> list[str]:
        """Divide um texto longo em pedaços seguros para envio no WhatsApp."""
        if len(text) <= self.max_length:
            return [text]
            
        chunks = []
        while text:
            if len(text) <= self.max_length:
                chunks.append(text)
                break
                
            split_idx = text.rfind('\n\n', 0, self.max_length)
            if split_idx == -1:
                split_idx = text.rfind('\n', 0, self.max_length)
                if split_idx == -1:
                    split_idx = text.rfind(' ', 0, self.max_length)
                    if split_idx == -1:
                        split_idx = self.max_length
                        
            chunks.append(text[:split_idx].strip())
            text = text[split_idx:].strip()
            
        return chunks

    async def send_text(self, user_id: str, text: str, reply_to: str | None = None) -> dict:
        text = to_whatsapp_markdown(text)
        chunks = self._chunk_text(text)
        last_response = {}
        for i, chunk in enumerate(chunks):
            if not chunk:
                continue
                
            message = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": user_id,
                "type": "text",
                "text": {"body": chunk}
            }
            
            if i == 0 and reply_to:
                message["context"] = {"message_id": reply_to}
                
            last_response = await self._send_request(message)
            
            if i < len(chunks) - 1:
                await asyncio.sleep(0.5)
                
        return last_response

    async def send_buttons(self, user_id: str, body: str, buttons: list[dict], footer: str = "") -> dict:
        if len(buttons) > 3:
            raise ValueError("Reply Buttons suportam no máximo 3 opções no WhatsApp")

        body = to_whatsapp_markdown(body)
        if len(body) > self.INTERACTIVE_BODY_LIMIT:
            # Corpo > 1024 (a Meta rejeita): manda o conteúdo completo como texto
            # e deixa os botões com um prompt curto. Nada se perde.
            await self.send_text(user_id, body)
            body = "👇 Escolha uma opção:"

        message: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": user_id,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": body},
                "action": {
                    "buttons": [
                        {
                            "type": "reply",
                            "reply": {"id": btn["id"], "title": btn["title"][:20]}
                        }
                        for btn in buttons
                    ]
                }
            }
        }
        if footer:
            message["interactive"]["footer"] = {"text": footer}

        return await self._send_request(message)

    async def send_list(self, user_id: str, body: str, button_text: str, sections: list[dict], footer: str = "") -> dict:
        total_rows = sum(len(s.get("rows", [])) for s in sections)
        if total_rows > 10:
            raise ValueError("List Messages suportam no máximo 10 itens no WhatsApp")

        # Trunca aos limites RÍGIDOS da Meta (senão a API rejeita com 400):
        # título de seção/linha ≤ 24, descrição ≤ 72.
        safe_sections: list[dict[str, Any]] = []
        for s in sections:
            rows = []
            for r in s.get("rows", []):
                row: dict[str, Any] = {"id": r["id"], "title": (r.get("title") or "")[:24]}
                if r.get("description"):
                    row["description"] = r["description"][:72]
                rows.append(row)
            section: dict[str, Any] = {"rows": rows}
            if s.get("title"):
                section["title"] = s["title"][:24]
            safe_sections.append(section)

        body = to_whatsapp_markdown(body)
        if len(body) > self.INTERACTIVE_BODY_LIMIT:
            await self.send_text(user_id, body)
            body = "👇 Escolha uma opção:"

        message: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": user_id,
            "type": "interactive",
            "interactive": {
                "type": "list",
                "body": {"text": body},
                "action": {
                    "button": button_text[:20],
                    "sections": safe_sections
                }
            }
        }
        if footer:
            message["interactive"]["footer"] = {"text": footer}

        return await self._send_request(message)

    async def send_template(self, user_id: str, template_name: str, params: list[str]) -> dict:
        # Simplificado para os templates existentes que suportam array of strings
        # A lógica completa original com botões e languages extras pode ser expandida aqui se necessário
        body: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": user_id,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": settings.DEFAULT_LANGUAGE},
            },
        }
        
        components = []
        if params:
            components.append({
                "type": "body",
                "parameters": [{"type": "text", "text": p} for p in params]
            })
            body["template"]["components"] = components

        return await self._send_request(body)

    async def send_typing(self, user_id: str) -> None:
        # WhatsApp Cloud API não suporta indicador de typing direto.
        pass

    async def send_audio(self, user_id: str, audio_bytes: bytes, caption: str = "") -> dict:
        """Pílula de áudio (H1.3): upload do MP3 na Media API + envio.
        WhatsApp não suporta caption em áudio — se houver, vai em texto antes."""
        async with httpx.AsyncClient() as client:
            upload = await client.post(
                url=f"{self.api_url}/{self.phone_number_id}/media",
                headers={"Authorization": f"Bearer {self.access_token}"},
                data={"messaging_product": "whatsapp", "type": "audio/mpeg"},
                files={"file": ("pilula.mp3", audio_bytes, "audio/mpeg")},
                timeout=60.0,
            )
            upload.raise_for_status()
            media_id = upload.json()["id"]

        if caption:
            await self.send_text(user_id, caption)

        return await self._send_request({
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": user_id,
            "type": "audio",
            "audio": {"id": media_id},
        })

    async def send_image(self, user_id: str, image_bytes: bytes, caption: str = "") -> dict:
        """Fórmula renderizada (M6): upload do PNG na Media API + envio.
        Imagem suporta caption nativo (diferente do áudio)."""
        async with httpx.AsyncClient() as client:
            upload = await client.post(
                url=f"{self.api_url}/{self.phone_number_id}/media",
                headers={"Authorization": f"Bearer {self.access_token}"},
                data={"messaging_product": "whatsapp", "type": "image/png"},
                files={"file": ("formula.png", image_bytes, "image/png")},
                timeout=60.0,
            )
            upload.raise_for_status()
            media_id = upload.json()["id"]

        image_payload: dict = {"id": media_id}
        if caption:
            image_payload["caption"] = caption
        return await self._send_request({
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": user_id,
            "type": "image",
            "image": image_payload,
        })

    async def download_media(self, media_id: str) -> bytes:
        """Baixa uma mídia recebida (H2.2): GET /media_id → url assinada → bytes."""
        headers = {"Authorization": f"Bearer {self.access_token}"}
        async with httpx.AsyncClient() as client:
            meta = await client.get(f"{self.api_url}/{media_id}", headers=headers, timeout=30.0)
            meta.raise_for_status()
            url = meta.json()["url"]
            resp = await client.get(url, headers=headers, timeout=60.0)
            resp.raise_for_status()
            return resp.content

    def parse_inbound(self, raw_payload: dict) -> InboundMessage | None:
        try:
            entry = raw_payload.get("entry", [{}])[0]
            changes = entry.get("changes", [{}])[0]
            value = changes.get("value", {})
            
            if "messages" not in value:
                return None
                
            msg_data = value["messages"][0]
            phone = msg_data["from"]
            msg_type = msg_data["type"]
            msg_id = msg_data["id"]
            
            # Puxa o nome do perfil de quem enviou, se disponível
            sender_name = ""
            contacts = value.get("contacts", [])
            if contacts:
                sender_name = contacts[0].get("profile", {}).get("name", "")
            
            text = msg_data.get("text", {}).get("body", "") if msg_type == "text" else ""
            interactive_payload = None
            media_id = None

            # H2.2: foto (redação) — o caption vira o text; download no worker.
            if msg_type == "image":
                media_id = msg_data.get("image", {}).get("id")
                text = msg_data.get("image", {}).get("caption", "")

            # F3 (Idiomas): nota de voz / áudio — o media_id segue; STT no worker.
            # WhatsApp envia voice note como type "audio" (audio.voice=true).
            if msg_type == "audio":
                media_id = msg_data.get("audio", {}).get("id")

            if msg_type == "interactive":
                interactive = msg_data.get("interactive", {})
                if interactive.get("type") == "button_reply":
                    interactive_payload = {
                        "type": "button_reply",
                        "id": interactive["button_reply"]["id"],
                        "title": interactive["button_reply"]["title"],
                    }
                elif interactive.get("type") == "list_reply":
                    interactive_payload = {
                        "type": "list_reply",
                        "id": interactive["list_reply"]["id"],
                        "title": interactive["list_reply"]["title"],
                        "description": interactive["list_reply"].get("description", ""),
                    }

            return InboundMessage(
                sender_id=phone,
                text=text,
                message_id=msg_id,
                message_type=msg_type if msg_type in ["text", "interactive", "image", "audio"] else "unknown",
                interactive_payload=interactive_payload,
                sender_name=sender_name,
                media_id=media_id,
            )
            
        except (KeyError, IndexError):
            return None

    async def verify_signature(self, body: bytes, headers: Mapping[str, str]) -> bool:
        if settings.ENVIRONMENT == "local":
            return True
            
        signature = headers.get("X-Hub-Signature-256", "")
        if not signature:
            return False
            
        expected = "sha256=" + hmac.new(
            self.app_secret.encode('utf-8'), 
            body, 
            hashlib.sha256
        ).hexdigest()
        
        return hmac.compare_digest(signature, expected)

    def build_verification_response(self, query_params: Mapping[str, str]) -> Any:
        # Responde ao hub.challenge
        if query_params.get("hub.verify_token") != self.verify_token:
            return Response(content="Verification failed", status_code=403)
        return Response(content=query_params.get("hub.challenge"), media_type="text/plain")
