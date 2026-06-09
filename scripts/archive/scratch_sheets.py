import asyncio
from src.drive.client import drive_client

async def main():
    service = drive_client.sheets_service
    from src.shared.config import settings
    sheet_id = settings.GOOGLE_SHEET_ID
    
    result = service.spreadsheets().values().get(spreadsheetId=sheet_id, range="Alunos!A2:J").execute()
    rows = result.get('values', [])
    for idx, row in enumerate(rows):
        print(f"Row {idx}: {row}")

asyncio.run(main())
