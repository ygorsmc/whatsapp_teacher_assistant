"""
Testes unitários do parser de comandos gerenciais do professor.

São testes PUROS (regex + função de parse): não tocam banco, Redis nem rede.
Rodar com:  pytest tests/unit/test_management_commands.py -v
"""

import pytest

from src.shared.constants.commands import (
    CommandType,
    MANAGEMENT_COMMAND_REGEX,
    parse_management_command,
    parse_command,
)


# ==============================================================================
# /aviso_[turma]_[mensagem] — o caso mais delicado (mensagem livre)
# ==============================================================================

def test_aviso_basico():
    result = parse_management_command("/aviso_TurmaA_Amanhã não tem aula!")
    assert result == (CommandType.BROADCAST_NOTICE, "TurmaA", "Amanhã não tem aula!")


def test_aviso_mensagem_com_underscore():
    # Tudo após o 2º "_" pertence à mensagem — inclusive outros underscores.
    result = parse_management_command("/aviso_TurmaA_Reunião dia 10_05 às 19h")
    assert result == (CommandType.BROADCAST_NOTICE, "TurmaA", "Reunião dia 10_05 às 19h")


def test_aviso_turma_com_espaco():
    # A turma pode conter espaços (delimitador é o "_", não o espaço).
    result = parse_management_command("/aviso_Turma A_Prova adiada para sexta")
    assert result == (CommandType.BROADCAST_NOTICE, "Turma A", "Prova adiada para sexta")


def test_aviso_mensagem_multiline():
    # DOTALL: a mensagem do aviso pode ter quebras de linha.
    result = parse_management_command("/aviso_TurmaA_Linha 1\nLinha 2\nLinha 3")
    assert result is not None
    cmd, turma, msg = result
    assert cmd == CommandType.BROADCAST_NOTICE
    assert turma == "TurmaA"
    assert msg == "Linha 1\nLinha 2\nLinha 3"


def test_aviso_sem_mensagem_retorna_extra_vazio():
    # Sem o 2º "_" não há como separar turma de mensagem: o worker
    # responde com a orientação de formato (CMD_AVISO_USAGE).
    result = parse_management_command("/aviso_TurmaA")
    assert result == (CommandType.BROADCAST_NOTICE, "TurmaA", "")


# ==============================================================================
# Comandos de turma — /comando_[turma]
# ==============================================================================

def test_engajamento():
    assert parse_management_command("/engajamento_TurmaA") == (
        CommandType.ENGAGEMENT, "TurmaA", ""
    )


def test_acertos():
    assert parse_management_command("/acertos_Turma B") == (
        CommandType.QUIZ_PERFORMANCE, "Turma B", ""
    )


def test_risco():
    assert parse_management_command("/risco_3A") == (CommandType.AT_RISK, "3A", "")


def test_duvidas_frequentes():
    # O nome do comando TEM underscore interno — o regex precisa consumi-lo
    # como parte do comando, não como delimitador da turma.
    assert parse_management_command("/duvidas_frequentes_TurmaA") == (
        CommandType.FREQUENT_DOUBTS, "TurmaA", ""
    )


def test_limpar_quizzes():
    assert parse_management_command("/limpar_quizzes_TurmaA") == (
        CommandType.CLEAR_QUIZ_BANK, "TurmaA", ""
    )


def test_case_insensitive():
    assert parse_management_command("/Engajamento_T1") == (CommandType.ENGAGEMENT, "T1", "")
    assert parse_management_command("/AVISO_T1_Olá!") == (
        CommandType.BROADCAST_NOTICE, "T1", "Olá!"
    )


def test_espacos_nas_bordas_sao_aparados():
    assert parse_management_command("  /risco_TurmaA  ") == (CommandType.AT_RISK, "TurmaA", "")


# ==============================================================================
# Comandos de aluno — /comando_[telefone]
# ==============================================================================

def test_ver_aluno():
    assert parse_management_command("/ver_aluno_11999998888") == (
        CommandType.VIEW_STUDENT, "11999998888", ""
    )


def test_resetar_aluno():
    assert parse_management_command("/resetar_aluno_5511999998888") == (
        CommandType.RESET_STUDENT, "5511999998888", ""
    )


def test_ver_aluno_telefone_formatado():
    # O handler limpa a máscara depois; o parser só captura o argumento bruto.
    assert parse_management_command("/ver_aluno_(11) 99999-8888") == (
        CommandType.VIEW_STUDENT, "(11) 99999-8888", ""
    )


# ==============================================================================
# Não-matches: nada de falso positivo
# ==============================================================================

@pytest.mark.parametrize("texto", [
    "/quiz_1",                 # comando legado (parse_command cuida dele)
    "/aula Turma = 2",         # comando legado
    "/ajuda",                  # comando legado
    "/sinc_alunos",            # comando legado
    "/engajamento",            # sem "_arg" → não é gerencial válido
    "/risco_",                 # arg vazio → não casa (.+ exige 1+ chars)
    "engajamento_TurmaA",      # sem a barra inicial
    "Oi, tudo bem?",           # conversa normal de aluno
    "",                        # vazio
])
def test_nao_match(texto):
    assert parse_management_command(texto) is None


def test_regex_compartilhado_com_webhook():
    """O webhook usa o MESMO regex pré-compilado para rotular o payload —
    garante que webhook e worker nunca divergem sobre o que é gerencial."""
    m = MANAGEMENT_COMMAND_REGEX.match("/acertos_TurmaA")
    assert m is not None
    assert m.group("cmd").lower() == "acertos"

    assert MANAGEMENT_COMMAND_REGEX.match("/quiz_1") is None


# ==============================================================================
# Convivência com o parser legado
# ==============================================================================

def test_legados_continuam_funcionando():
    assert parse_command("/sinc_alunos") == (CommandType.SYNC_STUDENTS, "")
    assert parse_command("/aula Turma A = 2") == (CommandType.UPDATE_LESSON, "Turma A = 2")
    assert parse_command("/quiz_1") == (CommandType.SEND_QUIZ, "_1")
    assert parse_command("/ajuda")[0] == CommandType.HELP


def test_gerencial_nao_vaza_para_legado():
    # parse_command não conhece os gerenciais → UNKNOWN (o dispatch tenta o
    # gerencial PRIMEIRO, então isso nunca chega ao usuário).
    cmd, _ = parse_command("/engajamento_TurmaA")
    assert cmd == CommandType.UNKNOWN
