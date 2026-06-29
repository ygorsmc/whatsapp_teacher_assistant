import pytest
from src.channel.factory import get_channel
from src.channel.whatsapp import WhatsAppChannel
from src.channel.telegram import TelegramChannel
from src.channel.markdown import to_whatsapp_markdown
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


# ── Adaptador de markdown CommonMark → WhatsApp ──────────────────────────────

def test_md_bold_double_to_single():
    assert to_whatsapp_markdown("isto é **negrito**") == "isto é *negrito*"
    assert to_whatsapp_markdown("isto é __negrito__") == "isto é *negrito*"


def test_md_heading_to_bold():
    assert to_whatsapp_markdown("# Título") == "*Título*"
    assert to_whatsapp_markdown("## Sub") == "*Sub*"


def test_md_link_to_text():
    assert to_whatsapp_markdown("[Google](http://g.com)") == "Google (http://g.com)"


def test_md_table_to_monospace():
    # Tabela vira bloco ``` (monospace) — sem separador, sem linha vazia.
    out = to_whatsapp_markdown("| A | B |\n|---|---|\n| 1 | 2 |")
    assert out == "```\nA | B\n1 | 2\n```"


def test_md_table_aligns_columns():
    src = "| Mês | Vendas |\n|---|---|\n| Jan | 120 |\n| Fevereiro | 9 |"
    out = to_whatsapp_markdown(src)
    assert out.startswith("```\n") and out.endswith("\n```")
    assert "Fevereiro | 9" in out                 # coluna 0 com largura de "Fevereiro"
    # Todas as linhas alinham o pipe na MESMA coluna (prova do alinhamento).
    rows = [ln for ln in out.strip("`").strip().split("\n") if "|" in ln]
    assert len({ln.index("|") for ln in rows}) == 1


def test_md_non_table_pipes_untouched():
    # Linha com `|` que NÃO é tabela (sem separador) fica intacta.
    assert to_whatsapp_markdown("opção a | opção b") == "opção a | opção b"


def test_md_preserves_existing_whatsapp_markup():
    # *x* e _y_ simples (dialeto WA / copy do projeto) NÃO podem ser tocados.
    src = "Olá *negrito* e _itálico_ e `código`"
    assert to_whatsapp_markdown(src) == src


def test_md_idempotent():
    once = to_whatsapp_markdown("**a**\n# T\nfim")
    assert to_whatsapp_markdown(once) == once


# ── Blindagem dos limites da Meta (botões/lista) ─────────────────────────────

@pytest.mark.asyncio
async def test_send_list_truncates_long_row(mocker):
    ch = WhatsAppChannel()
    captured = {}

    async def fake_send(msg):
        captured["msg"] = msg
        return {}

    mocker.patch.object(ch, "_send_request", side_effect=fake_send)
    await ch.send_list("123", "corpo", "Ver", [
        {"title": "S" * 40, "rows": [{"id": "r1", "title": "T" * 40, "description": "D" * 100}]}
    ])
    section = captured["msg"]["interactive"]["action"]["sections"][0]
    row = section["rows"][0]
    assert len(section["title"]) == 24
    assert len(row["title"]) == 24
    assert len(row["description"]) == 72


@pytest.mark.asyncio
async def test_send_buttons_overflows_long_body(mocker):
    ch = WhatsAppChannel()
    captured = {}
    sent_texts = []

    async def fake_send(msg):
        captured["msg"] = msg
        return {}

    async def fake_text(uid, txt, reply_to=None):
        sent_texts.append(txt)
        return {}

    mocker.patch.object(ch, "_send_request", side_effect=fake_send)
    mocker.patch.object(ch, "send_text", side_effect=fake_text)
    big = "x" * 1500
    await ch.send_buttons("123", big, [{"id": "b1", "title": "OK"}])
    assert sent_texts and len(sent_texts[0]) == 1500   # corpo completo foi como texto
    assert len(captured["msg"]["interactive"]["body"]["text"]) <= 1024


# ── Áudio: transcrição como TEXTO logo DEPOIS do áudio (não há caption nativo) ──

class _FakeUpload:
    def raise_for_status(self):
        pass

    def json(self):
        return {"id": "media123", "result": {}}


class _FakeHTTPClient:
    """Stub mínimo de httpx.AsyncClient para o upload de mídia (media/sendAudio)."""

    def __init__(self, calls):
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, *a, **k):
        self._calls.append("upload")
        return _FakeUpload()


@pytest.mark.asyncio
async def test_send_audio_caption_after_audio_whatsapp(mocker):
    ch = WhatsAppChannel()
    calls: list = []
    mocker.patch("src.channel.whatsapp.httpx.AsyncClient", lambda: _FakeHTTPClient(calls))

    async def fake_send(msg):
        calls.append(("audio", msg["type"]))
        return {"messages": [{"id": "wamid.audio"}]}

    async def fake_text(uid, txt, reply_to=None):
        calls.append(("text", txt, reply_to))
        return {}

    mocker.patch.object(ch, "_send_request", side_effect=fake_send)
    mocker.patch.object(ch, "send_text", side_effect=fake_text)

    await ch.send_audio("123", b"\x00\x01", caption="apple. I eat an apple.")

    # Ordem RÍGIDA: upload → áudio → transcrição CITANDO (reply_to) o áudio.
    assert calls == [
        "upload",
        ("audio", "audio"),
        ("text", "apple. I eat an apple.", "wamid.audio"),
    ]


@pytest.mark.asyncio
async def test_send_audio_without_caption_sends_no_text_whatsapp(mocker):
    ch = WhatsAppChannel()
    calls: list = []
    mocker.patch("src.channel.whatsapp.httpx.AsyncClient", lambda: _FakeHTTPClient(calls))

    async def fake_send(msg):
        calls.append(("audio", msg["type"]))
        return {}

    async def fake_text(uid, txt, reply_to=None):
        calls.append(("text", txt))
        return {}

    mocker.patch.object(ch, "_send_request", side_effect=fake_send)
    mocker.patch.object(ch, "send_text", side_effect=fake_text)

    await ch.send_audio("123", b"\x00\x01")

    assert not any(isinstance(c, tuple) and c[0] == "text" for c in calls)


@pytest.mark.asyncio
async def test_send_audio_caption_after_audio_telegram(mocker):
    # Paridade: o Telegram também manda a transcrição como TEXTO depois do áudio.
    ch = TelegramChannel()
    calls: list = []
    mocker.patch("src.channel.telegram.httpx.AsyncClient", lambda: _FakeHTTPClient(calls))

    async def fake_text(uid, txt, reply_to=None):
        calls.append(("text", txt))
        return {}

    mocker.patch.object(ch, "send_text", side_effect=fake_text)

    await ch.send_audio("123", b"\x00\x01", caption="bonjour")

    assert calls == ["upload", ("text", "bonjour")]
