import os
import io
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
        Root -> CourseFolder -> LessonFolder -> Files
        Returns a list of dicts with id, name, mimeType, modifiedTime, course_name, and lesson_number.
        """
        if not self.service:
            raise ValueError("Google Drive Service is not initialized.")
            
        import asyncio
        loop = asyncio.get_running_loop()
        import re
        
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
            
            # 1. Fetch Course Folders inside Root
            c_query = f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
            c_result = self.service.files().list(q=c_query, fields="files(id, name)", spaces='drive').execute()
            courses = c_result.get('files', [])
            
            for course in courses:
                c_name = course['name']
                c_id = course['id']
                
                # 2. Fetch Lesson Folders inside each Course Folder
                l_query = f"'{c_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
                l_result = self.service.files().list(q=l_query, fields="files(id, name)", spaces='drive').execute()
                lessons = l_result.get('files', [])
                
                for lesson in lessons:
                    l_name = lesson['name']
                    l_id = lesson['id']
                    
                    # Extract lesson number from folder name (ex: "1 - Introdução")
                    match = re.match(r"^(\d+)\s*-", l_name)
                    lesson_num = int(match.group(1)) if match else 1
                    
                    # 3. Fetch allowed files inside each Lesson Folder
                    f_query = f"'{l_id}' in parents and ({mime_query}) and trashed=false"
                    f_result = self.service.files().list(q=f_query, fields="files(id, name, mimeType, modifiedTime)", spaces='drive').execute()
                    
                    for f in f_result.get('files', []):
                        f['course_name'] = c_name
                        f['lesson_number'] = lesson_num
                        all_files.append(f)
                        
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
