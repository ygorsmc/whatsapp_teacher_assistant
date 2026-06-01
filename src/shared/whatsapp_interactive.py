import os
import httpx
import asyncio
from src.shared.config import settings

# WhatsApp API Settings
WHATSAPP_API_URL = settings.WHATSAPP_API_URL
PHONE_NUMBER_ID = settings.PHONE_NUMBER_ID
ACCESS_TOKEN = settings.WHATSAPP_TOKEN

async def _send_whatsapp_request(message: dict) -> dict:
    """Envio centralizado para a WhatsApp Cloud API (Graph API)."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            url=f"{WHATSAPP_API_URL}/{PHONE_NUMBER_ID}/messages",
            headers={"Authorization": f"Bearer {ACCESS_TOKEN}"},
            json=message,
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()

async def send_reply_buttons(
    phone: str,
    body_text: str,
    buttons: list[dict],  # [{"id": "quiz_123_A", "title": "Opção A"}, ...] (máx 3)
    header: str | None = None,
    footer: str | None = None,
) -> dict:
    """Envia mensagem com até 3 Reply Buttons via WhatsApp Cloud API."""
    if len(buttons) > 3:
        raise ValueError("Reply Buttons suportam no máximo 3 opções")

    message = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body_text},
            "action": {
                "buttons": [
                    {
                        "type": "reply",
                        "reply": {"id": btn["id"], "title": btn["title"][:20]}  # título máx 20 chars
                    }
                    for btn in buttons
                ]
            }
        }
    }
    if header:
        message["interactive"]["header"] = {"type": "text", "text": header}
    if footer:
        message["interactive"]["footer"] = {"text": footer}

    return await _send_whatsapp_request(message)

async def send_list_message(
    phone: str,
    body_text: str,
    button_text: str,  # texto do botão que abre o menu (máx 20 chars)
    sections: list[dict],  # [{"title": "Seção", "rows": [{"id": "A", "title": "...", "description": "..."}]}]
    header: str | None = None,
    footer: str | None = None,
) -> dict:
    """Envia mensagem com menu de lista via WhatsApp Cloud API."""
    total_rows = sum(len(s.get("rows", [])) for s in sections)
    if total_rows > 10:
        raise ValueError("List Messages suportam no máximo 10 itens")

    message = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {"text": body_text},
            "action": {
                "button": button_text[:20],
                "sections": sections
            }
        }
    }
    if header:
        message["interactive"]["header"] = {"type": "text", "text": header}
    if footer:
        message["interactive"]["footer"] = {"text": footer}

    return await _send_whatsapp_request(message)

def _chunk_text(text: str, max_length: int = 4000) -> list[str]:
    """Divide um texto longo em pedaços seguros para envio no WhatsApp."""
    if len(text) <= max_length:
        return [text]
        
    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
            
        # Tenta quebrar pelo último parágrafo duplo
        split_idx = text.rfind('\n\n', 0, max_length)
        if split_idx == -1:
            # Tenta quebrar pela última linha
            split_idx = text.rfind('\n', 0, max_length)
            if split_idx == -1:
                # Tenta quebrar pelo último espaço
                split_idx = text.rfind(' ', 0, max_length)
                if split_idx == -1:
                    # Sem opção, corta duro
                    split_idx = max_length
                    
        chunks.append(text[:split_idx].strip())
        text = text[split_idx:].strip()
        
    return chunks

async def send_whatsapp_message(phone: str, text: str, reply_to_message_id: str | None = None) -> dict:
    """Envia uma mensagem de texto simples via WhatsApp Cloud API com suporte a chunking e citações."""
    chunks = _chunk_text(text, max_length=4000)
    
    last_response = {}
    for i, chunk in enumerate(chunks):
        if not chunk:
            continue
            
        message = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone,
            "type": "text",
            "text": {"body": chunk}
        }
        
        # Apenas a primeira mensagem do chunking deve citar a pergunta original
        if i == 0 and reply_to_message_id:
            message["context"] = {"message_id": reply_to_message_id}
            
        last_response = await _send_whatsapp_request(message)
        
        # Pausa de meio segundo para garantir a ordem sequencial de leitura no WhatsApp
        if i < len(chunks) - 1:
            await asyncio.sleep(0.5)
            
    return last_response

async def send_whatsapp_typing(phone: str) -> dict:
    """Placeholder para indicador de digitação."""
    # A Cloud API não suporta typing indicator direto (apenas mark as read)
    pass


async def send_template_message(
    phone: str,
    template_name: str,
    language: str = "pt_BR",
    parameters: list[str] | None = None,
) -> dict:
    """Envia um Message Template aprovado pelo Meta via Cloud API.

    Templates precisam ser criados e aprovados no Meta Business Manager.
    O campo template_name deve corresponder exatamente ao nome registrado.

    Utility Templates custam ~R$0,05 e abrem janela de 24h para interação gratuita.
    """
    body = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language},
        },
    }
    if parameters:
        body["template"]["components"] = [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": p} for p in parameters
                ],
            }
        ]

    return await _send_whatsapp_request(body)
