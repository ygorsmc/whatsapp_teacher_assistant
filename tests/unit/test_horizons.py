"""
Testes unitários dos Horizontes H1-H3 (plano de diferenciação).

Testes PUROS (parsers, regex, helpers de cálculo): sem banco, Redis ou rede.
Rodar com:  pytest tests/unit/test_horizons.py -v
"""

from datetime import datetime, timedelta

import pytest

from src.shared.constants.commands import (
    CommandType,
    extract_activation_code,
    parse_management_command,
    parse_student_command,
)
from scripts.generate_activation_keys import (
    generate_key, generate_batch, build_wa_link, CROCKFORD_ALPHABET,
)
from src.workers.challenges import (
    MIN_COHORT_SIZE,
    percent_correct,
    percentile_below,
    build_challenge_result,
    build_simulado_result,
)
from src.workers.sentinel import (
    rule_consecutive_errors,
    rule_streak_broken,
    rule_gone_quiet,
)
from src.workers.spaced_repetition import adaptive_next_interval
from src.workers.reading_journey import streak_medal, best_streak_badge
from src.workers.essay import caption_triggers_essay
from src.rag.review_generator import choose_topic_adaptive
from src.shared.tts import prepare_tts_text


# ==============================================================================
# H2.1 — Chaves de ativação (geração + regex do webhook)
# ==============================================================================

def test_generate_key_formato():
    key = generate_key("LIVRO")
    assert key.startswith("LIVRO-")
    parts = key.split("-")
    assert len(parts) == 3 and len(parts[1]) == 4 and len(parts[2]) == 4
    # Alfabeto Crockford: nunca contém 0/O/1/I (anti-confusão de digitação)
    for ch in parts[1] + parts[2]:
        assert ch in CROCKFORD_ALPHABET


def test_generate_batch_unicidade():
    keys = generate_batch(200, "LIVRO")
    assert len(keys) == len(set(keys)) == 200


def test_chave_gerada_casa_com_o_regex_do_webhook():
    # Invariante crítico: TODA chave gerada precisa ser roteável pelo webhook.
    for key in generate_batch(50, "LIVRO") + generate_batch(50, "PROF"):
        assert extract_activation_code(f"Quero ativar: {key}") == key


@pytest.mark.parametrize("texto,esperado", [
    ("Quero ativar meu material: LIVRO-A2B3-C4D5", "LIVRO-A2B3-C4D5"),
    ("livro-a2b3-c4d5", "LIVRO-A2B3-C4D5"),           # case-insensitive, normaliza
    ("PROF-WXYZ-2345 sou professor", "PROF-WXYZ-2345"),
    ("oi, tudo bem?", None),
    ("LIVRO-A2B3", None),                              # incompleta
    ("LIVRO-A0B3-C4D5", None),                         # '0' fora do alfabeto
])
def test_extract_activation_code(texto, esperado):
    assert extract_activation_code(texto) == esperado


def test_wa_link():
    link = build_wa_link("5511999998888", "LIVRO-A2B3-C4D5")
    assert link.startswith("https://wa.me/5511999998888?text=")
    assert "LIVRO-A2B3-C4D5" in link


# ==============================================================================
# H1.1/H2.3 — Desafio de turma e simulado (placar anônimo + percentil)
# ==============================================================================

def test_percent_correct():
    assert percent_correct(8, 11) == 73
    assert percent_correct(0, 0) == 0


def test_percentile_below():
    assert percentile_below(4, [4, 2, 3, 5, 1]) == 75   # 3 dos 4 colegas abaixo
    assert percentile_below(1, [1, 2, 3]) == 0
    assert percentile_below(5, [5]) == 0                # sozinho: sem percentil


def test_placar_anonimo_respeitando_coorte_minima():
    # Coorte < 3 (LGPD): NUNCA divulga percentual do grupo.
    msg = build_challenge_result(True, 2, 2)
    assert "%" not in msg
    # Coorte >= 3: placar anônimo com percentual.
    msg = build_challenge_result(True, 8, 11)
    assert "73%" in msg and "você foi um deles" in msg
    msg = build_challenge_result(False, 8, 11)
    assert "73%" in msg and "errou" in msg.lower()


def test_simulado_resultado():
    msg = build_simulado_result(4, 5, [4, 2, 3, 5, 1])
    assert "4/5" in msg and "75%" in msg
    # Coorte pequena: sem percentil
    msg = build_simulado_result(4, 5, [4, 2])
    assert "4/5" in msg and "%* da turma" not in msg


def test_min_cohort_size_constante():
    assert MIN_COHORT_SIZE == 3


# ==============================================================================
# H1.2 — Regras da Sentinela Emocional
# ==============================================================================

NOW = datetime(2026, 6, 12, 15, 0)


def test_rule_consecutive_errors():
    # mais recente primeiro: 3 erros seguidos disparam
    assert rule_consecutive_errors([False, False, False, True]) is True
    assert rule_consecutive_errors([False, False, True, False]) is False
    assert rule_consecutive_errors([False, False]) is False  # menos que o mínimo


def test_rule_streak_broken():
    assert rule_streak_broken(9, NOW - timedelta(hours=72), NOW) is True
    assert rule_streak_broken(9, NOW - timedelta(hours=24), NOW) is False  # 24h não é quebra
    assert rule_streak_broken(3, NOW - timedelta(hours=72), NOW) is False  # streak irrelevante


def test_rule_gone_quiet():
    # Ativo (5 interações na janela anterior) e sumido há 6 dias → alerta
    assert rule_gone_quiet(NOW - timedelta(days=6), 5, NOW) is True
    # Sumido há 2 dias apenas → ainda não
    assert rule_gone_quiet(NOW - timedelta(days=2), 5, NOW) is False
    # Nunca foi ativo → não é "silêncio repentino"
    assert rule_gone_quiet(NOW - timedelta(days=6), 1, NOW) is False
    # Abandono antigo (20+ dias) → fora da janela de resgate
    assert rule_gone_quiet(NOW - timedelta(days=20), 5, NOW) is False


# ==============================================================================
# H1.4 — Medalhas de streak
# ==============================================================================

def test_streak_medal_somente_no_marco_exato():
    assert streak_medal(3) == "🥉"
    assert streak_medal(7) == "🥈"
    assert streak_medal(30) == "🥇"
    assert streak_medal(100) == "🏆"
    assert streak_medal(8) is None   # entre marcos: sem celebração repetida


def test_best_streak_badge():
    assert best_streak_badge(2) == ""
    assert best_streak_badge(8) == "🥈"
    assert best_streak_badge(150) == "🏆"


# ==============================================================================
# H3.2 — SM-2 adaptativo + priorização de tópicos fracos
# ==============================================================================

def test_adaptive_next_interval_ease_alto():
    # Quem domina espaça rápido: 3 → 8 → 20 → 30 → completed
    assert adaptive_next_interval(3, 2.5) == 8
    assert adaptive_next_interval(8, 2.5) == 20
    assert adaptive_next_interval(20, 2.5) == 30   # cap em 30
    assert adaptive_next_interval(30, 2.5) is None  # ciclo completo


def test_adaptive_next_interval_ease_baixo():
    # Quem patina revisa mais: cresce devagar mas SEMPRE avança (+1 mínimo)
    assert adaptive_next_interval(3, 1.3) == 4
    assert adaptive_next_interval(4, 1.3) == 5
    assert adaptive_next_interval(10, 1.3) == 13


def test_choose_topic_adaptive_prioriza_fracos():
    available = ["A", "B", "C"]
    # "B" tem 1/4 de acerto (25% < 70%) → é o único fraco, sempre escolhido
    stats = {"A": (3, 3), "B": (1, 4)}
    for _ in range(10):
        assert choose_topic_adaptive(available, stats) == "B"


def test_choose_topic_adaptive_sem_fracos_sorteia():
    available = ["A", "B"]
    stats = {"A": (3, 3), "B": (4, 4)}  # ambos dominados
    assert choose_topic_adaptive(available, stats) in available


# ==============================================================================
# H2.2 — gatilho de correção por legenda
# ==============================================================================

@pytest.mark.parametrize("caption,esperado", [
    ("corrigir por favor", True),
    ("minha REDAÇÃO", True),
    ("foto da paisagem", False),
    ("", False),
])
def test_caption_triggers_essay(caption, esperado):
    assert caption_triggers_essay(caption) is esperado


# ==============================================================================
# H1.3 — limpeza de texto para TTS
# ==============================================================================

def test_prepare_tts_text_remove_ruido():
    clean = prepare_tts_text("📖 *Olá!* Leia a _seção 3_ hoje. 🔥")
    assert "*" not in clean and "_" not in clean
    assert "📖" not in clean and "🔥" not in clean
    assert "Leia a seção 3 hoje." in clean


def test_prepare_tts_text_corta_em_fronteira_de_frase():
    long_text = ("Primeira frase completa. " * 60)
    clean = prepare_tts_text(long_text, max_chars=200)
    assert len(clean) <= 200
    assert clean.endswith(".")


# ==============================================================================
# Comandos novos — parsing (webhook/worker compartilham a fonte da verdade)
# ==============================================================================

@pytest.mark.parametrize("texto,cmd,target,extra", [
    ("/desafio_TurmaA", CommandType.CLASS_CHALLENGE, "TurmaA", ""),
    ("/chaves_TurmaA", CommandType.KEY_STATS, "TurmaA", ""),
    ("/rubrica_TurmaA_1. Tese (0-2); 2. Norma culta (0-8)", CommandType.RUBRIC,
     "TurmaA", "1. Tese (0-2); 2. Norma culta (0-8)"),
])
def test_novos_comandos_gerenciais(texto, cmd, target, extra):
    assert parse_management_command(texto) == (cmd, target, extra)


def test_novos_comandos_de_aluno():
    assert parse_student_command("/sair_livro") == CommandType.LEAVE_BOOK
    assert parse_student_command("/corrigir") == CommandType.CORRECT_ESSAY
    assert parse_student_command("/corrigir_agora") is None  # allowlist estrita


# ==============================================================================
# Correção de redação — Vertical de Idiomas (visão) + gate por curso de idioma
# ==============================================================================

def test_legenda_barra_corrigir_dispara_correcao():
    # O pedido do produto: mandar a FOTO com a legenda "/corrigir".
    assert caption_triggers_essay("/corrigir") is True
    assert caption_triggers_essay("/CORRIGIR") is True


def test_language_essay_prompt_placeholders():
    # Esquema: Mistral OCR lê a foto → o TEXTO extraído entra no prompt do
    # examinador (ESSAY_LANGUAGE_MODEL corrige texto, não imagem).
    from src.shared.constants.prompts import LANGUAGE_ESSAY_CORRECTION_PROMPT
    p = LANGUAGE_ESSAY_CORRECTION_PROMPT.format(
        target_language_name="inglês",
        feedback_language_name="português brasileiro",
        cefr_level="B1",
        essay_text="My essay text here.",
    )
    assert "examinador" in p           # persona de especialista
    assert "B1" in p and "inglês" in p
    assert "My essay text here." in p  # texto do OCR embutido
    assert "OCR" in p                  # instrução de tolerar ruído de leitura
    assert "{" not in p                # todos os placeholders resolvidos


def test_essay_por_padrao_somente_idiomas():
    # Default do produto: correção por foto habilitada só em turmas de idiomas;
    # o fluxo genérico H2.2 (OCR + rubrica) volta com ESSAY_ALL_COURSES_ENABLED.
    from src.shared.config import settings
    assert settings.ESSAY_ALL_COURSES_ENABLED is False
    assert settings.ESSAY_LANGUAGE_MODEL == "gemini-3.5-flash"


@pytest.mark.asyncio
async def test_ocr_preview_gateado_pela_flag(mocker):
    """DEBUG de teste: com ESSAY_OCR_PREVIEW_ENABLED o texto lido pelo OCR é
    mandado ao aluno antes da correção; com a flag OFF é no-op (produção)."""
    from src.workers import essay
    from src.shared.config import settings

    channel = mocker.Mock()
    channel.send_text = mocker.AsyncMock()

    # Flag OFF → nada é enviado
    mocker.patch.object(settings, "ESSAY_OCR_PREVIEW_ENABLED", False)
    await essay._maybe_preview_ocr(channel, "sender-1", "texto lido pelo OCR")
    channel.send_text.assert_not_called()

    # Flag ON → manda o texto do OCR ao aluno
    mocker.patch.object(settings, "ESSAY_OCR_PREVIEW_ENABLED", True)
    await essay._maybe_preview_ocr(channel, "sender-1", "texto lido pelo OCR")
    channel.send_text.assert_awaited_once()
    (_sid, body) = channel.send_text.await_args.args
    assert "texto lido pelo OCR" in body


@pytest.mark.asyncio
async def test_agenerate_vision_monta_mensagem_multimodal(mocker):
    """agenerate_vision anexa a imagem como data URI base64 num bloco image_url
    junto do prompt — o LangChain traduz para o formato nativo do provider."""
    from types import SimpleNamespace
    from src.llm.providers import GeminiLLMProvider

    provider = object.__new__(GeminiLLMProvider)  # sem __init__ (não exige API key)
    provider.llm = mocker.Mock()
    provider.llm.ainvoke = mocker.AsyncMock(
        return_value=SimpleNamespace(content="feedback ok", response_metadata={})
    )

    out = await provider.agenerate_vision("corrija esta redação", b"bytes-da-foto")

    assert out == "feedback ok"
    (messages,) = provider.llm.ainvoke.call_args.args
    parts = messages[0].content
    assert parts[0] == {"type": "text", "text": "corrija esta redação"}
    url = parts[1]["image_url"]["url"]
    assert url.startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_mistral_ocr_image_payload(mocker):
    """ocr_image manda a foto como image_url data-URI ao Mistral OCR e devolve
    o markdown puro — sem o enriquecimento Gemini do parse() de PDFs."""
    from types import SimpleNamespace
    from src.parsing.mistral_parser import MistralOCRParser

    parser = object.__new__(MistralOCRParser)  # sem __init__ (não exige API key)
    process = mocker.AsyncMock(return_value=SimpleNamespace(
        pages=[SimpleNamespace(markdown="Hello"), SimpleNamespace(markdown="World")]
    ))
    parser.mistral_client = SimpleNamespace(ocr=SimpleNamespace(process_async=process))

    out = await parser.ocr_image(b"bytes-da-foto")

    assert out == "Hello\n\nWorld"
    kwargs = process.call_args.kwargs
    assert kwargs["model"] == "mistral-ocr-latest"
    assert kwargs["document"]["type"] == "image_url"
    assert kwargs["document"]["image_url"].startswith("data:image/jpeg;base64,")
