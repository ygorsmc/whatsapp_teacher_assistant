"""
Testes unitários da Vertical de Idiomas — F0 (fundação).

Testes PUROS (DTOs, regex de detecção, parsing, seleção de provider com mock):
sem banco, Redis ou rede. Provam a invariante-mestra: flag off OU curso sem
marcador ⇒ comportamento idêntico ao atual.

Rodar com:  pytest tests/unit/test_idiomas.py -v
"""

from types import SimpleNamespace

import pytest

import src.speech.factory as sf
from src.shared.config import settings
from src.shared.language import (
    LanguageDelivery,
    LanguageSpec,
    classify_cefr,
    detect_language_course,
    language_delivery,
    language_name,
    level_to_cefr,
    normalize_level,
)
from src.workers.placement import parse_placement_answers, placement_enabled
from src.workers.roleplay import roleplay_enabled
from src.workers.exam_prep import exam_prep_enabled
from src.rag.query_router import detect_roleplay_intent, is_roleplay_exit
from src.rag.review_generator import EXAM_PREP_PERSPECTIVES
from src.shared.constants.prompts import EXAM_PREP_PROMPT
from src.workers.optout import optout_intent
from src.shared.privacy import messaging_optout_clause
from src.speech.factory import get_pronunciation, get_stt, get_tts
from src.speech.interfaces import PronunciationScore, Transcription, WordScore
from src.speech.pronunciation import AzurePronunciation, _parse_azure
from src.speech.stt import AzureSTT, WhisperOpenAISTT, to_locale
from src.speech.tts import AzureTTS, ElevenLabsTTS, default_voice_for
from src.shared.tts import prepare_tts_text
from src.shared.constants.prompts import (
    LISTENING_PROMPT_TEMPLATE,
    LANGUAGE_REVIEW_PROMPT_TEMPLATE,
    VOCAB_GENERATION_PROMPT,
)
from src.rag.review_generator import (
    LISTENING_PERSPECTIVES,
    VOCAB_PERSPECTIVES,
    class_language_context,
    listening_directives,
    synthesize_listening,
    _review_from_bank_row,
)
from src.workers.vocabulary import vocab_enabled
from src.workers.speaking import speaking_enabled, format_scores
from src.workers.proficiency_report import (
    pronunciation_trend,
    enough_cohort,
    process_monthly_proficiency_report,
)
from src.shared.constants.prompts import PRONUNCIATION_FEEDBACK_PROMPT
from src.channel.whatsapp import WhatsAppChannel
from src.channel.telegram import TelegramChannel


# ==============================================================================
# §1.4 — Detecção de curso de idioma PELO NOME (helper puro)
# ==============================================================================

def test_tag_explicita_e_autoritativa():
    spec = detect_language_course("Inglês Business [idioma:en]")
    assert spec == LanguageSpec(target_language="en", detected_via="tag")
    assert detect_language_course("Espanhol A2 [idioma:es]").target_language == "es"


def test_tag_vence_a_stoplist():
    # "História da Língua Inglesa" sozinho é bloqueado, mas a tag é autoritativa.
    spec = detect_language_course("História da Língua Inglesa [idioma:en]")
    assert spec is not None and spec.target_language == "en" and spec.detected_via == "tag"


def test_tag_aceita_idioma_fora_da_heuristica():
    # Coreano não está no mapa de palavras-chave, mas a tag é honrada.
    assert detect_language_course("Curso [idioma:ko]").target_language == "ko"


def test_tag_ambigua_cai_no_default():
    # Código inexistente em ISO 639-1 → ambíguo → usa o default fornecido.
    spec = detect_language_course("Curso [idioma:zz]", default_target="en")
    assert spec is not None and spec.target_language == "en" and spec.detected_via == "tag"
    # Sem default disponível → None.
    assert detect_language_course("Curso [idioma:zz]") is None


def test_heuristica_por_palavra_chave():
    assert detect_language_course("Inglês Business").target_language == "en"
    assert detect_language_course("Espanhol Intermediário").target_language == "es"
    assert detect_language_course("Curso de Francês").target_language == "fr"
    assert detect_language_course("English for Kids").detected_via == "heuristic"


def test_stoplist_anti_falso_positivo():
    # Cursos SOBRE a língua, sem tag, NÃO viram tutor de idioma.
    assert detect_language_course("História da Língua Inglesa") is None
    assert detect_language_course("Literatura Inglesa") is None
    assert detect_language_course("Linguística Aplicada") is None


def test_curso_normal_retorna_none():
    assert detect_language_course("Matemática") is None
    assert detect_language_course("Cálculo I") is None
    assert detect_language_course("") is None


def test_language_name():
    assert language_name("en") == "inglês"
    assert language_name("pt-BR") == "português"   # locale → base
    assert language_name("xx") == "xx"             # fallback = o próprio código


# ==============================================================================
# §1.1 — DTOs puros (sem I/O)
# ==============================================================================

def test_dtos_puros():
    t = Transcription(text="hello", language="en")
    assert t.text == "hello" and t.confidence is None
    ws = WordScore(word="hello", score=92.0, phonemes=["h", "ə"])
    ps = PronunciationScore(accuracy=90.0, fluency=85.0, completeness=100.0, words=[ws])
    assert ps.words[0].phonemes == ["h", "ə"]
    # `words` default não compartilha estado entre instâncias.
    assert PronunciationScore(1, 2, 3).words == []


def test_parse_azure_pronunciation_puro():
    payload = {
        "NBest": [{
            "PronunciationAssessment": {
                "AccuracyScore": 88.0, "FluencyScore": 75.0, "CompletenessScore": 100.0,
            },
            "Words": [
                {"Word": "hello", "PronunciationAssessment": {"AccuracyScore": 95.0},
                 "Phonemes": [{"Phoneme": "h"}, {"Phoneme": "ə"}]},
            ],
        }]
    }
    score = _parse_azure(payload)
    assert score.accuracy == 88.0 and score.completeness == 100.0
    assert score.words[0].word == "hello" and score.words[0].phonemes == ["h", "ə"]


def test_parse_azure_vazio_nao_quebra():
    score = _parse_azure({})
    assert score.accuracy == 0.0 and score.words == []


def test_to_locale():
    assert to_locale("en") == "en-US"
    assert to_locale("pt") == "pt-BR"
    assert to_locale("en-GB") == "en-GB"  # já é locale → pass-through


# ==============================================================================
# §1.1 — Seleção de provider por env (factory + fallback gracioso)
# ==============================================================================

def _reset_caches():
    sf._stt_cache.clear()
    sf._pron_cache.clear()
    sf._tts_cache.clear()


def test_factory_tts_selecao(mocker):
    _reset_caches()
    mocker.patch.object(settings, "AZURE_SPEECH_KEY", "k")
    mocker.patch.object(settings, "AZURE_SPEECH_REGION", "brazilsouth")
    mocker.patch.object(settings, "ELEVENLABS_API_KEY", "k")
    mocker.patch.object(settings, "TTS_PROVIDER", "azure")
    assert isinstance(get_tts(), AzureTTS)
    assert isinstance(get_tts("elevenlabs"), ElevenLabsTTS)


def test_factory_none_e_desconhecido_degradam(mocker):
    _reset_caches()
    mocker.patch.object(settings, "TTS_PROVIDER", "none")
    assert get_tts() is None              # none = sem áudio (segue só com texto)
    assert get_tts("inexistente") is None  # provider desconhecido → None (não quebra)
    mocker.patch.object(settings, "STT_PROVIDER", "none")
    assert get_stt() is None
    mocker.patch.object(settings, "PRONUNCIATION_PROVIDER", "none")
    assert get_pronunciation() is None


def test_factory_chave_ausente_degrada_para_none(mocker):
    _reset_caches()
    # Provider selecionado mas SEM credencial → None (fallback), nunca exceção.
    mocker.patch.object(settings, "AZURE_SPEECH_KEY", None)
    mocker.patch.object(settings, "AZURE_SPEECH_REGION", None)
    mocker.patch.object(settings, "TTS_PROVIDER", "azure")
    assert get_tts() is None


def test_factory_stt_pronuncia_selecao(mocker):
    _reset_caches()
    mocker.patch.object(settings, "AZURE_SPEECH_KEY", "k")
    mocker.patch.object(settings, "AZURE_SPEECH_REGION", "brazilsouth")
    mocker.patch.object(settings, "OPENAI_API_KEY", "k")
    mocker.patch.object(settings, "STT_PROVIDER", "azure")
    assert isinstance(get_stt(), AzureSTT)
    assert isinstance(get_stt("whisper_openai"), WhisperOpenAISTT)
    mocker.patch.object(settings, "PRONUNCIATION_PROVIDER", "azure")
    assert isinstance(get_pronunciation(), AzurePronunciation)


# ==============================================================================
# §1.2 — TTS retrocompatível (H1.3 intocado)
# ==============================================================================

def test_prepare_tts_text_reexport_estavel():
    # O caller H1.3 e os testes importam de src.shared.tts — deve continuar puro.
    out = prepare_tts_text("**Olá** 🌍 mundo")
    assert "*" not in out and "🌍" not in out and "Olá mundo" in out


# ==============================================================================
# §1.4 — Invariante-mestra no build_system_prompt (flag off = intocado)
# ==============================================================================

_TUTOR_MARKER = "MODO TUTOR DE IDIOMA"


def _fake_actors():
    teacher = SimpleNamespace(
        name="Ana", subject="Inglês", assistant_persona="amigável",
        out_of_scope_message="Fora do escopo.",
    )
    student = SimpleNamespace(name="João Silva")
    return teacher, student


async def _build(course_name: str):
    from src.rag.prompt_builder import build_system_prompt
    teacher, student = _fake_actors()
    return await build_system_prompt(teacher, student, "crs_x", course_name, "les_01")


@pytest.mark.asyncio
async def test_flag_off_nao_anexa_tutor(mocker):
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", False)
    prompt = await _build("Inglês [idioma:en]")
    assert _TUTOR_MARKER not in prompt  # flag off ⇒ prompt base intocado


@pytest.mark.asyncio
async def test_flag_off_prompt_byte_a_byte(mocker):
    """Invariante-mestra (§4.1): com a flag OFF, o prompt de um curso de idioma é
    byte a byte o PREFIXO do prompt com a flag ON — OFF não muta nada; a vertical
    SÓ anexa (nunca reescreve o prompt base)."""
    mocker.patch.object(settings, "L1_SAFETY_NET_ENABLED", False)
    mocker.patch.object(settings, "DEFAULT_TARGET_LANGUAGE", "en")

    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", False)
    prompt_off = await _build("Inglês [idioma:en]")
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", True)
    prompt_on = await _build("Inglês [idioma:en]")

    assert prompt_on.startswith(prompt_off)   # OFF = prefixo exato (byte a byte)
    assert prompt_on != prompt_off            # ON realmente anexa o persona
    assert _TUTOR_MARKER not in prompt_off    # OFF sem conteúdo de tutor


def test_review_audio_text_por_nivel():
    """S1-3: o texto narrado nunca sai na língua errada. Iniciante sem audio_script
    (tradução no alvo) ⇒ None (não narra o resumo pt-BR com voz do alvo)."""
    from src.shared.language import review_audio_text
    # Iniciante com audio_script (versão no alvo) → narra o script.
    assert review_audio_text("HELLO no alvo", "resumo pt-BR", True) == "HELLO no alvo"
    # Iniciante SEM audio_script → None (texto segue; áudio é suprimido).
    assert review_audio_text(None, "resumo pt-BR", True) is None
    assert review_audio_text("", "resumo pt-BR", True) is None
    # Avançado: o resumo já está no alvo → narra o resumo mesmo sem audio_script.
    assert review_audio_text(None, "summary no alvo", False) == "summary no alvo"
    assert review_audio_text("script", "summary", False) == "script"


@pytest.mark.asyncio
async def test_flag_on_curso_normal_nao_anexa_tutor(mocker):
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", True)
    assert _TUTOR_MARKER not in await _build("Matemática")
    # Curso SOBRE a língua (stoplist), sem tag, também não vira tutor.
    assert _TUTOR_MARKER not in await _build("História da Língua Inglesa")


@pytest.mark.asyncio
async def test_flag_on_curso_de_idioma_anexa_tutor(mocker):
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", True)
    mocker.patch.object(settings, "DEFAULT_TARGET_LANGUAGE", "en")
    mocker.patch.object(settings, "DEFAULT_CEFR_LEVEL", "A2")
    mocker.patch.object(settings, "DEFAULT_NATIVE_LANGUAGE", "pt-BR")
    prompt = await _build("Inglês [idioma:en]")
    assert _TUTOR_MARKER in prompt
    assert "inglês" in prompt and "A2" in prompt and "português" in prompt


# ==============================================================================
# F1 — Listening ("áudio do dia"): diretrizes, prompt, voz e síntese (fallback)
# ==============================================================================

def test_listening_perspectives_nao_vazio():
    assert len(LISTENING_PERSPECTIVES) >= 2


def test_listening_directives_curso_de_idioma(mocker):
    mocker.patch.object(settings, "DEFAULT_TARGET_LANGUAGE", "en")
    mocker.patch.object(settings, "DEFAULT_CEFR_LEVEL", "B1")
    mocker.patch.object(settings, "DEFAULT_NATIVE_LANGUAGE", "pt-BR")
    d = listening_directives("Espanhol A2 [idioma:es]")
    assert d["target_language"] == "es"
    assert d["target_language_name"] == "espanhol"
    assert d["cefr_level"] == "B1"
    assert d["native_language_name"] == "português"


def test_listening_directives_fallback_default(mocker):
    mocker.patch.object(settings, "DEFAULT_TARGET_LANGUAGE", "en")
    # Curso não-idioma → cai no DEFAULT_TARGET_LANGUAGE (o chamador só usa isto
    # quando já decidiu que é listening; o default garante robustez).
    assert listening_directives("Matemática")["target_language"] == "en"


def test_listening_prompt_formata_com_as_diretrizes():
    d = listening_directives("Inglês [idioma:en]")
    prompt = LISTENING_PROMPT_TEMPLATE.format(
        target_language_name=d["target_language_name"],
        cefr_level=d["cefr_level"],
        native_language_name=d["native_language_name"],
        student_name="Ana", course_name="Inglês", section_label="Módulo 1",
        topic="saudações", perspective="diálogo do cotidiano",
        lesson_content="hello, hi, good morning",
    )
    assert "inglês" in prompt and "hello" in prompt


def test_default_voice_for_idioma():
    assert default_voice_for("en") == "en-US-JennyNeural"
    assert default_voice_for("pt-BR") == "pt-BR-FranciscaNeural"   # locale → base
    assert default_voice_for("xx") == "en-US-JennyNeural"          # fallback


@pytest.mark.asyncio
async def test_synthesize_listening_flag_off(mocker):
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", False)
    mocker.patch.object(settings, "TTS_ENABLED", True)
    assert await synthesize_listening("hello", "en") is None  # vertical off → None


@pytest.mark.asyncio
async def test_synthesize_listening_sem_provider(mocker):
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", True)
    mocker.patch.object(settings, "TTS_ENABLED", True)
    mocker.patch("src.speech.factory.get_tts", return_value=None)
    assert await synthesize_listening("hello", "en") is None  # TTS none → texto


@pytest.mark.asyncio
async def test_synthesize_listening_erro_degrada(mocker):
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", True)
    mocker.patch.object(settings, "TTS_ENABLED", True)

    class BoomTTS:
        name = "boom"
        async def synthesize(self, text, voice, language):
            raise RuntimeError("provider down")

    mocker.patch("src.speech.factory.get_tts", return_value=BoomTTS())
    assert await synthesize_listening("hello", "en") is None  # erro → None (best-effort)


@pytest.mark.asyncio
async def test_synthesize_listening_sucesso_usa_voz_e_locale_do_alvo(mocker):
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", True)
    mocker.patch.object(settings, "TTS_ENABLED", True)

    calls: dict = {}

    class FakeTTS:
        name = "fake"
        async def synthesize(self, text, voice, language):
            calls.update(text=text, voice=voice, language=language)
            return b"AUDIO"

    mocker.patch("src.speech.factory.get_tts", return_value=FakeTTS())
    audio = await synthesize_listening("- A: hello\n- B: hi", "en")
    assert audio == b"AUDIO"
    assert calls["voice"] == "en-US-JennyNeural"  # voz da língua-alvo, não pt-BR
    assert calls["language"] == "en-US"           # locale derivado do alvo


# ==============================================================================
# Perfil de idioma por turma (planilha: Idioma + Nível) → língua do texto/áudio
# ==============================================================================

def test_normalize_level():
    assert normalize_level("Iniciante") == "Iniciante"
    assert normalize_level("intermediário") == "Intermediário"
    assert normalize_level("intermediario") == "Intermediário"  # sem acento
    assert normalize_level("AVANÇADO") == "Avançado"
    assert normalize_level("fluente") == "Fluente"
    assert normalize_level("-") is None
    assert normalize_level("") is None
    assert normalize_level(None) is None
    assert normalize_level("qualquer") is None


def test_level_to_cefr():
    assert level_to_cefr("Iniciante") == "A2"
    assert level_to_cefr("Intermediário") == "B1"
    assert level_to_cefr("Avançado") == "C1"
    assert level_to_cefr("Fluente") == "C2"
    assert level_to_cefr(None) == "A2"  # conservador


def test_language_delivery_nao_idioma():
    assert language_delivery(False, "Avançado", "en", "pt-BR") is None


def test_language_delivery_iniciante_texto_pt_audio_alvo():
    d = language_delivery(True, "Iniciante", "en", "pt-BR")
    assert d == LanguageDelivery(text_language="pt-BR", audio_language="en", cefr_level="A2", is_beginner=True)


def test_language_delivery_nivel_ausente_trata_como_iniciante():
    d = language_delivery(True, None, "es", "pt-BR")
    assert d.is_beginner is True and d.text_language == "pt-BR" and d.audio_language == "es"


def test_language_delivery_avancado_texto_e_audio_no_alvo():
    d = language_delivery(True, "Avançado", "en", "pt-BR")
    assert d.text_language == "en" and d.audio_language == "en"
    assert d.cefr_level == "C1" and d.is_beginner is False


def test_language_review_prompt_formata():
    prompt = LANGUAGE_REVIEW_PROMPT_TEMPLATE.format(
        target_language_name="inglês", text_language_name="português",
        cefr_level="A2", student_name="Ana", course_name="Inglês",
        lesson_label="Aula 1", perspective="detalhe técnico",
        lesson_content="present simple: I work, you work",
    )
    assert "inglês" in prompt and "português" in prompt and "present simple" in prompt


def test_class_language_context_flag_off(mocker):
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", False)
    assert class_language_context("Inglês [idioma:en]", True, "Avançado") == (None, None)


def test_class_language_context_nao_idioma(mocker):
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", True)
    assert class_language_context("Inglês [idioma:en]", False, "Avançado") == (None, None)


def test_class_language_context_iniciante(mocker):
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", True)
    mocker.patch.object(settings, "DEFAULT_TARGET_LANGUAGE", "en")
    mocker.patch.object(settings, "DEFAULT_NATIVE_LANGUAGE", "pt-BR")
    delivery, target = class_language_context("Inglês [idioma:en]", True, "Iniciante")
    assert target == "en"
    assert delivery is not None and delivery.is_beginner and delivery.text_language == "pt-BR"


def test_class_language_context_avancado(mocker):
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", True)
    mocker.patch.object(settings, "DEFAULT_TARGET_LANGUAGE", "en")
    mocker.patch.object(settings, "DEFAULT_NATIVE_LANGUAGE", "pt-BR")
    delivery, target = class_language_context("Espanhol [idioma:es]", True, "Avançado")
    assert target == "es"
    assert delivery is not None and not delivery.is_beginner and delivery.text_language == "es"


# ==============================================================================
# F2 — Vocabulário com SRS (flashcard palavra-alvo + áudio, cacheável no QuizBank)
# ==============================================================================

def test_vocab_perspectives_nao_vazio():
    assert len(VOCAB_PERSPECTIVES) >= 2


def test_vocab_enabled_exige_as_duas_flags(mocker):
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", True)
    mocker.patch.object(settings, "VOCABULARY_SRS_ENABLED", False)
    assert vocab_enabled() is False
    mocker.patch.object(settings, "VOCABULARY_SRS_ENABLED", True)
    assert vocab_enabled() is True
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", False)
    assert vocab_enabled() is False


def test_vocab_prompt_formata():
    prompt = VOCAB_GENERATION_PROMPT.format(
        target_language_name="inglês", native_language_name="português",
        cefr_level="A2", student_name="Ana", course_name="Inglês",
        lesson_label="Aula 1", topic="cumprimentos", perspective="substantivo central",
        lesson_content="apple = maçã; book = livro",
    )
    assert "inglês" in prompt and "português" in prompt and "apple" in prompt


def test_review_from_bank_row_hidrata_audio_script():
    # Cache Hit deve restaurar o roteiro de áudio (vocab/idioma) p/ refalar a palavra.
    row = {
        "id": "qb_1",
        "question_text": "O que significa **apple**?",
        "alternatives": '[{"id": "A", "text": "maçã"}, {"id": "B", "text": "casa"}, {"id": "C", "text": "carro"}]',
        "correct_answer": "A",
        "explanation": "apple = maçã",
        "key_points": '["apple pie (torta de maçã)"]',
        "summary": "apple — maçã",
        "topic": "frutas",
        "audio_script": "apple. I eat an apple every day.",
    }
    review = _review_from_bank_row(row)
    assert review is not None
    assert review.audio_script == "apple. I eat an apple every day."
    assert review.quiz_bank_id == "qb_1"


# ==============================================================================
# F3 — Speaking + Pronúncia (áudio → STT → pronúncia → feedback; áudio descartado)
# ==============================================================================

def test_speaking_enabled_exige_as_duas_flags(mocker):
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", True)
    mocker.patch.object(settings, "SPEECH_INPUT_ENABLED", False)
    assert speaking_enabled() is False
    mocker.patch.object(settings, "SPEECH_INPUT_ENABLED", True)
    assert speaking_enabled() is True
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", False)
    assert speaking_enabled() is False


def test_format_scores_sem_matriz():
    assert format_scores(None) == "indisponível"


def test_format_scores_com_matriz_destaca_palavras_fracas():
    score = PronunciationScore(
        accuracy=82.0, fluency=75.0, completeness=90.0,
        words=[
            WordScore(word="hello", score=95.0),
            WordScore(word="world", score=55.0),
            WordScore(word="thanks", score=40.0),
        ],
    )
    out = format_scores(score)
    assert "accuracy 82" in out and "fluency 75" in out and "completeness 90" in out
    # Só as fracas (<70), da pior para a menos pior; a forte (hello) fica de fora.
    assert "thanks (40)" in out and "world (55)" in out and "hello" not in out


def test_pronunciation_feedback_prompt_formata():
    prompt = PRONUNCIATION_FEEDBACK_PROMPT.format(
        target_language_name="inglês", native_language_name="português",
        cefr_level="A2", student_name="Ana",
        transcription="I go to school every day",
        scores="accuracy 80, fluency 70, completeness 90; palavras a melhorar: school (60)",
    )
    assert "inglês" in prompt and "I go to school" in prompt and "school (60)" in prompt


def test_whatsapp_parseia_audio_como_media():
    raw = {"entry": [{"changes": [{"value": {
        "contacts": [{"profile": {"name": "Ana"}}],
        "messages": [{"from": "5511999990000", "type": "audio", "id": "wamid.X",
                      "audio": {"id": "media_123", "voice": True}}],
    }}]}]}
    msg = WhatsAppChannel().parse_inbound(raw)
    assert msg is not None and msg.message_type == "audio" and msg.media_id == "media_123"


def test_telegram_parseia_voice_como_audio():
    raw = {"message": {"chat": {"id": 123}, "message_id": 7,
                       "from": {"first_name": "Ana"}, "voice": {"file_id": "voice_123"}}}
    msg = TelegramChannel().parse_inbound(raw)
    assert msg is not None and msg.message_type == "audio" and msg.media_id == "voice_123"


# ==============================================================================
# F6 — Relatório de proficiência (inventário + agregado, coorte >= 3)
# ==============================================================================

def test_pronunciation_trend():
    assert pronunciation_trend(60.0, 80.0) == "📈 evoluindo"
    assert pronunciation_trend(80.0, 60.0) == "📉 atenção"
    assert pronunciation_trend(70.0, 72.0) == "➡️ estável"   # dentro do threshold
    assert pronunciation_trend(None, None) == "➡️ estável"   # sem dados


def test_enough_cohort():
    assert enough_cohort(3) is True
    assert enough_cohort(2) is False
    assert enough_cohort(0) is False


@pytest.mark.asyncio
async def test_proficiency_report_noop_quando_flag_off(mocker):
    # Flags off ⇒ retorna sem tocar banco/fila (gate da vertical + submodo).
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", True)
    mocker.patch.object(settings, "PROFICIENCY_REPORT_ENABLED", False)
    assert await process_monthly_proficiency_report() is None


# ==============================================================================
# F5 — Nivelamento CEFR (classify_cefr + parse de respostas + gate)
# ==============================================================================

def test_classify_cefr_escada_consecutiva():
    assert classify_cefr([True] * 6) == "C2"                       # acertou tudo
    assert classify_cefr([True, True, True, False, False, False]) == "B1"  # até B1
    assert classify_cefr([False] * 6) == "A1"                      # piso
    assert classify_cefr([True, False, True, True, True, True]) == "A1"   # 1ª falha encerra
    assert classify_cefr([True, True, False, True, False, False]) == "A2"


def test_parse_placement_answers_formatos():
    assert parse_placement_answers("1A 2B 3C 4A 5B 6C") == ["A", "B", "C", "A", "B", "C"]
    assert parse_placement_answers("1) a 2) b 3) c 4) a 5) b 6) c") == ["A", "B", "C", "A", "B", "C"]
    assert parse_placement_answers("A B C A B C") == ["A", "B", "C", "A", "B", "C"]
    assert parse_placement_answers("ABCABC") == ["A", "B", "C", "A", "B", "C"]


def test_parse_placement_answers_parcial():
    # Numeração esparsa → só as posições informadas preenchidas.
    assert parse_placement_answers("1A 3C") == ["A", None, "C", None, None, None]


def test_placement_enabled_exige_as_duas_flags(mocker):
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", True)
    mocker.patch.object(settings, "PLACEMENT_TEST_ENABLED", False)
    assert placement_enabled() is False
    mocker.patch.object(settings, "PLACEMENT_TEST_ENABLED", True)
    assert placement_enabled() is True
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", False)
    assert placement_enabled() is False


# ==============================================================================
# F7 — Rede de segurança L1 + inteligência emocional (diretiva no System Prompt)
# ==============================================================================

_L1_MARKER = "REDE DE SEGURANÇA EMOCIONAL"


@pytest.mark.asyncio
async def test_f7_anexa_diretiva_l1_quando_flag_on(mocker):
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", True)
    mocker.patch.object(settings, "L1_SAFETY_NET_ENABLED", True)
    mocker.patch.object(settings, "DEFAULT_NATIVE_LANGUAGE", "pt-BR")
    prompt = await _build("Inglês [idioma:en]")
    assert _L1_MARKER in prompt and "português" in prompt


@pytest.mark.asyncio
async def test_f7_off_nao_anexa(mocker):
    # Flag F7 off: tutor de idioma entra, mas a diretiva L1 (própria flag) não.
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", True)
    mocker.patch.object(settings, "L1_SAFETY_NET_ENABLED", False)
    assert _L1_MARKER not in await _build("Inglês [idioma:en]")


@pytest.mark.asyncio
async def test_f7_curso_normal_nao_anexa(mocker):
    # Curso não-idioma: nem tutor nem diretiva L1, mesmo com a flag F7 on.
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", True)
    mocker.patch.object(settings, "L1_SAFETY_NET_ENABLED", True)
    assert _L1_MARKER not in await _build("Matemática")


# ==============================================================================
# F4 — Roleplay (intenção/saída por regex + gate)
# ==============================================================================

def test_detect_roleplay_intent():
    assert detect_roleplay_intent("vamos praticar uma conversa no restaurante") == "restaurante"
    assert detect_roleplay_intent("vamos conversar") == ""          # intenção genérica
    assert detect_roleplay_intent("faça um roleplay no aeroporto") == "aeroporto"
    assert detect_roleplay_intent("o que é o present perfect?") is None  # sem intenção


def test_is_roleplay_exit():
    assert is_roleplay_exit("parar") is True
    assert is_roleplay_exit("encerrar") is True
    assert is_roleplay_exit("quero continuar a conversa") is False


def test_roleplay_enabled_exige_as_duas_flags(mocker):
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", True)
    mocker.patch.object(settings, "ROLEPLAY_ENABLED", False)
    assert roleplay_enabled() is False
    mocker.patch.object(settings, "ROLEPLAY_ENABLED", True)
    assert roleplay_enabled() is True
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", False)
    assert roleplay_enabled() is False


# ==============================================================================
# F8 — Exam prep (TOEFL/IELTS): estilo-prova + gate
# ==============================================================================

def test_exam_prep_perspectives_nao_vazio():
    assert len(EXAM_PREP_PERSPECTIVES) >= 2


def test_exam_prep_prompt_formata():
    prompt = EXAM_PREP_PROMPT.format(
        target_language_name="inglês", cefr_level="B2", student_name="Ana",
        course_name="Inglês", lesson_label="Capítulo 1",
        topic="academic vocabulary", perspective="reading comprehension",
        lesson_content="The study concluded that...",
    )
    assert "inglês" in prompt and "TOEFL/IELTS" in prompt and "The study" in prompt


def test_exam_prep_enabled_exige_as_duas_flags(mocker):
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", True)
    mocker.patch.object(settings, "EXAM_PREP_ENABLED", False)
    assert exam_prep_enabled() is False
    mocker.patch.object(settings, "EXAM_PREP_ENABLED", True)
    assert exam_prep_enabled() is True
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", False)
    assert exam_prep_enabled() is False


# ==============================================================================
# F9 — White-Label/B2B: opt-out de mensagens (SAIR/VOLTAR) + cláusula SQL
# ==============================================================================

def test_optout_intent():
    assert optout_intent("sair") == "out"
    assert optout_intent("SAIR") == "out"
    assert optout_intent("cancelar") == "out"
    assert optout_intent("voltar") == "in"
    assert optout_intent("retomar") == "in"
    assert optout_intent("o que é o present perfect?") is None
    assert optout_intent("vou sair de casa") is None  # não é standalone


def test_messaging_optout_clause():
    clause = messaging_optout_clause("s")
    assert "messaging_opted_out" in clause and clause.strip().startswith("AND")
    # 'sair' do roleplay foi reservado ao opt-out → não é mais saída de roleplay.
    assert is_roleplay_exit("sair") is False
