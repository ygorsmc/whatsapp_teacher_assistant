"""
Testes da camada de fontes de material/cadastro (Ports & Adapters — src/drive/).

Cobrem: contrato do DTO, convenção de pastas (re-export), adapters LOCAIS
(prova de "qualquer fonte") e a seleção por env var na factory — sem rede.
"""

import os

import pytest

from src.drive.interfaces import IMaterialSource, IRegistrySource, MaterialFile
from src.drive.local import LocalFolderMaterialSource, LocalCsvRegistrySource
from src.drive.client import GoogleDriveClient
from src.drive import factory
from src.shared.config import settings


# ── DTO ──────────────────────────────────────────────────────────────────────
def test_material_file_defaults():
    mf = MaterialFile(id="x", name="a.pdf")
    assert mf.mime_type == ""
    assert mf.lesson_number == 1
    assert mf.material_type == "lesson"
    assert mf.course_name is None and mf.subject_name is None


# ── Convenção de pasta (re-export por compat) ────────────────────────────────
def test_folder_convention_reexport_matches():
    from src.drive.client import material_type_for_folder, extract_section_number
    from src.drive import folder_convention as fc
    assert material_type_for_folder is fc.material_type_for_folder
    assert extract_section_number("Cap 3") == 3
    assert fc.material_type_for_folder("Apostila") == "workbook"
    assert fc.material_type_for_folder("Aula Solta") is None


# ── Conformidade às portas ───────────────────────────────────────────────────
def test_adapters_conform_to_ports():
    assert isinstance(LocalFolderMaterialSource(), IMaterialSource)
    assert isinstance(LocalCsvRegistrySource(), IRegistrySource)
    # O adapter Google cumpre AS DUAS portas (auth compartilhada).
    gd = GoogleDriveClient()
    assert isinstance(gd, IMaterialSource)
    assert isinstance(gd, IRegistrySource)


# ── LocalFolderMaterialSource (a prova de "qualquer fonte") ───────────────────
def _build_tree(root: str):
    def mk(*parts):
        os.makedirs(os.path.join(root, *parts), exist_ok=True)
    def write(rel, content=b"x"):
        path = os.path.join(root, rel)
        with open(path, "wb") as fh:
            fh.write(content)
    # Curso > Disciplina > Aulas > "1 - Introdução" > aula1.pdf
    mk("Inteligência Artificial", "Design Thinking", "Aulas", "1 - Introdução")
    write("Inteligência Artificial/Design Thinking/Aulas/1 - Introdução/aula1.pdf")
    # Curso > Disciplina > Livro > "Cap 2" > cap2.docx
    mk("Inteligência Artificial", "Design Thinking", "Livro", "Cap 2")
    write("Inteligência Artificial/Design Thinking/Livro/Cap 2/cap2.docx")
    # Seção solta direto na Disciplina (legado) = lesson; + arquivo não suportado
    mk("Inteligência Artificial", "Design Thinking", "2 - Solta")
    write("Inteligência Artificial/Design Thinking/2 - Solta/solta.md", b"# md")
    write("Inteligência Artificial/Design Thinking/2 - Solta/ignore.zip")


@pytest.mark.asyncio
async def test_local_material_source_walks_tree(tmp_path):
    _build_tree(str(tmp_path))
    files = await LocalFolderMaterialSource().list_materials(str(tmp_path))

    by_name = {f.name: f for f in files}
    assert set(by_name) == {"aula1.pdf", "cap2.docx", "solta.md"}  # .zip ignorado

    aula = by_name["aula1.pdf"]
    assert aula.course_name == "Inteligência Artificial"
    assert aula.subject_name == "Design Thinking"
    assert aula.material_type == "lesson"
    assert aula.lesson_number == 1
    assert aula.mime_type == "application/pdf"

    assert by_name["cap2.docx"].material_type == "book"
    assert by_name["cap2.docx"].lesson_number == 2  # "Cap 2"
    assert by_name["solta.md"].material_type == "lesson"
    assert by_name["solta.md"].lesson_number == 2   # "2 - Solta"


@pytest.mark.asyncio
async def test_local_material_source_download(tmp_path):
    _build_tree(str(tmp_path))
    src = LocalFolderMaterialSource()
    files = await src.list_materials(str(tmp_path))
    md = next(f for f in files if f.name == "solta.md")
    assert await src.download(md.id) == b"# md"


# ── LocalCsvRegistrySource ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_local_csv_registry_skips_header(tmp_path):
    csv_path = tmp_path / "Professores.csv"
    csv_path.write_text(
        "Nome,Telefone\nAna,5511999990000\nBruno,5511888880000\n", encoding="utf-8"
    )
    rows = await LocalCsvRegistrySource().read_table(str(tmp_path), "Professores")
    assert rows == [["Ana", "5511999990000"], ["Bruno", "5511888880000"]]


@pytest.mark.asyncio
async def test_local_csv_registry_missing_table_is_empty(tmp_path):
    rows = await LocalCsvRegistrySource().read_table(str(tmp_path), "Inexistente")
    assert rows == []


# ── Factory: seleção por env var ─────────────────────────────────────────────
def test_factory_selects_local(mocker):
    mocker.patch.object(settings, "MATERIAL_SOURCE_BACKEND", "local")
    mocker.patch.object(settings, "REGISTRY_BACKEND", "local_csv")
    factory.reset_source_cache()
    try:
        assert isinstance(factory.get_material_source(), LocalFolderMaterialSource)
        assert isinstance(factory.get_registry_source(), LocalCsvRegistrySource)
        # Cacheado: mesma instância em chamadas repetidas.
        assert factory.get_material_source() is factory.get_material_source()
    finally:
        factory.reset_source_cache()


def test_factory_default_is_gdrive(mocker):
    mocker.patch.object(settings, "MATERIAL_SOURCE_BACKEND", "gdrive")
    mocker.patch.object(settings, "REGISTRY_BACKEND", "gdrive")
    factory.reset_source_cache()
    try:
        assert isinstance(factory.get_material_source(), GoogleDriveClient)
        assert isinstance(factory.get_registry_source(), GoogleDriveClient)
    finally:
        factory.reset_source_cache()
