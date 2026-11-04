"""YouTube authentication, playlist management and resumable uploads."""
import pickle
import sys
import time
from pathlib import Path

import httplib2
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from .config import CLIENT_SECRETS_FILE, TOKEN_PICKLE_FILE
from .state import format_bytes, save_state

# Scopes required for uploading and managing playlists
SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube'
]

# HTTP status codes that are safe to retry
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def get_authenticated_service():
    """Handle OAuth 2.0 authentication and token caching"""
    credentials = None
    if TOKEN_PICKLE_FILE.exists():
        with open(TOKEN_PICKLE_FILE, 'rb') as token:
            credentials = pickle.load(token)

    if not credentials or not credentials.valid:
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        else:
            if not CLIENT_SECRETS_FILE.exists():
                sys.exit(f"[錯誤] 找不到憑證檔案 {CLIENT_SECRETS_FILE}，請確認檔案放在同一目錄。")
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_FILE), SCOPES)
            credentials = flow.run_local_server(port=0)

        with open(TOKEN_PICKLE_FILE, 'wb') as token:
            pickle.dump(credentials, token)

    return build('youtube', 'v3', credentials=credentials)


def get_or_create_playlist(youtube, playlist_name, privacy_status):
    """Look up the playlist and create a new one if it does not exist"""
    request = youtube.playlists().list(
        part="snippet",
        mine=True,
        maxResults=50
    )

    while request:
        response = request.execute()
        for item in response.get("items", []):
            if item["snippet"]["title"] == playlist_name:
                print(f"[清單就緒] 找到現有清單: {playlist_name} (ID: {item['id']})")
                return item["id"]
        request = youtube.playlists().list_next(request, response)

    # No playlist with the same name, so create one
    print(f"[建立清單] 建立新播放清單: {playlist_name}")
    insert_response = youtube.playlists().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": playlist_name,
                "description": f"由批量腳本自動建立: {playlist_name}"
            },
            "status": {
                "privacyStatus": privacy_status
            }
        }
    ).execute()
    return insert_response["id"]


def add_video_to_playlist(youtube, playlist_id, video_id):
    """Add an uploaded video to the playlist (skip if already present, to avoid duplicates on rerun)"""
    existing = youtube.playlistItems().list(
        part="snippet",
        playlistId=playlist_id,
        videoId=video_id,
        maxResults=1
    ).execute()
    if existing.get("items"):
        print(f"  -> 播放清單內已有此影片，略過")
        return

    body = {
        "snippet": {
            "playlistId": playlist_id,
            "resourceId": {
                "kind": "youtube#video",
                "videoId": video_id
            }
        }
    }
    youtube.playlistItems().insert(part="snippet", body=body).execute()
    print(f"  -> 已成功加入播放清單")


def upload_video(youtube, media_path, title, display_name, config, entry, state):
    """Upload one video file, resuming after an interruption if possible.

    entry is the per-part progress dict stored under the job's upload_state entry.
    """
    media_path = Path(media_path)
    video_title = title

    body = {
        'snippet': {
            'title': video_title,
            'description': '',
            'categoryId': '22'
        },
        'status': {
            'privacyStatus': config["privacy_status"],
            'selfDeclaredMadeForKids': config["made_for_kids"]
        }
    }

    media = MediaFileUpload(
        str(media_path),
        chunksize=config["chunk_size_mb"] * 1024 * 1024,
        resumable=True
    )
    request = youtube.videos().insert(part=','.join(body.keys()), body=body, media_body=media)

    # If a previous upload was unfinished, continue the same resumable session
    resume_uri = entry.get("resumable_uri")
    resume_bytes = int(entry.get("bytes_sent") or 0)
    if resume_uri and entry.get("upload_size") not in (None, media.size()):
        # The payload changed (e.g. the source was re-converted), so the old session cannot be resumed
        print("\n[續傳] 檔案大小與上次不同，改為重新上傳...")
        resume_uri = None
        resume_bytes = 0
        entry.pop("resumable_uri", None)
        entry["bytes_sent"] = 0
    entry["upload_size"] = media.size()

    if resume_uri:
        request.resumable_uri = resume_uri
        request.resumable_progress = resume_bytes
        # Mark as error state so next_chunk asks the server for the actual byte count first
        request._in_error_state = True
        print(f"\n[續傳] 發現未完成的上傳（約 {format_bytes(resume_bytes)}），嘗試由斷點繼續...")

    print(f"\n[上傳中] {display_name} ({format_bytes(media.size())})")
    print(f"  -> 權限: {config['privacy_status']} | 兒童導向: {config['made_for_kids']}")

    response = None
    retries = 0
    while response is None:
        try:
            status, response = request.next_chunk(num_retries=config["max_retries"])
            retries = 0
        except HttpError as e:
            code = e.resp.status
            # Upload session expired (usually after a week), so start over
            if code in (404, 410) and request.resumable_uri:
                print("\n  [續傳] 上傳工作階段已過期，改為重新上傳...")
                request.resumable_uri = None
                request.resumable_progress = 0
                request._in_error_state = False
                entry.pop("resumable_uri", None)
                entry["bytes_sent"] = 0
                save_state(state)
                continue
            if code in RETRYABLE_STATUS and retries < config["max_retries"]:
                retries += 1
                wait = min(2 ** retries, 60)
                print(f"\n  [重試] HTTP {code}，{wait} 秒後重試（第 {retries} 次）...")
                request._in_error_state = True
                time.sleep(wait)
                continue
            entry["resumable_uri"] = request.resumable_uri
            entry["bytes_sent"] = request.resumable_progress
            save_state(state)
            raise
        except (httplib2.HttpLib2Error, OSError) as e:
            # Temporary network drop: save progress and retry; the session is still valid
            if retries < config["max_retries"]:
                retries += 1
                wait = min(2 ** retries, 60)
                print(f"\n  [重試] 網絡錯誤（{e}），{wait} 秒後重試（第 {retries} 次）...")
                entry["resumable_uri"] = request.resumable_uri
                entry["bytes_sent"] = request.resumable_progress
                save_state(state)
                request._in_error_state = True
                time.sleep(wait)
                continue
            entry["resumable_uri"] = request.resumable_uri
            entry["bytes_sent"] = request.resumable_progress
            save_state(state)
            raise

        # Save progress after every chunk so a forced termination can still resume
        entry["resumable_uri"] = request.resumable_uri
        entry["bytes_sent"] = request.resumable_progress
        save_state(state)

        if status:
            total = media.size() or 0
            percent = int(status.progress() * 100)
            print(
                f"  進度: {percent}% ({format_bytes(request.resumable_progress)} / {format_bytes(total)})",
                end='\r'
            )

    video_id = response.get('id')
    entry.pop("resumable_uri", None)
    entry.update({
        "video_id": video_id,
        "playlist_added": False,
        "bytes_sent": media.size(),
        "upload_size": media.size(),
    })
    save_state(state)
    print(f"  [完成] 影片 ID: {video_id} -> https://youtu.be/{video_id}")
    return video_id
