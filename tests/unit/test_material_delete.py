"""
Testes do /delete_<nº> (exclusão de material indexado) e do /listar_materiais
numerado. Cobrem o parsing, a renderização do caminho, a INVARIANTE DE SEGURANÇA
(todo filtro de exclusão inclui teacher_id) e os early-returns do fluxo.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.shared.constants.commands import parse_management_command, CommandType, _MULTIARG_COMMANDS
from src.shared.constants import messages
from src.workers import command_worker as cw


# ── Parsing ──────────────────────────────────────────────────────────────────
def test_parse_delete_command():
    assert parse_management_command("/delete_3") == (CommandType.DELETE_MATERIAL, "3", "")
    assert parse_management_command("/delete_12") == (CommandType.DELETE_MATERIAL, "12", "")
    # IGNORECASE do regex gerencial
    assert parse_management_command("/DELETE_5")[0] == CommandType.DELETE_MATERIAL


def test_delete_is_single_arg():
    assert CommandType.DELETE_MATERIAL not in _MULTIARG_COMMANDS


def test_deletar_is_not_delete():
    # "/deletar_x" NÃO deve casar como /delete (sem falso positivo).
    parsed = parse_management_command("/deletar_x")
    assert parsed is None or parsed[0] != CommandType.DELETE_MATERIAL


# ── Caminho / rótulo ─────────────────────────────────────────────────────────
def test_section_label():
    assert cw._section_label("lesson", 3) == "Aula 3"
    assert cw._section_label("book", 2) == "Cap. 2"
    assert cw._section_label("workbook", 1) == "Módulo 1"
    assert cw._section_label("desconhecido", 9) == "Seção 9"


def test_material_path():
    assert cw._material_path("Matemática", "Álgebra", "book", 2, "cap2.pdf") == \
        "Matemática › Álgebra › Cap. 2 › cap2.pdf"
    # campos vazios são omitidos
    assert cw._material_path("Curso", None, "lesson", 1, "a.pdf") == "Curso › Aula 1 › a.pdf"


# ── INVARIANTE DE SEGURANÇA: todo filtro inclui teacher_id ───────────────────
def _keys(flt):
    return {c.key for c in flt.must}


def test_primary_filter_scopes_by_file_id_and_teacher():
    flt = cw._material_primary_filter("FILE123", "teacher-A")
    keys = _keys(flt)
    assert "file_id" in keys
    assert "teacher_id" in keys  # isolamento por professor (regra inviolável)


def test_legacy_filter_always_scopes_by_teacher_and_course():
    ref = {
        "source": "aula3.pdf", "course_id": "c1", "subject_name": "Álgebra",
        "lesson_number": 3, "material_type": "lesson",
    }
    flt = cw._material_legacy_filter(ref, "teacher-A")
    keys = _keys(flt)
    assert "teacher_id" in keys
    assert "course_id" in keys
    assert "source" in keys
    assert {"subject_name", "lesson_number", "material_type"} <= keys


def test_legacy_filter_minimal_ref_still_has_teacher():
    # Mesmo sem subject/lesson/material, teacher_id + source seguem presentes.
    flt = cw._material_legacy_filter({"source": "x.pdf"}, "teacher-Z")
    keys = _keys(flt)
    assert "teacher_id" in keys and "source" in keys


# ── Fluxo: early-returns (sem tocar no Qdrant) ───────────────────────────────
def _fake_channel():
    ch = MagicMock()
    ch.send_text = AsyncMock()
    ch.send_buttons = AsyncMock()
    return ch


class _FakeSession:
    async def __aenter__(self):
        return self
    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_delete_rejects_non_numeric(mocker):
    ch = _fake_channel()
    await cw.run_delete_material_command(ch, "5511", "abc")
    ch.send_text.assert_awaited_once_with("5511", messages.CMD_DELETE_USAGE)


@pytest.mark.asyncio
async def test_delete_needs_listing_first(mocker):
    ch = _fake_channel()
    mocker.patch.object(cw, "AsyncSessionLocal", lambda: _FakeSession())
    mocker.patch.object(cw, "_resolve_teacher", AsyncMock(return_value=("t1", False)))
    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(return_value=None)  # nenhum índice salvo
    mocker.patch.object(cw, "get_redis", AsyncMock(return_value=fake_redis))

    await cw.run_delete_material_command(ch, "5511", "3")
    ch.send_text.assert_awaited_once_with("5511", messages.CMD_DELETE_NEED_LIST)


@pytest.mark.asyncio
async def test_delete_item_not_in_index(mocker):
    ch = _fake_channel()
    mocker.patch.object(cw, "AsyncSessionLocal", lambda: _FakeSession())
    mocker.patch.object(cw, "_resolve_teacher", AsyncMock(return_value=("t1", False)))
    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(return_value='{"1": {"path": "x"}}')  # só tem o item 1
    mocker.patch.object(cw, "get_redis", AsyncMock(return_value=fake_redis))

    await cw.run_delete_material_command(ch, "5511", "9")
    ch.send_text.assert_awaited_once_with("5511", messages.CMD_DELETE_NOT_FOUND.format(num="9"))


@pytest.mark.asyncio
async def test_decision_expired_when_no_pending(mocker):
    ch = _fake_channel()
    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(return_value=None)
    mocker.patch.object(cw, "get_redis", AsyncMock(return_value=fake_redis))

    await cw.handle_material_delete_decision(ch, "5511", confirmed=True)
    ch.send_text.assert_awaited_once_with("5511", messages.CMD_DELETE_EXPIRED)


@pytest.mark.asyncio
async def test_decision_cancel_deletes_pending_and_aborts(mocker):
    ch = _fake_channel()
    fake_redis = MagicMock()
    fake_redis.get = AsyncMock(return_value='{"file_id": "F", "teacher_id": "t1", "path": "p", "mode": "file_id"}')
    fake_redis.delete = AsyncMock()
    mocker.patch.object(cw, "get_redis", AsyncMock(return_value=fake_redis))

    await cw.handle_material_delete_decision(ch, "5511", confirmed=False)
    fake_redis.delete.assert_awaited_once()  # confirmação consumida
    ch.send_text.assert_awaited_once_with("5511", messages.CMD_DELETE_CANCELLED)
