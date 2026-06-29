"""
Convenção de hierarquia de pastas (PURA, sem I/O) — compartilhada por toda
fonte de material baseada em "árvore de pastas" (Google Drive, pasta local,
prefixos de bucket S3/Blob…).

Estrutura: Curso/ → Disciplina/ → [Aulas | Livro | Apostila]/ → Seção N/ → Arquivos
O nível de TIPO de material é opcional: pastas de aula soltas direto na
Disciplina continuam valendo como 'lesson' (compatibilidade total com o legado).
"""

import re

# Tipos MIME aceitos na ingestão (multiformat).
ALLOWED_MIMES = [
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/plain",
    "text/markdown",
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.presentation",
]

# Dentro da pasta da Disciplina, pastas com estes nomes agrupam materiais de
# tipos diferentes. Pastas de aula soltas direto na Disciplina = 'lesson'.
MATERIAL_TYPE_FOLDERS = {
    "aulas": "lesson",
    "aula": "lesson",
    "lessons": "lesson",
    "lesson": "lesson",
    "livro": "book",
    "livros": "book",
    "book": "book",
    "books": "book",
    "apostila": "workbook",
    "apostilas": "workbook",
    "workbook": "workbook",
    "workbooks": "workbook",
}

# "1 - Introdução" (formato legado das aulas) tem precedência; senão, o
# primeiro número do nome ("Cap 3", "Capítulo 3", "Módulo 2") vale como seção.
# Pastas de idiomas como "A1-16-review-consolidation" devem ignorar o número do nível.
_LEVEL_PREFIX_REGEX = re.compile(r"^[A-Za-z]\d+-(\d+)")
_LEGACY_SECTION_REGEX = re.compile(r"^(\d+)\s*-")
_ANY_NUMBER_REGEX = re.compile(r"(\d+)")


def material_type_for_folder(folder_name: str) -> str | None:
    """Mapeia o nome de uma pasta para um material_type ('lesson' | 'book' |
    'workbook'). None se a pasta não for um agrupador de material conhecido."""
    return MATERIAL_TYPE_FOLDERS.get(folder_name.strip().lower())


def extract_section_number(folder_name: str) -> int:
    """Extrai o número da seção/aula/capítulo do nome da pasta.

    Exemplos: "1 - Introdução" → 1, "Cap 3" → 3, "Módulo 12" → 12.
    Default 1 quando não há número (mesmo comportamento legado).
    """
    match = _LEVEL_PREFIX_REGEX.match(folder_name)
    if match:
        return int(match.group(1))

    match = _LEGACY_SECTION_REGEX.match(folder_name)
    if not match:
        match = _ANY_NUMBER_REGEX.search(folder_name)
    return int(match.group(1)) if match else 1
