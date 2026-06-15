"""
Adapters de fonte LOCAL — provam que o sistema busca material/cadastro de
QUALQUER fonte (não só Google) e servem de fixture de dev/teste sem credenciais.

  • LocalFolderMaterialSource — varre uma árvore de pastas no disco usando a
    MESMA convenção de hierarquia do Drive (`folder_convention`).
  • LocalCsvRegistrySource — lê o cadastro de arquivos CSV ({tabela}.csv).

São o molde para futuros adapters (Azure Blob, S3/GCS, OneDrive, banco): basta
cumprir `IMaterialSource` / `IRegistrySource` e registrar na factory.
"""

import asyncio
import csv
import os

from src.shared.config import settings
from src.drive.interfaces import IMaterialSource, IRegistrySource, MaterialFile
from src.drive.folder_convention import (
    material_type_for_folder,
    extract_section_number,
)

# Extensão → MIME (espelha ALLOWED_MIMES; arquivos fora desta lista são ignorados,
# como o filtro de mimeType do adapter do Drive).
_EXT_TO_MIME = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".txt": "text/plain",
    ".md": "text/markdown",
}


def _list_subdirs(path: str) -> list[str]:
    """Subpastas diretas de `path`, ordenadas (determinismo)."""
    try:
        entries = [os.path.join(path, n) for n in os.listdir(path)]
    except (FileNotFoundError, NotADirectoryError):
        return []
    return sorted(d for d in entries if os.path.isdir(d))


class LocalFolderMaterialSource(IMaterialSource):
    """Material a partir de uma árvore de pastas no disco.

    `root_id` é o caminho da raiz (ou `LOCAL_MATERIAL_ROOT` se vazio); `id` de
    cada `MaterialFile` é o caminho absoluto do arquivo (usado em `download`)."""

    async def list_materials(self, root_id: str) -> list[MaterialFile]:
        root = root_id or settings.LOCAL_MATERIAL_ROOT
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._walk, root)

    def _walk(self, root: str) -> list[MaterialFile]:
        files: list[MaterialFile] = []

        def _collect_section(section_dir: str, c_name: str, s_name: str, mat_type: str):
            section_num = extract_section_number(os.path.basename(section_dir))
            try:
                names = sorted(os.listdir(section_dir))
            except (FileNotFoundError, NotADirectoryError):
                return
            for name in names:
                fpath = os.path.join(section_dir, name)
                if not os.path.isfile(fpath):
                    continue
                mime = _EXT_TO_MIME.get(os.path.splitext(name)[1].lower())
                if not mime:
                    continue  # tipo não suportado — ignora (como o Drive)
                files.append(MaterialFile(
                    id=fpath,
                    name=name,
                    mime_type=mime,
                    course_name=c_name,
                    subject_name=s_name,
                    lesson_number=section_num,
                    material_type=mat_type,
                    modified_time=str(os.path.getmtime(fpath)),
                ))

        # Root -> Curso -> Disciplina -> [Aulas|Livro|Apostila] | seção solta
        for course_dir in _list_subdirs(root):
            c_name = os.path.basename(course_dir)
            for subject_dir in _list_subdirs(course_dir):
                s_name = os.path.basename(subject_dir)
                for child_dir in _list_subdirs(subject_dir):
                    mat_type = material_type_for_folder(os.path.basename(child_dir))
                    if mat_type:
                        for section_dir in _list_subdirs(child_dir):
                            _collect_section(section_dir, c_name, s_name, mat_type)
                    else:
                        _collect_section(child_dir, c_name, s_name, "lesson")
        return files

    async def download(self, file_id: str) -> bytes:
        loop = asyncio.get_running_loop()

        def _read() -> bytes:
            with open(file_id, "rb") as fh:
                return fh.read()

        return await loop.run_in_executor(None, _read)


class LocalCsvRegistrySource(IRegistrySource):
    """Cadastro a partir de CSVs em disco: cada tabela lógica é um `{tabela}.csv`.

    `source_id` é o diretório dos CSVs (ou `LOCAL_REGISTRY_DIR` se vazio). A
    primeira linha (cabeçalho) é descartada — espelha o início em `A2` do Sheets."""

    async def read_table(self, source_id: str, table: str) -> list[list[str]]:
        base_dir = source_id or settings.LOCAL_REGISTRY_DIR
        path = os.path.join(base_dir, f"{table}.csv")
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._read_csv, path)

    @staticmethod
    def _read_csv(path: str) -> list[list[str]]:
        if not os.path.isfile(path):
            return []
        with open(path, newline="", encoding="utf-8") as fh:
            rows = list(csv.reader(fh))
        return rows[1:] if rows else []  # pula o cabeçalho (= A2 no Sheets)
