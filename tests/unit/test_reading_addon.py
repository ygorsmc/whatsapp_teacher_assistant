"""
Testes unitários do Addon de Livros, Apostilas e Materiais Complementares.

São testes PUROS (parsers, regex e helpers de cálculo): não tocam banco,
Redis nem rede. Rodar com:  pytest tests/unit/test_reading_addon.py -v
"""

from datetime import datetime

import pytest

from src.shared.constants.commands import (
    CommandType,
    STUDENT_COMMAND_REGEX,
    parse_management_command,
    parse_student_command,
)
from src.drive.client import material_type_for_folder, extract_section_number
from src.workers.reading_journey import (
    compute_reading_next_trigger,
    update_streak,
    progress_percent,
    parse_active_chapters,
    next_active_section,
    section_label_for,
    extract_section_refs,
    _checkin_hour,
)
from src.workers.spaced_repetition import next_interval_on_correct, adjust_ease


# ==============================================================================
# Fase 1 — Detecção de material_type na árvore do Google Drive
# ==============================================================================

@pytest.mark.parametrize("folder,expected", [
    ("Aulas", "lesson"),
    ("aulas", "lesson"),
    ("Livro", "book"),
    ("LIVROS", "book"),
    ("Apostila", "workbook"),
    ("apostilas ", "workbook"),
    ("1 - Introdução", None),   # pasta de seção/aula solta NÃO é agrupador
    ("Materiais", None),
])
def test_material_type_for_folder(folder, expected):
    assert material_type_for_folder(folder) == expected


@pytest.mark.parametrize("folder,expected", [
    ("1 - Introdução", 1),       # formato legado das aulas
    ("12 - Avançado", 12),
    ("Cap 3", 3),                # capítulos de livro
    ("Capítulo 7", 7),
    ("Módulo 2", 2),
    ("Apêndice", 1),             # sem número → default 1 (comportamento legado)
])
def test_extract_section_number(folder, expected):
    assert extract_section_number(folder) == expected


# ==============================================================================
# Comandos do professor (gerenciais multi-arg)
# ==============================================================================

def test_plano_leitura():
    assert parse_management_command("/plano_leitura_TurmaA_semanal") == (
        CommandType.READING_PLAN, "TurmaA", "semanal"
    )


def test_plano_leitura_turma_com_espaco():
    assert parse_management_command("/plano_leitura_Turma A_diario") == (
        CommandType.READING_PLAN, "Turma A", "diario"
    )


def test_simulado():
    assert parse_management_command("/simulado_TurmaA_5") == (
        CommandType.SIMULADO, "TurmaA", "5"
    )


def test_vincular_aula():
    # extra = "aula_capítulo" — o handler faz o split fino (2_3 → Aula 2, Cap 3)
    assert parse_management_command("/vincular_aula_TurmaA_2_3") == (
        CommandType.LINK_LESSON_CHAPTER, "TurmaA", "2_3"
    )


def test_errata_com_texto_livre():
    assert parse_management_command("/errata_TurmaA_cap3_Onde diz 'X', leia 'Y'") == (
        CommandType.ERRATA, "TurmaA", "cap3_Onde diz 'X', leia 'Y'"
    )


def test_ativar_capitulos():
    assert parse_management_command("/ativar_capitulos_TurmaA_1,3,5,7") == (
        CommandType.ACTIVATE_CHAPTERS, "TurmaA", "1,3,5,7"
    )


def test_gabarito_section_ref_com_underscore():
    # O section_ref "cap3_ex2" contém underscore — o split de turma pega só o
    # primeiro segmento; o resto vai inteiro para o handler.
    assert parse_management_command("/gabarito_TurmaA_cap3_ex2_42J") == (
        CommandType.ANSWER_KEY, "TurmaA", "cap3_ex2_42J"
    )


@pytest.mark.parametrize("texto,cmd", [
    ("/mapa_dificuldade_TurmaA", CommandType.DIFFICULTY_MAP),
    ("/funil_conclusao_TurmaA", CommandType.COMPLETION_FUNNEL),
    ("/corpus_duvidas_TurmaA", CommandType.DOUBTS_CORPUS),
])
def test_telemetria_editorial(texto, cmd):
    assert parse_management_command(texto) == (cmd, "TurmaA", "")


def test_aviso_continua_funcionando():
    # Não-regressão: o comando multi-arg original não pode quebrar.
    assert parse_management_command("/aviso_TurmaA_Reunião dia 10_05 às 19h") == (
        CommandType.BROADCAST_NOTICE, "TurmaA", "Reunião dia 10_05 às 19h"
    )


# ==============================================================================
# Comandos do aluno (allowlist do webhook)
# ==============================================================================

@pytest.mark.parametrize("texto,cmd", [
    ("/pausar_leitura", CommandType.PAUSE_READING),
    ("/retomar_leitura", CommandType.RESUME_READING),
    ("/meu_progresso", CommandType.MY_PROGRESS),
    ("/flashcards", CommandType.FLASHCARDS),
    ("/PAUSAR_LEITURA", CommandType.PAUSE_READING),
    ("  /meu_progresso  ", CommandType.MY_PROGRESS),
])
def test_student_commands(texto, cmd):
    assert parse_student_command(texto) == cmd


@pytest.mark.parametrize("texto", [
    "/quiz_1",                 # comando de professor não entra na allowlist
    "/sinc_alunos",
    "/engajamento_TurmaA",
    "/pausar_leitura_agora",   # sufixo extra → não casa (allowlist estrita)
    "pausar_leitura",          # sem a barra
    "Oi, tudo bem?",
])
def test_student_commands_nao_match(texto):
    assert parse_student_command(texto) is None
    assert STUDENT_COMMAND_REGEX.match(texto.strip()) is None


# ==============================================================================
# Fase 2 — Helpers da Jornada de Leitura
# ==============================================================================

def test_next_trigger_daily_apos_horario():
    # 11/06 12:00 UTC = 09:00 BRT (depois das 07:00) → amanhã 07:00 BRT = 10:00 UTC
    trigger = compute_reading_next_trigger("daily", "07:00", datetime(2026, 6, 11, 12, 0))
    assert trigger == datetime(2026, 6, 12, 10, 0)


def test_next_trigger_daily_antes_do_horario():
    # 11/06 08:00 UTC = 05:00 BRT (antes das 07:00) → hoje 07:00 BRT = 10:00 UTC
    trigger = compute_reading_next_trigger("daily", "07:00", datetime(2026, 6, 11, 8, 0))
    assert trigger == datetime(2026, 6, 11, 10, 0)


def test_next_trigger_weekly():
    trigger = compute_reading_next_trigger("weekly", "07:00", datetime(2026, 6, 11, 12, 0))
    assert trigger == datetime(2026, 6, 18, 10, 0)


def test_streak_primeiro_dia():
    assert update_streak(None, datetime(2026, 6, 11, 22, 0), 0) == 1


def test_streak_dia_consecutivo():
    assert update_streak(datetime(2026, 6, 10, 22, 0), datetime(2026, 6, 11, 22, 0), 5) == 6


def test_streak_mesmo_dia_nao_duplica():
    assert update_streak(datetime(2026, 6, 11, 10, 0), datetime(2026, 6, 11, 22, 0), 5) == 5


def test_streak_quebra_apos_buraco():
    assert update_streak(datetime(2026, 6, 5, 22, 0), datetime(2026, 6, 11, 22, 0), 9) == 1


def test_progress_percent():
    assert progress_percent(0, 10) == 0
    assert progress_percent(4, 10) == 40
    assert progress_percent(10, 10) == 100
    assert progress_percent(5, 12) == 42   # o "42% do livro" do spec
    assert progress_percent(1, 0) == 0     # divisão por zero protegida


def test_checkin_hour():
    assert _checkin_hour("07:00") == "19:00"
    assert _checkin_hour("18:30") == "06:30"  # wrap de meia-noite


def test_section_label():
    assert section_label_for("book", 3) == "Capítulo 3"
    assert section_label_for("workbook", 2) == "Módulo 2"


# ==============================================================================
# Fase 6.7 — Modo Professor Curador (capítulos ativos)
# ==============================================================================

def test_parse_active_chapters():
    assert parse_active_chapters("[1, 3, 5]") == [1, 3, 5]
    assert parse_active_chapters(None) is None       # NULL = todos ativos
    assert parse_active_chapters("inválido") is None  # corrompido = todos
    assert parse_active_chapters("[]") is None        # vazio = todos


def test_next_active_section_sem_curadoria():
    assert next_active_section(0, 10, None) == 1
    assert next_active_section(9, 10, None) == 10
    assert next_active_section(10, 10, None) is None  # concluído


def test_next_active_section_pula_desativados():
    # Capítulos ativos: 1, 3, 5 — após o 1, pula o 2 e vai direto ao 3.
    assert next_active_section(1, 10, [1, 3, 5]) == 3
    assert next_active_section(3, 10, [1, 3, 5]) == 5
    # Depois do 5 não há mais capítulos ativos → jornada concluída.
    assert next_active_section(5, 10, [1, 3, 5]) is None


# ==============================================================================
# Fase 4 — Escada da Revisão Espaçada (D+3 → D+10 → D+30)
# ==============================================================================

def test_srs_escada_de_intervalos():
    assert next_interval_on_correct(3) == 10
    assert next_interval_on_correct(10) == 30
    assert next_interval_on_correct(30) is None  # ciclo completo


def test_srs_ease_factor_clamp():
    assert adjust_ease(2.5, True) == 2.6
    assert adjust_ease(2.5, False) == 2.3
    assert adjust_ease(1.4, False) == 1.3   # piso
    assert adjust_ease(2.95, True) == 3.0   # teto


def test_spaced_review_intro_mostra_intervalo_e_disciplina():
    # Sem a disciplina explícita, "Design Thinking" e outras matérias do mesmo
    # curso (ex.: Inteligência Artificial) ficam indistinguíveis para o aluno.
    from src.shared.constants.messages import SPACED_REVIEW_INTRO
    msg = SPACED_REVIEW_INTRO.format(interval=10, subject_name="Design Thinking")
    assert "D+10" in msg
    assert "Design Thinking" in msg
    assert "{" not in msg  # todos os placeholders resolvidos


# ==============================================================================
# Fase 5 — Gabarito Verificado (extração de referências da pergunta)
# ==============================================================================

def test_extract_section_refs_formato_pontuado():
    assert extract_section_refs("não entendi o exercício 3.2 do livro") == ["cap3_ex2"]


def test_extract_section_refs_formato_extenso():
    assert extract_section_refs("me ajuda no exercício 2 do capítulo 3?") == ["cap3_ex2"]


def test_extract_section_refs_sem_referencia():
    assert extract_section_refs("o que é fotossíntese?") == []
