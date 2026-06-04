import os
import json
import uuid
import httpx
import time
import argparse

# ==============================================================================
# Simulador E2E do Meta (WhatsApp)
# Permite testar o Webhook e Pipeline LLM sem depender do WhatsApp real
# ==============================================================================

# Defina a URL do seu Webhook aqui (Local ou Azure/GCP)
# Para testar no Azure após o deploy: https://webhook-app.whitebush-XXXX.brazilsouth.azurecontainerapps.io/v1/webhook
DEFAULT_WEBHOOK_URL = os.getenv("WEBHOOK_URL", "http://localhost:8000/v1/webhook")
VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "your-verify-token-here")

def generate_message_payload(phone_number: str, message_text: str) -> dict:
    """Gera um payload idêntico ao que a Meta Cloud API envia para o Webhook"""
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "1143914788804185", # ID da WABA
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "5511999999999",
                                "phone_number_id": "1143914788804185"
                            },
                            "contacts": [
                                {
                                    "profile": {
                                        "name": "Simulated Student"
                                    },
                                    "wa_id": phone_number
                                }
                            ],
                            "messages": [
                                {
                                    "from": phone_number,
                                    "id": f"wamid.{uuid.uuid4().hex[:15]}", # ID único realístico
                                    "timestamp": str(int(time.time())),
                                    "text": {
                                        "body": message_text
                                    },
                                    "type": "text"
                                }
                            ]
                        },
                        "field": "messages"
                    }
                ]
            }
        ]
    }

def simulate_message(url: str, phone: str, text: str):
    """Envia um request POST ao Webhook com a assinatura SHA256 correta (se exigida)"""
    payload = generate_message_payload(phone, text)
    
    # Obs: Se o webhook verificar o HMAC-SHA256 rigorosamente via X-Hub-Signature-256,
    # precisaríamos calcular o hash usando a META_APP_SECRET. 
    # Em ambiente de dev, o webhook costuma pular essa validação se a secret for 'mock'.
    
    print(f"🚀 Enviando mensagem simulada: '{text}' do número {phone}")
    print(f"📡 Destino: {url}")
    
    try:
        response = httpx.post(url, json=payload, timeout=10.0)
        print(f"✅ Resposta do Webhook: HTTP {response.status_code}")
        print(response.text)
    except Exception as e:
        print(f"❌ Erro ao conectar ao Webhook: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simula mensagens do WhatsApp para o Webhook")
    parser.add_argument("--url", type=str, default=DEFAULT_WEBHOOK_URL, help="URL do Webhook")
    parser.add_argument("--phone", type=str, default="5511999999999", help="Número de telefone do remetente (incluindo DDI)")
    parser.add_argument("--text", type=str, required=True, help="Texto da mensagem (ex: '/ajuda')")
    
    args = parser.parse_args()
    simulate_message(args.url, args.phone, args.text)
