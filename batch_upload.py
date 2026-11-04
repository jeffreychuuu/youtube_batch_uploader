import hashlib
import json
import os
import pickle
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
import httplib2
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

# Keep all files in the script's directory so it works when run from any directory
BASE_DIR = Path(__file__).resolve().parent
CLIENT_SECRETS_FILE = BASE_DIR / 'client_secrets.json'
TOKEN_PICKLE_FILE = BASE_DIR / 'token.pickle'
STATE_FILE = BASE_DIR / 'upload_state.json'
CONFIG_FILE = BASE_DIR / 'config.json'
# Cache for MP4 conversions; safe to delete between runs
CONVERTED_DIR = BASE_DIR / '.converted'

# ==================== Default settings ====================
# Real settings go in config.json in the same directory (copy config.example.json and edit it)
DEFAULT_CONFIG = {
    "root_folder": "/path/to/your/videos",
    "playlist_name": "My Playlist",
    "privacy_status": "unlisted",
    "made_for_kids": False,
    "video_extensions": [".mp4", ".mov", ".mkv", ".avi", ".m4v", ".vob"],
    # Extensions converted to MP4 with ffmpeg before upload (VOB needs this;
    # add e.g. ".avi" here if you want every AVI transcoded for compatibility)
    "convert_extensions": [".vob"],
    "chunk_size_mb": 32,
    "max_retries": 5,
    # Whether to remove local video files after a successful upload
    "remove_local_after_upload": False,
    # Removal method: false = move to Trash (recoverable), true = delete permanently (irreversible)
    "remove_local_permanent": False,
}


def load_config():
    """Load config.json and merge it with the defaults; use defaults if config.json is missing"""
    config = dict(DEFAULT_CONFIG)
    if not CONFIG_FILE.exists():
        return config

    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            user_config = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        sys.exit(f"[錯誤] 無法讀取 {CONFIG_FILE.name}：{e}")

    if not isinstance(user_config, dict):
        sys.exit(f"[錯誤] {CONFIG_FILE.name} 格式錯誤，最外層必須係 JSON object。")

    unknown = sorted(set(user_config) - set(DEFAULT_CONFIG))
    if unknown:
        print(f"[警告] {CONFIG_FILE.name} 有未知設定項目，已忽略：{', '.join(unknown)}")

    config.update({k: v for k, v in user_config.items() if k in DEFAULT_CONFIG})

    if config["privacy_status"] not in ("public", "unlisted", "private"):
        sys.exit("[錯誤] privacy_status 只可以係 'public'、'unlisted' 或 'private'。")
    if not isinstance(config["video_extensions"], list):
        sys.exit("[錯誤] video_extensions 必須係 JSON 陣列，例如 [\".mp4\", \".mov\"]。")
    if not isinstance(config["convert_extensions"], list):
        sys.exit("[錯誤] convert_extensions 必須係 JSON 陣列，例如 [\".vob\"]。")
    if any(
        not isinstance(ext, str) or not ext.startswith('.')
        for ext in config["convert_extensions"]
    ):
        sys.exit("[錯誤] convert_extensions 每項都必須係以 '.' 開頭的字串，例如 \".vob\"。")
    for key in ("chunk_size_mb", "max_retries"):
        if isinstance(config[key], bool) or not isinstance(config[key], int):
            sys.exit(f"[錯誤] {key} 必須係整數。")
    for key in ("remove_local_after_upload", "remove_local_permanent"):
        if not isinstance(config[key], bool):
            sys.exit(f"[錯誤] {key} 必須係 true 或 false。")

    return config


CONFIG = load_config()

# Scopes required for uploading and managing playlists
SCOPES = [
    'https://www.googleapis.com/auth/youtube.upload',
    'https://www.googleapis.com/auth/youtube'
]

# HTTP status codes that are safe to retry
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Formats converted to MP4 before upload (user-configurable; VOB is the default)
CONVERT_EXTENSIONS = {ext.lower() for ext in CONFIG["convert_extensions"]}
# ffmpeg encoding settings for the conversion (quality vs. speed/size)
CONVERT_CRF = 20
CONVERT_PRESET = 'medium'
# DVD-style VOB segments: VTS_01_1.VOB, VTS_01_2.VOB, ...
VOB_SEGMENT_RE = re.compile(r'^(?P<prefix>.+)_(?P<index>\d+)$')
# ====================================================

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

# ==================== Upload progress tracking ====================

def load_state():
    """Load upload_state.json; start fresh if the file is corrupt, without affecting uploaded videos"""
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
    """Atomically write the progress file so a sudden interruption cannot corrupt it"""
    tmp = STATE_FILE.with_name(STATE_FILE.name + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)

def get_entry(state, file_path):
    return state["uploads"].setdefault(str(file_path.resolve()), {})

def source_fingerprint(files):
    """Aggregate size / latest mtime and a stable hash for one or more source files"""
    total_size = 0
    latest_mtime = 0.0
    digest = hashlib.sha1()
    for file_path in files:
        stat = file_path.stat()
        total_size += stat.st_size
        latest_mtime = max(latest_mtime, stat.st_mtime)
        digest.update(str(file_path.resolve()).encode('utf-8'))
        digest.update(b'\0')
        digest.update(str(stat.st_size).encode('ascii'))
        digest.update(b'\0')
        digest.update(str(stat.st_mtime_ns).encode('ascii'))
        digest.update(b'\0')
    return total_size, latest_mtime, digest.hexdigest()[:20]

def is_same_source(entry, files):
    """Check that the source files have not changed since upload"""
    try:
        total_size, latest_mtime, _ = source_fingerprint(files)
    except OSError:
        return False
    return (
        entry.get("size") == total_size
        and abs(float(entry.get("mtime", 0)) - latest_mtime) < 0.001
    )

def refresh_entry(entry, files):
    """Reset the entry when the source has changed"""
    total_size, latest_mtime, _ = source_fingerprint(files)
    entry.clear()
    entry.update({"size": total_size, "mtime": latest_mtime})

def format_bytes(num):
    num = float(num)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024 or unit == "TB":
            return f"{num:.1f} {unit}"
        num /= 1024

# ====================================================

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

def upload_video(youtube, job, config, state, media_path=None):
    """Upload a single video, resuming after an interruption if possible.

    job["files"] identifies the source (one file, or several VOB segments merged
    into one video). media_path lets the caller upload a converted copy while
    state tracking and the title stay tied to the original source.
    """
    files = job["files"]
    entry = get_entry(state, files[0])
    if not is_same_source(entry, files):
        refresh_entry(entry, files)
        save_state(state)

    media_path = Path(media_path) if media_path else Path(files[0])
    video_title = job["title"]

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

    converted_note = "（已轉檔為 MP4）" if job["convert"] else ""
    print(f"\n[上傳中] {job['display_name']} ({format_bytes(media.size())}){converted_note}")
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
    })
    save_state(state)
    print(f"  [完成] 影片 ID: {video_id} -> https://youtu.be/{video_id}")
    return video_id

def move_to_trash(file_path):
    """Move the file to the Trash (use send2trash if installed, otherwise Finder on macOS)"""
    try:
        from send2trash import send2trash
    except ImportError:
        send2trash = None

    if send2trash is not None:
        send2trash(str(file_path))
        return

    if sys.platform == 'darwin':
        # Pass the path via argv so quotes or special characters in file names do not break it
        result = subprocess.run(
            [
                'osascript',
                '-e', 'on run argv',
                '-e', 'tell application "Finder" to delete (POSIX file (item 1 of argv))',
                '-e', 'end run',
                str(file_path),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise OSError(result.stderr.strip() or 'osascript 無法移到垃圾桶')
        return

    raise OSError('未安裝 send2trash，無法移到垃圾桶（可執行 pip3 install send2trash）')

def remove_local_file(file_path, permanent):
    """Remove the local video file according to the settings"""
    if permanent:
        file_path.unlink()
        print(f"  [清理] 已永久刪除本機檔案: {file_path}")
    else:
        move_to_trash(file_path)
        print(f"  [清理] 已將本機檔案移到垃圾桶: {file_path}")

def converted_cache_path(fingerprint):
    """Deterministic cache path for a given source fingerprint"""
    return CONVERTED_DIR / f"{fingerprint}.mp4"

def write_concat_list(files, list_path):
    """Write an ffmpeg concat-demuxer list for multi-segment VOB input"""
    with open(list_path, 'w', encoding='utf-8') as f:
        f.write('ffconcat version 1.0\n')
        for file_path in files:
            escaped = str(file_path.resolve()).replace("'", "'\\''")
            f.write(f"file '{escaped}'\n")

def convert_to_mp4(files):
    """Merge (if needed) and convert source videos to MP4 (H.264/AAC) with ffmpeg.

    The output is cached in CONVERTED_DIR, so resuming an interrupted upload
    does not re-encode the file. The cache key covers the file paths, sizes
    and modification times, so any change produces a fresh conversion.
    """
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('找不到 ffmpeg，請先安裝（macOS: brew install ffmpeg）')

    try:
        _, _, fingerprint = source_fingerprint(files)
    except OSError as e:
        raise RuntimeError(f'無法讀取來源檔案: {e}')

    target = converted_cache_path(fingerprint)
    if target.exists():
        print(f"  [轉檔] 重用已轉換檔案: {target.name}")
        return target

    CONVERTED_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(target.name + '.part.mp4')

    if len(files) == 1:
        concat_path = None
        input_args = ['-i', str(files[0])]
        print(f"  [轉檔] {files[0].name} -> MP4（H.264/AAC），需時可能較長...")
    else:
        concat_path = target.with_name(target.name + '.concat.txt')
        write_concat_list(files, concat_path)
        input_args = ['-f', 'concat', '-safe', '0', '-i', str(concat_path)]
        print(f"  [轉檔] 合併 {len(files)} 段 VOB -> MP4（H.264/AAC），需時可能較長...")
        for file_path in files:
            print(f"    - {file_path.name}")

    cmd = [
        ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-stats',
        *input_args,
        # Keep the first video and audio streams only; VOB subtitles are not MP4-compatible
        '-map', '0:v:0', '-map', '0:a:0?', '-sn',
        # Deinterlace only frames flagged as interlaced (DVD content usually is)
        '-vf', 'yadif=deint=interlaced',
        '-c:v', 'libx264', '-preset', CONVERT_PRESET, '-crf', str(CONVERT_CRF),
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '192k',
        str(tmp_path),
    ]

    try:
        result = subprocess.run(cmd)
    except OSError as e:
        raise RuntimeError(f'無法執行 ffmpeg: {e}')
    finally:
        if concat_path is not None:
            try:
                concat_path.unlink()
            except OSError:
                pass

    if result.returncode != 0:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise RuntimeError(f'ffmpeg 轉檔失敗（exit code {result.returncode}）')

    os.replace(tmp_path, target)
    print(f"  [轉檔] 完成: {target.name}")
    return target

def remove_cached_conversion(files):
    """Delete the cached MP4 (and any partial output) for these source files"""
    try:
        _, _, fingerprint = source_fingerprint(files)
    except OSError:
        return
    target = converted_cache_path(fingerprint)
    for path in (
        target,
        target.with_name(target.name + '.part.mp4'),
        target.with_name(target.name + '.concat.txt'),
    ):
        try:
            path.unlink()
        except OSError:
            pass

def natural_sort_key(path):
    """Sort key that compares digit runs as numbers (2 before 10) and ignores case"""
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r'(\d+)', str(path))
    ]

def scan_video_files(folder_path, extensions):
    """Recursively find all video files in the given folder, in natural order"""
    folder = Path(folder_path)
    if not folder.exists() or not folder.is_dir():
        sys.exit(f"[錯誤] 資料夾不存在或非有效目錄: {folder_path}")

    matched_files = [
        f for f in folder.rglob("*")
        if f.is_file() and f.suffix.lower() in extensions
    ]
    return sorted(matched_files, key=natural_sort_key)

def folder_display_name(folder):
    """Video title for a folder; skip the generic VIDEO_TS level when present"""
    if folder.name.upper() == 'VIDEO_TS' and folder.parent != folder:
        return folder.parent.name
    return folder.name

def build_upload_jobs(video_files):
    """Turn scanned files into upload jobs, merging multi-segment VOB files.

    Non-VOB files are single-file jobs. VOB files named like VTS_01_1.VOB,
    VTS_01_2.VOB, ... are merged per title set (same folder and prefix), in
    numeric order, and titled after the folder. DVD menu files (*_0.VOB and
    VIDEO_TS.VOB) are skipped.
    """
    jobs = []
    groups = {}

    for path in video_files:
        suffix = path.suffix.lower()

        if suffix not in CONVERT_EXTENSIONS:
            # Upload this format as-is
            jobs.append({
                "files": [path],
                "title": path.stem,
                "display_name": path.name,
                "convert": False,
            })
            continue

        if suffix != '.vob':
            # Other formats can be transcoded individually when configured
            jobs.append({
                "files": [path],
                "title": path.stem,
                "display_name": path.name,
                "convert": True,
            })
            continue

        if path.name.upper() == 'VIDEO_TS.VOB':
            print(f"[略過] DVD 選單檔案: {path.name}")
            continue

        match = VOB_SEGMENT_RE.match(path.stem)
        if not match:
            # Standalone VOB (e.g. a single-file rip)
            jobs.append({
                "files": [path],
                "title": path.stem,
                "display_name": path.name,
                "convert": True,
            })
            continue

        index = int(match.group('index'))
        if index == 0:
            print(f"[略過] DVD 選單檔案: {path.name}")
            continue

        group_key = (str(path.parent.resolve()), match.group('prefix').lower())
        group = groups.get(group_key)
        if group is None:
            group = {"folder": path.parent, "segments": []}
            groups[group_key] = group
            jobs.append(group)
        group["segments"].append((index, path))

    folder_counts = {}
    for group in groups.values():
        folder = group["folder"]
        folder_counts[folder] = folder_counts.get(folder, 0) + 1

    for group in groups.values():
        group["segments"].sort(key=lambda item: item[0])
        files = [path for _, path in group["segments"]]
        folder = group["folder"]
        merged = len(files) > 1

        indexes = [index for index, _ in group["segments"]]
        if indexes != list(range(1, len(indexes) + 1)):
            print(
                f"[警告] {folder.name} 的 VOB 分段不連續：{indexes}"
                "（可能未複製完成，請確認後再上傳）"
            )

        title = folder_display_name(folder) if merged else files[0].stem
        if merged and folder_counts[folder] > 1:
            # Multiple title sets in one folder: keep the titles distinct
            prefix = VOB_SEGMENT_RE.match(files[0].stem).group('prefix')
            title = f"{title} ({prefix})"

        group["files"] = files
        group["title"] = title
        group["display_name"] = (
            f"{title}（{len(files)} 段 VOB 合併）" if merged else files[0].name
        )
        group["convert"] = True
        group.pop("segments", None)
        group.pop("folder", None)

    return jobs

def main():
    chunk_bytes = CONFIG["chunk_size_mb"] * 1024 * 1024
    if chunk_bytes % (256 * 1024) != 0:
        sys.exit("[錯誤] chunk_size_mb 換算後必須是 256KB 的整數倍。")

    video_files = scan_video_files(CONFIG["root_folder"], CONFIG["video_extensions"])
    jobs = build_upload_jobs(video_files)
    if not jobs:
        print(f"[提示] 在 {CONFIG['root_folder']} 內找不到相容的影片檔案。")
        return

    print(
        f"[掃描完成] 共找到 {len(video_files)} 個影片檔案，"
        f"合併為 {len(jobs)} 個上傳項目。"
    )
    if any(job["convert"] for job in jobs) and not shutil.which('ffmpeg'):
        print(
            "[警告] 找不到 ffmpeg，需要轉檔的影片（例如 VOB）會被跳過。\n"
            "        請先安裝：brew install ffmpeg（macOS）"
        )
    state = load_state()
    youtube = get_authenticated_service()

    # Get or create the playlist ID
    playlist_id = None
    if CONFIG["playlist_name"]:
        playlist_id = get_or_create_playlist(
            youtube,
            CONFIG["playlist_name"],
            CONFIG["privacy_status"]
        )

    # Upload each video and add it to the playlist
    for idx, job in enumerate(jobs, start=1):
        files = job["files"]
        print(f"\n處理第 [{idx}/{len(jobs)}] 部影片...")
        key = str(files[0].resolve())
        entry = state["uploads"].get(key)
        try:
            if entry and entry.get("video_id") and is_same_source(entry, files):
                video_id = entry["video_id"]
                print(f"[略過] 已上傳過：{job['display_name']} (ID: {video_id})")
                if job["convert"]:
                    remove_cached_conversion(files)
            else:
                if entry and not is_same_source(entry, files):
                    print(f"[變更] 檔案已修改，重新上傳：{job['display_name']}")
                    if job["convert"]:
                        remove_cached_conversion(files)
                media_path = files[0]
                if job["convert"]:
                    media_path = convert_to_mp4(files)
                video_id = upload_video(youtube, job, CONFIG, state, media_path=media_path)
                entry = state["uploads"][key]
                # The converted copy is no longer needed once the upload has completed
                if job["convert"]:
                    remove_cached_conversion(files)

            if video_id and playlist_id and not entry.get("playlist_added"):
                add_video_to_playlist(youtube, playlist_id, video_id)
                entry["playlist_added"] = True
                save_state(state)

            # Remove local files only after a successful upload (or if uploaded before)
            if (
                video_id
                and CONFIG["remove_local_after_upload"]
                and not entry.get("local_removed")
            ):
                all_removed = True
                for source_file in files:
                    if not source_file.exists():
                        continue
                    try:
                        remove_local_file(source_file, CONFIG["remove_local_permanent"])
                    except Exception as e:
                        print(f"  [警告] 無法移除本機檔案 {source_file.name}: {e}")
                        all_removed = False
                if all_removed:
                    entry["local_removed"] = True
                    save_state(state)
        except KeyboardInterrupt:
            print("\n\n[已中斷] 進度已儲存，下次執行會自動續傳。")
            return
        except HttpError as e:
            print(f"  [API 錯誤] 無法上傳 {job['display_name']}: {e}")
            # Stop trying once the quota is exhausted
            if e.resp.status == 403 and b'quotaExceeded' in (e.content or b''):
                print("  [停止] 已超出 YouTube API 配額，請稍後再執行（進度已儲存）。")
                return
        except Exception as e:
            print(f"  [例外錯誤] {job['display_name']}: {e}")

if __name__ == '__main__':
    main()
