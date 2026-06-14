import os
import io
import re as _re
import structlog
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from src.shared.config import settings

logger = structlog.get_logger(__name__)

# Scopes needed for downloading files and reading spreadsheets
SCOPES = [
    'https://www.googleapis.com/auth/drive.readonly',
    'https://www.googleapis.com/auth/spreadsheets.readonly'
]

# ── Addon de Livros: tipos de material por pasta ────────────────────────────
# Dentro da pasta da Disciplina, pastas com estes nomes agrupam materiais de
# tipos diferentes (Curso > Disciplina > {Aulas | Livro | Apostila} > Seções).
# Pastas de aula soltas direto na Disciplina continuam valendo como 'lesson'
# (compatibilidade total com a estrutura antiga).
MATERIAL_TYPE_FOLDERS = {
    "aulas": "lesson",
    "aula": "lesson",
    "livro": "book",
    "livros": "book",
    "apostila": "workbook",
    "apostilas": "workbook",
}

# "1 - Introdução" (formato legado das aulas) tem precedência; senão, o
# primeiro número do nome ("Cap 3", "Capítulo 3", "Módulo 2") vale como seção.
_LEGACY_SECTION_REGEX = _re.compile(r"^(\d+)\s*-")
_ANY_NUMBER_REGEX = _re.compile(r"(\d+)")


def material_type_for_folder(folder_name: str) -> str | None:
    """Mapeia o nome de uma pasta para um material_type ('lesson' | 'book' |
    'workbook'). None se a pasta não for um agrupador de material conhecido."""
    return MATERIAL_TYPE_FOLDERS.get(folder_name.strip().lower())


def extract_section_number(folder_name: str) -> int:
    """Extrai o número da seção/aula/capítulo do nome da pasta.

    Exemplos: "1 - Introdução" → 1, "Cap 3" → 3, "Módulo 12" → 12.
    Default 1 quando não há número (mesmo comportamento legado).
    """
    match = _LEGACY_SECTION_REGEX.match(folder_name)
    if not match:
        match = _ANY_NUMBER_REGEX.search(folder_name)
    return int(match.group(1)) if match else 1

class GoogleDriveClient:
    def __init__(self):
        self.creds = None
        self.service = None
        
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

    async def get_spreadsheet_data(self, spreadsheet_id: str, range_name: str = "A1:H1000") -> list[list]:
        """
        Reads data from a Google Spreadsheet.
        Returns a list of rows, where each row is a list of strings.
        """
        if not self.sheets_service:
            raise ValueError("Google Sheets Service is not initialized.")
            
        import asyncio
        loop = asyncio.get_running_loop()
        
        def _fetch():
            sheet = self.sheets_service.spreadsheets()
            result = sheet.values().get(spreadsheetId=spreadsheet_id, range=range_name).execute()
            return result.get('values', [])
            
        values = await loop.run_in_executor(None, _fetch)
        return values

    async def list_course_files(self, folder_id: str):
        """
        Lists multiformat files (PDF, DOCX, PPTX, TXT, MD) following the hierarchy:
        Root -> CourseFolder -> SubjectFolder -> [MaterialFolder] -> SectionFolder -> Files

        O nível MaterialFolder é OPCIONAL (Addon de Livros): pastas chamadas
        "Aulas", "Livro" ou "Apostila" dentro da Disciplina agrupam materiais
        por tipo. Pastas de seção soltas direto na Disciplina continuam sendo
        tratadas como aulas ('lesson'), preservando o fluxo antigo.

        Returns a list of dicts with id, name, mimeType, modifiedTime,
        course_name, subject_name, lesson_number and material_type
        ('lesson' | 'book' | 'workbook').
        """
        if not self.service:
            raise ValueError("Google Drive Service is not initialized.")

        import asyncio
        loop = asyncio.get_running_loop()

        ALLOWED_MIMES = [
            "application/pdf",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            "text/plain",
            "text/markdown",
            "application/vnd.google-apps.document",
            "application/vnd.google-apps.presentation"
        ]
        mime_query = " or ".join([f"mimeType='{m}'" for m in ALLOWED_MIMES])

        def _fetch():
            all_files = []

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
                    f['course_name'] = c_name
                    f['subject_name'] = s_name
                    # Para 'lesson' = número da aula; para 'book'/'workbook' =
                    # capítulo/módulo (mesma coluna, semântica por material).
                    f['lesson_number'] = section_num
                    f['material_type'] = mat_type
                    all_files.append(f)

            # 1. Fetch Course Folders inside Root
            for course in _list_folders(folder_id):
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

        files = await loop.run_in_executor(None, _fetch)
        return files

    async def download_file(self, file_id: str) -> bytes:
        """
        Downloads a file from Google Drive directly into memory (bytes).
        """
        if not self.service:
            raise ValueError("Google Drive Service is not initialized.")
            
        import asyncio
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
            
        file_bytes = await loop.run_in_executor(None, _download)
        return file_bytes

# Singleton instance
drive_client = GoogleDriveClient()
