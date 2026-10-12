import json, os, threading
from copy import deepcopy
from datetime import datetime, timezone
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io

_LOCK = threading.RLock()
SCOPES = ['https://www.googleapis.com/auth/drive']

class DriveStore:
    def __init__(self):
        raw = os.getenv('GOOGLE_SERVICE_ACCOUNT_JSON', '').strip()
        self.file_id = os.getenv('GOOGLE_DRIVE_FILE_ID', '').strip()
        self.folder_id = os.getenv('GOOGLE_DRIVE_FOLDER_ID', '').strip()
        self.file_name = os.getenv('GOOGLE_DRIVE_FILE_NAME', 'provider_fax_data.json').strip()
        if not raw:
            raise RuntimeError('GOOGLE_SERVICE_ACCOUNT_JSON is missing')
        info = json.loads(raw)
        creds = Credentials.from_service_account_info(info, scopes=SCOPES)
        self.service = build('drive', 'v3', credentials=creds, cache_discovery=False)
        if not self.file_id:
            self.file_id = self._find_existing_file()
        if not self.file_id:
            raise RuntimeError('Pre-create provider_fax_data.json in Google Drive and set GOOGLE_DRIVE_FILE_ID')

    def _find_existing_file(self):
        safe = self.file_name.replace("'", "\\'")
        q = f"name='{safe}' and trashed=false"
        if self.folder_id:
            q += f" and '{self.folder_id}' in parents"
        result = self.service.files().list(q=q, spaces='drive', fields='files(id,name)', pageSize=20).execute()
        files = result.get('files', [])
        return files[0]['id'] if files else ''

    def load(self):
        with _LOCK:
            content = self.service.files().get_media(fileId=self.file_id).execute()
            data = json.loads(content.decode('utf-8') or '{}')
            return normalize(data)

    def save(self, data):
        with _LOCK:
            payload = deepcopy(normalize(data))
            payload['updated_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
            body = json.dumps(payload, indent=2, ensure_ascii=False).encode('utf-8')
            media = MediaIoBaseUpload(io.BytesIO(body), mimetype='application/json', resumable=False)
            self.service.files().update(fileId=self.file_id, media_body=media, fields='id,modifiedTime').execute()
            return payload

def normalize(data):
    if not isinstance(data, dict): data = {}
    data.setdefault('version', 2)
    data.setdefault('practices', [])
    data.setdefault('providers', [])
    data.setdefault('review_queue', [])
    data.setdefault('audit', [])
    data.setdefault('updated_at', '')
    return data
