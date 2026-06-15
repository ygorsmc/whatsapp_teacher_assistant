"""
Portas (Ports & Adapters) para a ORIGEM dos documentos e do cadastro.

O sistema não deve depender do Google Drive/Sheets como fonte de verdade: o
material didático e a planilha de cadastro podem vir de QUALQUER fonte (Drive,
Azure Blob/S3, pasta local, CSV, banco…). Cada fonte é um adapter que cumpre
uma destas duas interfaces; a troca é por env var na factory — mesmo padrão de
`IDocumentParser`, `IMessagingChannel`, `ISpeechToText`, etc.

Há DUAS responsabilidades distintas, por isso DUAS portas:

  • IMaterialSource  — lista e baixa os ARQUIVOS de material (ingestão/RAG).
  • IRegistrySource  — lê o CADASTRO tabular (professores/alunos/turmas/chaves).

O DTO `MaterialFile` é o contrato de saída de `list_materials`: qualquer fonte
produz a MESMA forma, então a lógica de negócio (ingestão, Addon de Livros)
nunca vê detalhes do fornecedor.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class MaterialFile:
    """Um arquivo de material, agnóstico de fonte.

    `id` é o identificador opaco que a MESMA fonte usa em `download()` (no Drive
    é o fileId; numa pasta local é o caminho; num bucket é a key). `lesson_number`
    é o número da aula (material 'lesson') ou do capítulo/seção ('book'/'workbook').
    """
    id: str
    name: str
    mime_type: str = ""
    course_name: Optional[str] = None
    subject_name: Optional[str] = None
    lesson_number: int = 1
    material_type: str = "lesson"  # 'lesson' | 'book' | 'workbook'
    modified_time: Optional[str] = None


class IMaterialSource(ABC):
    """Fonte dos ARQUIVOS de material didático (ingestão → parsing → RAG)."""

    @abstractmethod
    async def list_materials(self, root_id: str) -> list[MaterialFile]:
        """Lista os materiais sob a raiz `root_id`, já rotulados com
        course_name / subject_name / lesson_number / material_type.

        A convenção de hierarquia (Curso > Disciplina > [Aulas|Livro|Apostila]
        > Seção > Arquivos) é responsabilidade do adapter; o contrato de SAÍDA
        (`MaterialFile`) é o mesmo para todas as fontes."""
        ...

    @abstractmethod
    async def download(self, file_id: str) -> bytes:
        """Baixa o conteúdo bruto do arquivo `file_id` (mesmo id de `MaterialFile`)."""
        ...


class IRegistrySource(ABC):
    """Fonte do CADASTRO tabular (a 'planilha do professor', venha de onde vier).

    O parsing das abas já é agnóstico (consome `list[list[str]]`); esta porta
    abstrai apenas o FETCH das linhas de uma tabela lógica, sem cabeçalho."""

    # Tabelas lógicas que o `spreadsheet_sync` consome. Cada adapter traduz o
    # nome lógico para o seu meio (aba do Sheets, arquivo CSV, tabela SQL…).
    PROFESSORES = "Professores"
    ALUNOS = "Alunos"
    TURMAS = "Turmas"
    CHAVES = "Chaves"

    @abstractmethod
    async def read_table(self, source_id: str, table: str) -> list[list[str]]:
        """Devolve as linhas de DADOS (cabeçalho excluído) da tabela lógica
        `table` dentro da fonte `source_id`, como listas de strings."""
        ...
