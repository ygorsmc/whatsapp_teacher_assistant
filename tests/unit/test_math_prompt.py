"""Testes da diretiva de raciocínio matemático no System Prompt (M5).

Invariante-mestra: com `MATH_MODE_ENABLED` OFF, o prompt é byte-a-byte o de
hoje (a vertical SÓ anexa, nunca reescreve). Rodar:
    pytest tests/unit/test_math_prompt.py -v
"""

from types import SimpleNamespace

import pytest

from src.shared.config import settings
from src.shared.constants.prompts import MATH_REASONING_DIRECTIVE

_MATH_MARKER = "MODO RACIOCÍNIO MATEMÁTICO"


def _fake_actors():
    teacher = SimpleNamespace(
        name="Ana", subject="Física", assistant_persona="amigável",
        out_of_scope_message="Fora do escopo.",
    )
    student = SimpleNamespace(name="João Silva")
    return teacher, student


async def _build(course_name: str, subject_name: str | None = None):
    from src.rag.prompt_builder import build_system_prompt
    teacher, student = _fake_actors()
    return await build_system_prompt(teacher, student, "crs_x", course_name, "les_01", subject_name)


# ── A diretiva traz os dois objetivos (isolar fórmula + LaTeX p/ entrega) ─────

def test_directive_has_isolation_and_latex_bridge():
    assert "ISOLE" in MATH_REASONING_DIRECTIVE
    # Ponte M5→M6: instrui inline ($...$) e display ($$...$$).
    assert "$$" in MATH_REASONING_DIRECTIVE
    assert r"\frac" in MATH_REASONING_DIRECTIVE


# ── Invariante-mestra: flag OFF não anexa nada ───────────────────────────────

@pytest.mark.asyncio
async def test_flag_off_does_not_append(mocker):
    mocker.patch.object(settings, "MATH_MODE_ENABLED", False)
    prompt = await _build("Cálculo I", subject_name="Cálculo")
    assert _MATH_MARKER not in prompt  # exatas, mas flag off ⇒ intocado


@pytest.mark.asyncio
async def test_flag_off_is_byte_for_byte_prefix_of_on(mocker):
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", False)
    mocker.patch.object(settings, "CHART_GENERATION_ENABLED", False)

    mocker.patch.object(settings, "MATH_MODE_ENABLED", False)
    prompt_off = await _build("Cálculo I", subject_name="Cálculo")
    mocker.patch.object(settings, "MATH_MODE_ENABLED", True)
    prompt_on = await _build("Cálculo I", subject_name="Cálculo")

    assert prompt_on.startswith(prompt_off)   # OFF = prefixo exato
    assert prompt_on != prompt_off            # ON realmente anexa
    assert _MATH_MARKER not in prompt_off


# ── Flag ON: anexa só para disciplina de exatas ──────────────────────────────

@pytest.mark.asyncio
async def test_flag_on_appends_for_exatas_subject(mocker):
    mocker.patch.object(settings, "MATH_MODE_ENABLED", True)
    # Curso de nome neutro, mas disciplina de exatas → anexa.
    prompt = await _build("Turma 2026", subject_name="Física")
    assert _MATH_MARKER in prompt


@pytest.mark.asyncio
async def test_flag_on_appends_for_exatas_course_name(mocker):
    mocker.patch.object(settings, "MATH_MODE_ENABLED", True)
    prompt = await _build("Cálculo I")  # sem subject_name, pega pelo curso
    assert _MATH_MARKER in prompt


@pytest.mark.asyncio
async def test_flag_on_non_exatas_does_not_append(mocker):
    mocker.patch.object(settings, "MATH_MODE_ENABLED", True)
    assert _MATH_MARKER not in await _build("História da Arte", subject_name="História")
    # "Educação Física" tem a palavra "física" mas é stoplist → não anexa.
    assert _MATH_MARKER not in await _build("Educação Física")


# ── Diretiva de formatação: anexada SEMPRE (não é vertical) ──────────────────

@pytest.mark.asyncio
async def test_output_formatting_directive_always_present(mocker):
    # Curso normal, TODAS as flags off ⇒ a diretiva de formatação ainda aparece.
    mocker.patch.object(settings, "LANGUAGE_MODE_ENABLED", False)
    mocker.patch.object(settings, "MATH_MODE_ENABLED", False)
    mocker.patch.object(settings, "CHART_GENERATION_ENABLED", False)
    prompt = await _build("História Geral", subject_name="História")
    assert "FORMATAÇÃO DA RESPOSTA" in prompt
    assert "_texto_ para ITÁLICO" in prompt
