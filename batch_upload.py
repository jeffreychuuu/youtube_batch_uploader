import json
import os
import pickle
import sys
import time
from pathlib import Path
import httplib2
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

# ==================== 使用者設定區 ====================
CONFIG = {
    # 本機存放影片的 Root Folder 路徑（請改成你自己的路徑）
    "root_folder": "/path/to/your/videos",

    # 目標播放清單名稱（若 YouTube 上不存在會自動建立）
    "playlist_name": "My Playlist",

    # 預設分享權限: 'public' (公開), 'unlisted' (非公開), 'private' (私人)
    "privacy_status": "unlisted",

    # 觀眾受眾: False (否，非兒童導向內容), True (是，設定為兒童內容)
    "made_for_kids": False,

    # 支援掃描的影片副檔名
    "video_extensions": [".mp4", ".mov", ".mkv", ".avi", ".m4v"],

    # 每次上傳的分塊大小（MB）。必須是 256KB 的整數倍；大檔案建議 16-32
    "chunk_size_mb": 32,

    # 遇到網絡斷線或 5xx 錯誤時，每個分塊最多重試次數
    "max_retries": 5,
}

# 所有檔案都放在腳本所在目錄，避免由其他目錄執行時找不到
BASE_DIR = Path(__file__).resolve().parent
CLIENT_SECRETS_FILE = BASE_DIR / 'client_secrets.json'
TOKEN_PICKLE_FILE = BASE_DIR / 'token.pickle'
STATE_FILE = BASE_DIR / 'upload_state.json'

# 包含上傳與管理播放清單所需權限
SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube'
]

# 可以安全重試的 HTTP 狀態碼
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
# ====================================================

def get_authenticated_service():
    """處理 OAuth 2.0 驗證與 Token 快取"""
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

# ==================== 上傳進度記錄 ====================

def load_state():
    """讀取 upload_state.json；檔案損壞時重新開始，不影響已上傳的影片"""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                state = json.load(f)
            if isinstance(state.get("uploads"), dict):
                return state
        except (json.JSONDecodeError, OSError) as e:
            print(f"[警告] 進度檔 {STATE_FILE.name} 無法讀取（{e}），將重新開始記錄。")
    return {"uploads": {}}

def save_state(state):
    """原子寫入進度檔，突然中斷都唔會寫壞"""
    tmp = STATE_FILE.with_name(STATE_FILE.name + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)

def get_entry(state, file_path):
    return state["uploads"].setdefault(str(file_path.resolve()), {})

def is_same_file(entry, file_path):
    """確認檔案自上傳後冇被修改過（大小與修改時間一致）"""
    try:
        stat = file_path.stat()
    except OSError:
        return False
    return (
        entry.get("size") == stat.st_size
        and abs(float(entry.get("mtime", 0)) - stat.st_mtime) < 0.001
    )

def refresh_entry(entry, file_path):
    """檔案有變動時重設記錄"""
    stat = file_path.stat()
    entry.clear()
    entry.update({"size": stat.st_size, "mtime": stat.st_mtime})

def format_bytes(num):
    num = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return f"{num:.1f} {unit}"
        num /= 1024

# ====================================================

def get_or_create_playlist(youtube, playlist_name, privacy_status):
    """查詢播放清單，若不存在則建立新清單"""
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

    # 找不到同名清單，自動建立
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
    """將上傳完成的影片加入播放清單（已存在就跳過，避免重跑時重複）"""
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

def upload_video(youtube, file_path, config, state):
    """執行單一影片上傳，支援中斷後續傳"""
    entry = get_entry(state, file_path)
    if not is_same_file(entry, file_path):
        refresh_entry(entry, file_path)
        save_state(state)

    video_title = Path(file_path).stem  # 預設以檔案名稱（不含副檔名）作為影片標題

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
        str(file_path),
        chunksize=config["chunk_size_mb"] * 1024 * 1024,
        resumable=True
    )
    request = youtube.videos().insert(part=','.join(body.keys()), body=body, media_body=media)

    # 若上次有未完成的上傳，接住同一個 resumable session
    resume_uri = entry.get("resumable_uri")
    resume_bytes = int(entry.get("bytes_sent") or 0)
    if resume_uri:
        request.resumable_uri = resume_uri
        request.resumable_progress = resume_bytes
        # 標記為錯誤狀態，next_chunk 會先向伺服器查詢實際已接收的位元組數
        request._in_error_state = True
        print(f"\n[續傳] 發現未完成的上傳（約 {format_bytes(resume_bytes)}），嘗試由斷點繼續...")

    print(f"\n[上傳中] {video_title} ({file_path.name}, {format_bytes(media.size())})")
    print(f"  -> 權限: {config['privacy_status']} | 兒童導向: {config['made_for_kids']}")

    response = None
    retries = 0
    while response is None:
        try:
            status, response = request.next_chunk(num_retries=config["max_retries"])
            retries = 0
        except HttpError as e:
            code = e.resp.status
            # 上傳 session 過期（通常超過一星期）→ 重新開始
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
            # 網絡暫時斷線 → 儲存進度後重試，session 仍然有效
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

        # 每個分塊完成後都記錄進度，強制終止都可以續傳
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
    })
    save_state(state)
    print(f"  [完成] 影片 ID: {video_id} -> https://youtu.be/{video_id}")
    return video_id

def scan_video_files(folder_path, extensions):
    """遞迴搜尋指定資料夾中的所有影片檔案"""
    folder = Path(folder_path)
    if not folder.exists() or not folder.is_dir():
        sys.exit(f"[錯誤] 資料夾不存在或非有效目錄: {folder_path}")

    matched_files = [
        f for f in folder.rglob("*")
        if f.is_file() and f.suffix.lower() in extensions
    ]
    return sorted(matched_files)

def main():
    chunk_bytes = CONFIG["chunk_size_mb"] * 1024 * 1024
    if chunk_bytes % (256 * 1024) != 0:
        sys.exit("[錯誤] chunk_size_mb 換算後必須是 256KB 的整數倍。")

    video_files = scan_video_files(CONFIG["root_folder"], CONFIG["video_extensions"])
    if not video_files:
        print(f"[提示] 在 {CONFIG['root_folder']} 內找不到相容的影片檔案。")
        return

    print(f"[掃描完成] 共找到 {len(video_files)} 個影片檔案待處理。")
    state = load_state()
    youtube = get_authenticated_service()

    # 取得或建立播放清單 ID
    playlist_id = None
    if CONFIG["playlist_name"]:
        playlist_id = get_or_create_playlist(
            youtube,
            CONFIG["playlist_name"],
            CONFIG["privacy_status"]
        )

    # 逐一上傳並歸入清單
    for idx, video_path in enumerate(video_files, start=1):
        print(f"\n處理第 [{idx}/{len(video_files)}] 部影片...")
        key = str(video_path.resolve())
        entry = state["uploads"].get(key)
        try:
            if entry and entry.get("video_id") and is_same_file(entry, video_path):
                video_id = entry["video_id"]
                print(f"[略過] 已上傳過：{video_path.name} (ID: {video_id})")
            else:
                if entry and not is_same_file(entry, video_path):
                    print(f"[變更] 檔案已修改，重新上傳：{video_path.name}")
                video_id = upload_video(youtube, video_path, CONFIG, state)
                entry = state["uploads"][key]

            if video_id and playlist_id and not entry.get("playlist_added"):
                add_video_to_playlist(youtube, playlist_id, video_id)
                entry["playlist_added"] = True
                save_state(state)
        except KeyboardInterrupt:
            print("\n\n[已中斷] 進度已儲存，下次執行會自動續傳。")
            return
        except HttpError as e:
            print(f"  [API 錯誤] 無法上傳 {video_path.name}: {e}")
            # 配額用完就唔好再試落去
            if e.resp.status == 403 and b'quotaExceeded' in (e.content or b''):
                print("  [停止] 已超出 YouTube API 配額，請稍後再執行（進度已儲存）。")
                return
        except Exception as e:
            print(f"  [例外錯誤] {video_path.name}: {e}")

if __name__ == '__main__':
    main()
