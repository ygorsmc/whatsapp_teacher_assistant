import httpx
import asyncio
import sys
import uuid

async def simulate_message(text: str):
    url = "http://localhost:8000/v1/webhook"
    
    # Meta WhatsApp API Payload Structure
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "1234567890",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "1234567890",
                                "phone_number_id": "1234567890"
                            },
                            "contacts": [
                                {
                                    "profile": {
                                        "name": "Aluno Teste"
                                    },
                                    "wa_id": "5511999999999"
                                }
                            ],
                            "messages": [
                                {
                                    "from": "5511999999999",
                                    "id": f"wamid.{uuid.uuid4()}",
                                    "timestamp": "1609459200",
                                    "type": "text",
                                    "text": {
                                        "body": text
                                    }
                                }
                            ]
                        },
                        "field": "messages"
                    }
                ]
            }
        ]
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=payload, headers={"Content-Type": "application/json"})
            print(f"Status Code: {response.status_code}")
            print(f"Response: {response.text}")
            if response.status_code == 200:
                print("✅ Message successfully accepted by Webhook!")
            else:
                print("❌ Webhook rejected the message.")
        except Exception as e:
            print(f"❌ Connection error: {e}")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        text_message = sys.argv[1]
    else:
        text_message = "Olá professor, como funciona o curso?"
        
    asyncio.run(simulate_message(text_message))
