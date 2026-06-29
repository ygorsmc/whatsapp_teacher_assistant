"""
Adapter Google Drive + Sheets — implementação das portas `IMaterialSource` e
`IRegistrySource` (ver `src/drive/interfaces.py`).

Uma ÚNICA classe cumpre as duas portas porque a autenticação (Service Account)
é compartilhada entre Drive e Sheets — evita inicializar o serviço Google duas
vezes. A factory (`src/drive/factory.py`) decide, por env var, se esta é a fonte
ativa para material e/ou cadastro.

Os helpers de convenção de pasta (`material_type_for_folder`,
`extract_section_number`) foram movidos para `folder_convention.py` (puros,
compartilhados por outras fontes de árvore) e são re-exportados aqui por
compatibilidade com importadores existentes.
"""

import os
import io
import asyncio
import socket
import structlog
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# Increase default socket timeout for Google Drive API (nested folder reads can take >60s)
socket.setdefaulttimeout(300)

from src.shared.config import settings
from src.drive.interfaces import IMaterialSource, IRegistrySource, MaterialFile
from src.drive.folder_convention import (  # re-export (compat) + uso interno
    ALLOWED_MIMES,
    MATERIAL_TYPE_FOLDERS,
    material_type_for_folder,
    extract_section_number,
)

__all__ = [
    "GoogleDriveClient",
    "drive_client",
    "material_type_for_folder",
    "extract_section_number",
    "MATERIAL_TYPE_FOLDERS",
]

logger = structlog.get_logger(__name__)

# Scopes needed for downloading files and reading spreadsheets
SCOPES = [
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/spreadsheets.readonly'
]

# Mapeia cada tabela LÓGICA do cadastro para o range A1 da aba correspondente.
# Preserva byte a byte os ranges historicamente usados pelo spreadsheet_sync
# (linha 2 em diante = pula o cabeçalho; o teto de colunas é só limite superior).
_SHEET_RANGES = {
    IRegistrySource.PROFESSORES: "Professores!A2:F1000",
    IRegistrySource.ALUNOS: "Alunos!A2:K1000",
    IRegistrySource.TURMAS: "Turmas!A2:M1000",
    IRegistrySource.CHAVES: "Chaves!A2:F10000",
}


class GoogleDriveClient(IMaterialSource, IRegistrySource):
    def __init__(self):
        self.creds = None
        self.service = None
        self.sheets_service = None

        # Verify if service account file exists
        if not os.path.exists(settings.GOOGLE_SERVICE_ACCOUNT_FILE):
            logger.warning(f"Service account file not found: {settings.GOOGLE_SERVICE_ACCOUNT_FILE}")
            return

        try:
            self.creds = service_account.Credentials.from_service_account_file(
                settings.GOOGLE_SERVICE_ACCOUNT_FILE, scopes=SCOPES)
            self.service = build('drive', 'v3', credentials=self.creds)
            self.sheets_service = build('sheets', 'v4', credentials=self.creds)
            logger.info("Google Drive & Sheets API initialized via Service Account")
        except Exception as e:
            logger.error("Failed to initialize Google Drive Client", error=str(e))

    # ── IRegistrySource ─────────────────────────────────────────────────────
    async def read_table(self, source_id: str, table: str) -> list[list[str]]:
        """Lê as linhas de DADOS de uma tabela lógica (aba) do Google Sheets.

        `table` é o nome lógico ('Professores', 'Alunos', 'Turmas', 'Chaves');
        a tradução para o range A1 fica encapsulada aqui (detalhe do Sheets)."""
        if not self.sheets_service:
            raise ValueError("Google Sheets Service is not initialized.")

        range_name = _SHEET_RANGES.get(table, f"{table}!A2:ZZ100000")
        loop = asyncio.get_running_loop()

        def _fetch():
            sheet = self.sheets_service.spreadsheets()
            result = sheet.values().get(spreadsheetId=source_id, range=range_name).execute()
            return result.get('values', [])

        return await loop.run_in_executor(None, _fetch)

    # ── IMaterialSource ─────────────────────────────────────────────────────
    async def list_materials(self, root_id: str) -> list[MaterialFile]:
        """
        Lists multiformat files (PDF, DOCX, PPTX, TXT, MD) following the hierarchy:
        Root -> CourseFolder -> SubjectFolder -> [MaterialFolder] -> SectionFolder -> Files

        O nível MaterialFolder é OPCIONAL (Addon de Livros): pastas chamadas
        "Aulas", "Livro" ou "Apostila" dentro da Disciplina agrupam materiais
        por tipo. Pastas de seção soltas direto na Disciplina continuam sendo
        tratadas como aulas ('lesson'), preservando o fluxo antigo.
        """
        if not self.service:
            raise ValueError("Google Drive Service is not initialized.")

        loop = asyncio.get_running_loop()
        mime_query = " or ".join([f"mimeType='{m}'" for m in ALLOWED_MIMES])

        def _fetch() -> list[MaterialFile]:
            all_files: list[MaterialFile] = []

            def _list_folders(parent_id: str) -> list[dict]:
                query = f"'{parent_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
                result = self.service.files().list(q=query, fields="files(id, name)", spaces='drive').execute()
                return result.get('files', [])

            def _collect_section_files(section_folder: dict, c_name: str, s_name: str, mat_type: str):
                """Coleta os arquivos de uma pasta de seção/aula, rotulando o material."""
                section_num = extract_section_number(section_folder['name'])
                f_query = f"'{section_folder['id']}' in parents and ({mime_query}) and trashed=false"
                f_result = self.service.files().list(q=f_query, fields="files(id, name, mimeType, modifiedTime)", spaces='drive').execute()

                for f in f_result.get('files', []):
                    all_files.append(MaterialFile(
                        id=f['id'],
                        name=f['name'],
                        mime_type=f.get('mimeType', ''),
                        course_name=c_name,
                        subject_name=s_name,
                        # Para 'lesson' = número da aula; para 'book'/'workbook' =
                        # capítulo/módulo (mesma coluna, semântica por material).
                        lesson_number=section_num,
                        material_type=mat_type,
                        modified_time=f.get('modifiedTime'),
                    ))

            # 1. Fetch Course Folders inside Root
            for course in _list_folders(root_id):
                c_name = course['name']

                # 2. Fetch Subject Folders (Disciplinas) inside each Course Folder
                for subject in _list_folders(course['id']):
                    s_name = subject['name']

                    # 3. Children da Disciplina: ou agrupadores de material
                    #    (Aulas/Livro/Apostila), ou pastas de aula soltas (legado).
                    for child in _list_folders(subject['id']):
                        mat_type = material_type_for_folder(child['name'])

                        if mat_type:
                            # 3a. Agrupador: desce um nível para as seções
                            for section in _list_folders(child['id']):
                                _collect_section_files(section, c_name, s_name, mat_type)
                        else:
                            # 3b. Compatibilidade total: pasta de aula direto
                            # na Disciplina funciona exatamente como antes.
                            _collect_section_files(child, c_name, s_name, "lesson")

            return all_files

        return await loop.run_in_executor(None, _fetch)

    async def download(self, file_id: str) -> bytes:
        """
        Downloads a file from Google Drive directly into memory (bytes).
        """
        if not self.service:
            raise ValueError("Google Drive Service is not initialized.")

        loop = asyncio.get_running_loop()

        def _download():
            file_meta = self.service.files().get(fileId=file_id, fields="mimeType").execute()
            mime_type = file_meta.get("mimeType")

            if mime_type == "application/vnd.google-apps.document":
                request = self.service.files().export_media(fileId=file_id, mimeType="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
            elif mime_type == "application/vnd.google-apps.presentation":
                request = self.service.files().export_media(fileId=file_id, mimeType="application/vnd.openxmlformats-officedocument.presentationml.presentation")
            else:
                request = self.service.files().get_media(fileId=file_id)

            fh = io.BytesIO()
            downloader = MediaIoBaseDownload(fh, request)
            done = False
            while done is False:
                status, done = downloader.next_chunk()
            return fh.getvalue()

        return await loop.run_in_executor(None, _download)


# Singleton instance (a factory devolve esta instância quando o backend = gdrive).
drive_client = GoogleDriveClient()
