"""
Testes unitários da camada LGPD (Plano P0-P1 — _docs/docs/PLANO_LGPD.md).

Testes PUROS (helpers, regexes, cláusulas SQL, processor de log e markers):
não tocam banco, Redis nem rede. Rodar com:
    pytest tests/unit/test_lgpd.py -v
"""

import pytest

from src.shared import privacy
from src.shared.config import settings
from src.shared.constants.commands import (
    CommandType,
    GUARDIAN_REVOKE_REGEX,
    STUDENT_COMMAND_REGEX,
    parse_student_command,
)
from src.shared.telemetry import mask_pii_processor


# ==============================================================================
# P0.1 — Minimização de PII no prompt (primeiro nome)
# ==============================================================================

@pytest.mark.parametrize("full,expected", [
    ("Ana Beatriz Silva", "Ana"),
    ("João", "João"),
    ("  Maria   Clara  ", "Maria"),
    ("", "aluno(a)"),
    (None, "aluno(a)"),
    ("   ", "aluno(a)"),
])
def test_first_name(full, expected):
    assert privacy.first_name(full) == expected


def test_llm_safe_name_minimizes_when_flag_on(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PII_MINIMIZATION", True)
    assert privacy.llm_safe_name("Ana Beatriz Silva") == "Ana"
    # Nunca vaza sobrenome com a flag ligada.
    assert "Silva" not in privacy.llm_safe_name("Ana Beatriz Silva")


def test_llm_safe_name_full_when_flag_off(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PII_MINIMIZATION", False)
    assert privacy.llm_safe_name("Ana Beatriz Silva") == "Ana Beatriz Silva"
    assert privacy.llm_safe_name("") == "aluno(a)"


def test_prompt_builder_uses_first_name_only(monkeypatch):
    """O system prompt renderizado NUNCA contém o sobrenome do aluno (P0.1)."""
    monkeypatch.setattr(settings, "LLM_PII_MINIMIZATION", True)
    from src.shared.constants.prompts import SYSTEM_PROMPT_TEMPLATE
    rendered = SYSTEM_PROMPT_TEMPLATE.format(
        teacher_name="Prof. X", teacher_subject="Física", teacher_persona="amigável",
        student_name=privacy.llm_safe_name("Ana Beatriz Silva"),
        current_lesson_number=3, current_lesson_label="Aula 3",
        course_name="Curso", subject_text="", out_of_scope="fora",
    )
    assert "Ana" in rendered
    assert "Beatriz" not in rendered and "Silva" not in rendered


# ==============================================================================
# P1.4 — Mascaramento de PII em logs
# ==============================================================================

@pytest.mark.parametrize("contact,expected", [
    ("5541999887766", "…7766"),
    ("123", "…123"),
    ("", "?"),
    (None, "?"),
])
def test_mask_contact(contact, expected):
    assert privacy.mask_contact(contact) == expected


def test_mask_pii_processor_masks_known_fields():
    event = {
        "event": "x", "sender_id": "5541999887766", "phone": "5511888776655",
        "recipient_id": "999000111", "guardian_phone": "5541777", "teacher_id": "tch_1",
        "course_id": "crs_1", "score": 0.9,
    }
    out = mask_pii_processor(None, "info", dict(event))
    assert out["sender_id"] == "…7766"
    assert out["phone"] == "…6655"
    assert out["recipient_id"] == "…0111"
    assert out["guardian_phone"] == "…1777"
    # IDs internos (pseudônimos) NÃO são mascarados — são necessários p/ debug.
    assert out["teacher_id"] == "tch_1"
    assert out["course_id"] == "crs_1"
    assert out["score"] == 0.9


def test_mask_pii_processor_ignores_missing_and_none():
    assert mask_pii_processor(None, "info", {"event": "x"}) == {"event": "x"}
    out = mask_pii_processor(None, "info", {"sender_id": None})
    assert out["sender_id"] is None


# ==============================================================================
# P1.1 — Cláusula SQL do gate de consentimento parental
# ==============================================================================

def test_consent_clause_empty_when_flag_off(monkeypatch):
    monkeypatch.setattr(settings, "GUARDIAN_CONSENT_ENABLED", False)
    assert privacy.guardian_consent_sql_clause("s") == ""


def test_consent_clause_filters_unconsented_children_when_on(monkeypatch):
    monkeypatch.setattr(settings, "GUARDIAN_CONSENT_ENABLED", True)
    clause = privacy.guardian_consent_sql_clause("s")
    assert clause.strip().startswith("AND")
    assert "s.is_child = false" in clause
    assert '"GuardianConsents"' in clause
    assert "status = 'granted'" in clause
    # respeita o alias passado
    assert privacy.guardian_consent_sql_clause("rp").count("rp.") >= 1


# ==============================================================================
# Comandos — /apagar_meus_dados e REVOGAR
# ==============================================================================

def test_delete_my_data_in_student_allowlist():
    assert STUDENT_COMMAND_REGEX.match("/apagar_meus_dados")
    assert parse_student_command("/apagar_meus_dados") == CommandType.DELETE_MY_DATA


def test_existing_student_commands_still_parse():
    assert parse_student_command("/meu_progresso") == CommandType.MY_PROGRESS
    assert parse_student_command("/sair_livro") == CommandType.LEAVE_BOOK
    assert parse_student_command("/corrigir") == CommandType.CORRECT_ESSAY


def test_non_allowlisted_command_rejected():
    assert parse_student_command("/apagar_tudo_do_professor") is None
    assert STUDENT_COMMAND_REGEX.match("/apagar_meus_dados extra") is None


@pytest.mark.parametrize("text,matches", [
    ("REVOGAR", True),
    ("revogar", True),
    ("  Revogar  ", True),
    ("revogar consentimento", False),
    ("quero revogar", False),
])
def test_guardian_revoke_regex(text, matches):
    assert bool(GUARDIAN_REVOKE_REGEX.match(text.strip())) is matches


# ==============================================================================
# P1.3 — Marcador mensal de retenção (idempotência)
# ==============================================================================

def test_retention_month_marker():
    from datetime import datetime, timezone
    from src.workers.retention import retention_month_marker
    marker = retention_month_marker(datetime(2026, 6, 12, tzinfo=timezone.utc))
    assert marker == "retention_done:202606"
    # meses diferentes → markers diferentes (não roda 2x no mesmo mês)
    other = retention_month_marker(datetime(2026, 7, 1, tzinfo=timezone.utc))
    assert other != marker


# ==============================================================================
# P1.6 — Chave da janela de revisão de redação
# ==============================================================================

def test_essay_review_window_key():
    from src.workers.privacy_commands import essay_review_window_key
    assert essay_review_window_key("stu_42") == "essay_review_window:stu_42"
