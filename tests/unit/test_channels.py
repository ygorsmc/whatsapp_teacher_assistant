import pytest
from src.channel.factory import get_channel
from src.channel.whatsapp import WhatsAppChannel
from src.channel.telegram import TelegramChannel
from src.shared.config import settings

def test_channel_factory_whatsapp(mocker):
    # Arrange
    import src.channel.factory as factory
    factory._channel_instance = None
    mocker.patch.object(settings, "CHANNEL_BACKEND", "whatsapp")
    
    # Act
    channel = get_channel()
    
    # Assert
    assert isinstance(channel, WhatsAppChannel)
    assert channel.get_user_query_column() == "phone"

def test_channel_factory_telegram(mocker):
    # Arrange
    import src.channel.factory as factory
    factory._channel_instance = None
    mocker.patch.object(settings, "CHANNEL_BACKEND", "telegram")
    
    # Act
    channel = get_channel()
    
    # Assert
    assert isinstance(channel, TelegramChannel)
    assert channel.get_user_query_column() == "telegram_chat_id"

def test_channel_factory_invalid(mocker):
    # Arrange
    import src.channel.factory as factory
    factory._channel_instance = None
    mocker.patch.object(settings, "CHANNEL_BACKEND", "discord")
    
    # Act & Assert
    with pytest.raises(ValueError, match="Canal não suportado: discord"):
        get_channel()

@pytest.mark.asyncio
async def test_whatsapp_parse_inbound():
    # Arrange
    channel = WhatsAppChannel()
    payload = {
        "entry": [{
            "changes": [{
                "value": {
                    "messages": [{
                        "from": "5511999999999",
                        "id": "wamid.123",
                        "type": "text",
                        "text": {"body": "Olá!"}
                    }]
                }
            }]
        }]
    }
    
    # Act
    inbound = channel.parse_inbound(payload)
    
    # Assert
    assert inbound is not None
    assert inbound.sender_id == "5511999999999"
    assert inbound.text == "Olá!"
    assert inbound.message_id == "wamid.123"

@pytest.mark.asyncio
async def test_telegram_parse_inbound():
    # Arrange
    channel = TelegramChannel()
    payload = {
        "update_id": 12345,
        "message": {
            "message_id": 678,
            "chat": {"id": 11223344},
            "text": "/start"
        }
    }
    
    # Act
    inbound = channel.parse_inbound(payload)
    
    # Assert
    assert inbound is not None
    assert inbound.sender_id == "11223344"
    assert inbound.text == "/start"
    assert inbound.message_id == "678"
