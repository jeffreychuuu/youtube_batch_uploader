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
CUTS_FILE = BASE_DIR / 'cuts.json'
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
    # Split long videos into parts of this many hours (0 = never split)
    "part_duration_hours": 1,
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
    part_hours = config["part_duration_hours"]
    if isinstance(part_hours, bool) or not isinstance(part_hours, (int, float)):
        sys.exit("[錯誤] part_duration_hours 必須係數字（例如 1 表示每 1 小時一段）。")
    if not 0 <= part_hours <= 12:
        sys.exit("[錯誤] part_duration_hours 必須介乎 0 至 12（0 = 不分割）。")
    for key in ("chunk_size_mb", "max_retries"):
        if isinstance(config[key], bool) or not isinstance(config[key], int):
            sys.exit(f"[錯誤] {key} 必須係整數。")
    for key in ("remove_local_after_upload", "remove_local_permanent"):
        if not isinstance(config[key], bool):
            sys.exit(f"[錯誤] {key} 必須係 true 或 false。")

    return config


def parse_time(text):
    """Parse 'MM:SS', 'HH:MM:SS' or plain seconds into seconds"""
    parts = text.strip().split(':')
    if not 1 <= len(parts) <= 3 or any(not part.isdigit() for part in parts):
        raise ValueError(f'無效時間: {text}')
    values = [int(part) for part in parts]
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        minutes, seconds = values
        if seconds >= 60:
            raise ValueError(f'無效時間: {text}')
        return minutes * 60 + seconds
    hours, minutes, seconds = values
    if minutes >= 60 or seconds >= 60:
        raise ValueError(f'無效時間: {text}')
    return hours * 3600 + minutes * 60 + seconds

def normalise_cuts(cuts):
    """Sort and merge overlapping/adjacent cut ranges"""
    merged = []
    for start, end in sorted(cuts):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged

def parse_cut_ranges(items, key):
    """Parse a list of 'start-end' strings from cuts.json"""
    if not isinstance(items, list):
        sys.exit(f"[錯誤] {CUTS_FILE.name} 內 \"{key}\" 必須係時間範圍陣列。")

    ranges = []
    for item in items:
        if not isinstance(item, str):
            sys.exit(f"[錯誤] {CUTS_FILE.name} 內 \"{key}\" 嘅每個範圍都必須係字串。")
        parts = re.split(r'\s*(?:-|–|—|~|\bto\b)\s*', item.strip(), maxsplit=1)
        if len(parts) != 2:
            sys.exit(f"[錯誤] {CUTS_FILE.name} 內無法解析時間範圍: {item!r}")
        try:
            start = parse_time(parts[0])
            end = parse_time(parts[1])
        except ValueError:
            sys.exit(f"[錯誤] {CUTS_FILE.name} 內無法解析時間範圍: {item!r}")
        if end <= start:
            sys.exit(f"[錯誤] {CUTS_FILE.name} 內範圍結束時間必須大於開始時間: {item!r}")
        ranges.append((start, end))
    return normalise_cuts(ranges)

def load_cuts():
    """Load cuts.json; return an empty mapping if it does not exist"""
    if not CUTS_FILE.exists():
        return {}
    try:
        with open(CUTS_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        sys.exit(f"[錯誤] 無法讀取 {CUTS_FILE.name}：{e}")
    if not isinstance(data, dict):
        sys.exit(f"[錯誤] {CUTS_FILE.name} 格式錯誤，最外層必須係 JSON object。")
    return {key: parse_cut_ranges(items, key) for key, items in data.items()}

def part_cut_ranges(cuts_map, job, used_keys=None):
    """Convert '<title> [part N]' cut entries to the full video timeline"""
    part_hours = CONFIG["part_duration_hours"]
    if part_hours <= 0:
        return []
    prefix = f"{job['title']} [part "
    segment_seconds = max(1, int(part_hours * 3600) - 1)
    ranges = []
    for key, part_ranges in cuts_map.items():
        if not (key.startswith(prefix) and key.endswith(']')):
            continue
        number_text = key[len(prefix):-1].strip()
        if not number_text.isdigit() or int(number_text) < 1:
            continue
        offset = (int(number_text) - 1) * segment_seconds
        ranges.extend((start + offset, end + offset) for start, end in part_ranges)
        if used_keys is not None:
            used_keys.add(key)
    return ranges

def get_job_cuts(cuts_map, job, used_keys=None):
    """Look up full-timeline cuts and part-relative cuts for a job"""
    ranges = []
    for key in (job["title"], str(job["files"][0].resolve())):
        if key in cuts_map:
            ranges.extend(cuts_map[key])
            if used_keys is not None:
                used_keys.add(key)
    ranges.extend(part_cut_ranges(cuts_map, job, used_keys))
    return normalise_cuts(ranges)

def apply_entry_settings(entry, part_hours, cuts):
    """Store the current split/cut settings on a job entry"""
    entry["part_duration_hours"] = part_hours
    entry["cuts"] = cuts_for_state(cuts)

def print_old_video_links(entry):
    """Warn about existing YouTube videos before a re-upload"""
    old_ids = []
    if entry.get("video_id"):
        old_ids.append(entry["video_id"])
    for part in entry.get("parts", []) or []:
        video_id = part.get("video_id")
        if video_id and video_id not in old_ids:
            old_ids.append(video_id)
    if old_ids:
        print("  [提示] 舊版本仍然喺 YouTube，確認新片上傳成功後請手動刪除：")
        for video_id in old_ids:
            print(f"    - https://youtu.be/{video_id}")

def cuts_variant(cuts):
    """Stable string used in the conversion cache key"""
    return json.dumps([[start, end] for start, end in normalise_cuts(cuts)],
                      separators=(',', ':'))

def cuts_for_state(cuts):
    """JSON-serialisable cut ranges stored in upload_state.json"""
    return [[start, end] for start, end in normalise_cuts(cuts)]

def format_timestamp(seconds):
    """Format seconds as H:MM:SS or M:SS for logs"""
    seconds = int(round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


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
# Bump when the conversion pipeline changes so old caches are not reused
CONVERT_VERSION = 2
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

def source_fingerprint(files, variant=''):
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
    if variant:
        digest.update(variant.encode('utf-8'))
    return total_size, latest_mtime, digest.hexdigest()[:20]

def conversion_fingerprint(files, part_duration_hours=None, cuts=None):
    """Cache key for a conversion; includes part length and (when present) cut ranges"""
    if part_duration_hours is None:
        part_duration_hours = CONFIG["part_duration_hours"]
    variant = f'v{CONVERT_VERSION};part={part_duration_hours}'
    normalised = normalise_cuts(cuts or [])
    if normalised:
        variant += f';cuts={cuts_variant(normalised)}'
    _, _, fingerprint = source_fingerprint(files, variant=variant)
    return fingerprint

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
    """Single-file cache path for a given conversion fingerprint"""
    return CONVERTED_DIR / f"{fingerprint}.mp4"

def converted_marker_path(fingerprint):
    """Marker file written only after a conversion has completed"""
    return CONVERTED_DIR / f"{fingerprint}.done"

def converted_partial_dir(fingerprint):
    """Temporary directory used while a split conversion is running"""
    return CONVERTED_DIR / f"{fingerprint}.partial"

def load_cached_parts(fingerprint):
    """Return the cached part paths if a complete conversion exists, else None"""
    marker = converted_marker_path(fingerprint)
    try:
        count = int(marker.read_text(encoding='utf-8').strip())
    except (OSError, ValueError):
        return None
    if count < 1:
        return None
    if count == 1:
        path = converted_cache_path(fingerprint)
        return [path] if path.exists() else None
    paths = [
        CONVERTED_DIR / f"{fingerprint}.part{index:03d}.mp4"
        for index in range(1, count + 1)
    ]
    return paths if all(path.exists() for path in paths) else None

def remove_cache_by_fingerprint(fingerprint):
    """Delete every cached artifact belonging to one conversion fingerprint"""
    paths = [
        converted_cache_path(fingerprint),
        converted_marker_path(fingerprint),
        CONVERTED_DIR / f"{fingerprint}.done.tmp",
        CONVERTED_DIR / f"{fingerprint}.part.mp4",
        CONVERTED_DIR / f"{fingerprint}.concat.txt",
    ]
    paths.extend(CONVERTED_DIR.glob(f"{fingerprint}.part*.mp4"))
    for path in paths:
        try:
            path.unlink()
        except OSError:
            pass
    shutil.rmtree(converted_partial_dir(fingerprint), ignore_errors=True)

def write_cache_marker(fingerprint, count):
    """Atomically record how many parts a completed conversion has"""
    marker = converted_marker_path(fingerprint)
    tmp = marker.with_name(marker.name + '.tmp')
    tmp.write_text(str(count), encoding='utf-8')
    os.replace(tmp, marker)

def ffprobe_value(input_args, entries, ffprobe):
    """Read the first value from ffprobe for the given input"""
    result = subprocess.run(
        [
            ffprobe, '-v', 'error', *input_args,
            '-show_entries', entries,
            '-of', 'default=nw=1:nk=1',
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    lines = result.stdout.strip().splitlines()
    return lines[0].strip() if lines else None

def probe_frame_rate(files, ffprobe):
    """Get a usable frame-rate expression (e.g. '25/1') from the first valid input"""
    for file_path in files:
        value = ffprobe_value(['-i', str(file_path)], 'stream=r_frame_rate', ffprobe)
        if not value:
            continue
        try:
            num, den = value.split('/')
            if float(num) > 0 and float(den) > 0:
                return value
        except (ValueError, ZeroDivisionError):
            continue
    return None

def probe_sample_rate(files, ffprobe):
    """Get the audio sample rate from the first valid input"""
    for file_path in files:
        value = ffprobe_value(['-i', str(file_path)], 'stream=sample_rate', ffprobe)
        if value and value.isdigit() and int(value) > 0:
            return int(value)
    return None

def input_has_audio(input_args, ffprobe):
    """Check whether an input has an audio stream"""
    result = subprocess.run(
        [
            ffprobe, '-v', 'error', *input_args,
            '-select_streams', 'a:0',
            '-show_entries', 'stream=index',
            '-of', 'csv=p=0',
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f'ffprobe 無法讀取音訊資訊: {result.stderr.strip()}')
    return bool(result.stdout.strip())

def build_filter_graph(files, cuts, has_audio, fps_expr, sample_rate):
    """Re-time every input, merge the files, then remove the cut ranges.

    Re-timing with setpts/asetpts based on frame/sample counts ignores broken
    DVD timestamps (some VOBs jump to ~24 hours), which otherwise make ffmpeg
    duplicate frames and produce hours of frozen video.
    """
    graph = []
    count = len(files)
    for index in range(count):
        graph.append(f'[{index}:v:0]setpts=N/(({fps_expr})*TB)[nv{index}]')
        if has_audio:
            graph.append(f'[{index}:a:0]asetpts=N/({sample_rate}*TB)[na{index}]')

    if count > 1:
        if has_audio:
            inputs = ''.join(f'[nv{i}][na{i}]' for i in range(count))
            graph.append(f'{inputs}concat=n={count}:v=1:a=1[sv][sa]')
            sv, sa = 'sv', 'sa'
        else:
            inputs = ''.join(f'[nv{i}]' for i in range(count))
            graph.append(f'{inputs}concat=n={count}:v=1:a=0[sv]')
            sv, sa = 'sv', None
    else:
        sv = 'nv0'
        sa = 'na0' if has_audio else None

    cuts = normalise_cuts(cuts or [])
    if cuts:
        keep = []
        previous = 0
        for start, end in cuts:
            if start > previous:
                keep.append((previous, start))
            previous = max(previous, end)
        keep.append((previous, None))
        keep = [(start, end) for start, end in keep if end is None or end > start]
        if not keep:
            raise ValueError('cut 範圍覆蓋咗整段影片')

        if len(keep) > 1:
            graph.append(
                f'[{sv}]split={len(keep)}'
                + ''.join(f'[sv{i}]' for i in range(len(keep)))
            )
            if has_audio:
                graph.append(
                    f'[{sa}]asplit={len(keep)}'
                    + ''.join(f'[sa{i}]' for i in range(len(keep)))
                )
            sv_inputs = [f'sv{i}' for i in range(len(keep))]
            sa_inputs = [f'sa{i}' for i in range(len(keep))]
        else:
            sv_inputs = [sv]
            sa_inputs = [sa]

        for index, (start, end) in enumerate(keep):
            end_arg = '' if end is None else f':end={end}'
            graph.append(
                f'[{sv_inputs[index]}]trim=start={start}{end_arg},setpts=PTS-STARTPTS[cv{index}]'
            )
            if has_audio:
                graph.append(
                    f'[{sa_inputs[index]}]atrim=start={start}{end_arg},asetpts=PTS-STARTPTS[ca{index}]'
                )
        if has_audio:
            labels = ''.join(f'[cv{i}][ca{i}]' for i in range(len(keep)))
            graph.append(f'{labels}concat=n={len(keep)}:v=1:a=1[vcat][acat]')
            graph.append('[vcat]yadif=deint=interlaced[vout]')
            map_args = ['-map', '[vout]', '-map', '[acat]']
        else:
            labels = ''.join(f'[cv{i}]' for i in range(len(keep)))
            graph.append(f'{labels}concat=n={len(keep)}:v=1:a=0[vcat]')
            graph.append('[vcat]yadif=deint=interlaced[vout]')
            map_args = ['-map', '[vout]']
    else:
        graph.append(f'[{sv}]yadif=deint=interlaced[vout]')
        if has_audio:
            map_args = ['-map', '[vout]', '-map', f'[{sa}]']
        else:
            map_args = ['-map', '[vout]']

    return ';'.join(graph), map_args

def convert_to_mp4(files, cuts=None):
    """Merge (if needed) and convert source videos to MP4 (H.264/AAC) with ffmpeg.

    Long videos are split into parts according to part_duration_hours, and any
    ranges in `cuts` are removed before splitting. Finished parts are cached in
    CONVERTED_DIR so an interrupted upload resumes without re-encoding; the
    cache key covers the source files, part length and cut ranges, so changing
    any of them produces a fresh conversion.

    Returns a list of part paths (one item when no split is needed).
    """
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('找不到 ffmpeg，請先安裝（macOS: brew install ffmpeg）')
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        raise RuntimeError('找不到 ffprobe，轉檔需要 ffprobe（通常同 ffmpeg 一齊安裝）')

    cuts = normalise_cuts(cuts or [])
    part_hours = CONFIG["part_duration_hours"]
    try:
        fingerprint = conversion_fingerprint(files, cuts=cuts)
    except OSError as e:
        raise RuntimeError(f'無法讀取來源檔案: {e}')

    cached = load_cached_parts(fingerprint)
    if cached:
        print(f"  [轉檔] 重用已轉換檔案: {len(cached)} 段")
        return cached

    # Remove leftovers from an interrupted previous attempt
    remove_cache_by_fingerprint(fingerprint)
    CONVERTED_DIR.mkdir(parents=True, exist_ok=True)

    input_args = []
    for file_path in files:
        input_args.extend(['-i', str(file_path)])

    fps_expr = probe_frame_rate(files, ffprobe)
    if not fps_expr:
        raise RuntimeError('無法讀取來源影片 frame rate，請檢查檔案')
    audio_flags = [input_has_audio(['-i', str(f)], ffprobe) for f in files]
    has_audio = any(audio_flags)
    if has_audio and not all(audio_flags):
        raise RuntimeError('部分來源檔案有音軌、部分冇，暫時唔支援呢種混合來源')
    sample_rate = probe_sample_rate(files, ffprobe) if has_audio else None
    if has_audio and not sample_rate:
        raise RuntimeError('無法讀取來源音訊 sample rate，請檢查檔案')

    if len(files) == 1:
        source_desc = files[0].name
    else:
        source_desc = f"合併 {len(files)} 段 VOB"
        print(f"  [轉檔] {source_desc} -> MP4（H.264/AAC），需時可能較長...")
        for file_path in files:
            print(f"    - {file_path.name}")

    split = part_hours > 0
    if split:
        segment_seconds = max(1, int(part_hours * 3600) - 1)
        partial_dir = converted_partial_dir(fingerprint)
        partial_dir.mkdir(parents=True, exist_ok=True)
        output_pattern = str(partial_dir / 'part%03d.mp4')
        if len(files) == 1:
            print(
                f"  [轉檔] {source_desc} -> MP4（H.264/AAC），"
                f"每 {part_hours:g} 小時一段，需時可能較長..."
            )
        output_args = [
            '-f', 'segment',
            '-segment_time', str(segment_seconds),
            '-segment_start_number', '1',
            '-reset_timestamps', '1',
            '-force_key_frames', f'expr:gte(t,n_forced*{segment_seconds})',
            output_pattern,
        ]
    else:
        partial_dir = None
        tmp_path = converted_cache_path(fingerprint).with_name(
            converted_cache_path(fingerprint).name + '.part.mp4'
        )
        if len(files) == 1:
            print(f"  [轉檔] {source_desc} -> MP4（H.264/AAC），需時可能較長...")
        output_args = [str(tmp_path)]

    filter_complex, map_args = build_filter_graph(
        files, cuts, has_audio, fps_expr, sample_rate
    )
    stream_args = ['-filter_complex', filter_complex, *map_args, '-sn']
    if not has_audio:
        stream_args.append('-an')
    if cuts:
        removed = sum(end - start for start, end in cuts)
        print(f"  [剪輯] 移除 {len(cuts)} 段，共 {format_timestamp(removed)}:")
        for start, end in cuts:
            print(f"    - {format_timestamp(start)} - {format_timestamp(end)}")

    cmd = [
        ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-stats',
        *input_args,
        *stream_args,
        '-c:v', 'libx264', '-preset', CONVERT_PRESET, '-crf', str(CONVERT_CRF),
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '192k',
        *output_args,
    ]

    try:
        result = subprocess.run(cmd)
    except OSError as e:
        raise RuntimeError(f'無法執行 ffmpeg: {e}')

    if result.returncode != 0:
        remove_cache_by_fingerprint(fingerprint)
        raise RuntimeError(f'ffmpeg 轉檔失敗（exit code {result.returncode}）')

    if split:
        temp_parts = [
            path for path in sorted(partial_dir.glob('part*.mp4'))
            if path.stat().st_size > 0
        ]
        if not temp_parts:
            remove_cache_by_fingerprint(fingerprint)
            raise RuntimeError('ffmpeg 沒有產生任何影片分段')
        if len(temp_parts) == 1:
            target = converted_cache_path(fingerprint)
            os.replace(temp_parts[0], target)
            result_parts = [target]
        else:
            result_parts = []
            for index, temp_path in enumerate(temp_parts, start=1):
                target = CONVERTED_DIR / f"{fingerprint}.part{index:03d}.mp4"
                os.replace(temp_path, target)
                result_parts.append(target)
        shutil.rmtree(partial_dir, ignore_errors=True)
    else:
        target = converted_cache_path(fingerprint)
        os.replace(tmp_path, target)
        result_parts = [target]

    write_cache_marker(fingerprint, len(result_parts))
    if len(result_parts) == 1:
        print(f"  [轉檔] 完成: {result_parts[0].name}")
    else:
        print(f"  [轉檔] 完成: 共 {len(result_parts)} 段")
    return result_parts

def remove_cached_conversion(files, part_duration_hours=None, cuts=None):
    """Delete every cached part for these source files, part length and cut ranges"""
    try:
        fingerprint = conversion_fingerprint(files, part_duration_hours, cuts)
    except OSError:
        return
    remove_cache_by_fingerprint(fingerprint)

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

def part_title(base_title, index, total):
    """Title for one part; a single-part upload keeps the original title"""
    return base_title if total == 1 else f"{base_title} [part {index}]"

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
    cuts_map = load_cuts()
    used_cut_keys = set()
    for job in jobs:
        get_job_cuts(cuts_map, job, used_cut_keys)
    unused_cut_keys = sorted(set(cuts_map) - used_cut_keys)
    if unused_cut_keys:
        print(f"[警告] cuts.json 有未匹配任何影片嘅 key：{', '.join(unused_cut_keys)}")
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
        part_hours = CONFIG["part_duration_hours"]
        entry = state["uploads"].get(key)
        try:
            cuts = get_job_cuts(cuts_map, job)
            if cuts and not job["convert"]:
                print(
                    f"  [錯誤] {job['display_name']} 有 cut-list，但呢個格式唔會經 ffmpeg 轉檔；"
                    "請將副檔名加入 convert_extensions，或先手動剪好再上傳。"
                )
                continue
            stored_hours = entry.get("part_duration_hours") if entry else None
            stored_cuts = entry.get("cuts") if entry else None
            try:
                stored_cuts_norm = normalise_cuts(stored_cuts or [])
            except (TypeError, ValueError):
                stored_cuts_norm = []
            cuts_added_or_changed = bool(cuts) and cuts != stored_cuts_norm
            cuts_removed = not cuts and bool(stored_cuts_norm)
            hours_changed = stored_hours is not None and stored_hours != part_hours
            completed = bool(entry) and (
                bool(entry.get("video_id"))
                or (
                    bool(entry.get("parts"))
                    and all(part.get("video_id") for part in entry["parts"])
                )
            )

            if entry is None:
                entry = get_entry(state, files[0])
                refresh_entry(entry, files)
                apply_entry_settings(entry, part_hours, cuts)
                save_state(state)
            elif not is_same_source(entry, files):
                print(f"[變更] 檔案已修改，重新處理：{job['display_name']}")
                print_old_video_links(entry)
                if job["convert"]:
                    remove_cached_conversion(files, stored_hours, stored_cuts_norm)
                refresh_entry(entry, files)
                apply_entry_settings(entry, part_hours, cuts)
                save_state(state)
            elif cuts_added_or_changed:
                print(f"[變更] cut 範圍已更新，重新處理：{job['display_name']}")
                print_old_video_links(entry)
                if job["convert"]:
                    remove_cached_conversion(files, stored_hours, stored_cuts_norm)
                refresh_entry(entry, files)
                apply_entry_settings(entry, part_hours, cuts)
                save_state(state)
            elif cuts_removed:
                if not completed:
                    print(
                        "  [錯誤] 此影片之前有 cut-list，但 cuts.json 已移除相關 key；"
                        "為安全起見唔會自動上傳未剪版本。\n"
                        "        如確定要上傳未剪版本，請先刪除 upload_state.json 內該 entry。"
                    )
                    continue
                entry["cuts"] = []
                entry["part_duration_hours"] = part_hours
                save_state(state)
            elif hours_changed and not completed:
                print(f"[變更] 分段長度已變更，重新處理：{job['display_name']}")
                print_old_video_links(entry)
                if job["convert"]:
                    remove_cached_conversion(files, stored_hours, stored_cuts_norm)
                refresh_entry(entry, files)
                apply_entry_settings(entry, part_hours, cuts)
                save_state(state)
            else:
                settings_changed = False
                if "cuts" not in entry:
                    entry["cuts"] = cuts_for_state(cuts)
                    settings_changed = True
                if stored_hours is None:
                    entry["part_duration_hours"] = part_hours
                    settings_changed = True
                elif hours_changed and completed:
                    entry["part_duration_hours"] = part_hours
                    settings_changed = True
                if settings_changed:
                    save_state(state)

            # Migrate entries written by older versions that had one video per job
            if "parts" not in entry:
                if entry.get("video_id"):
                    entry["parts"] = [{
                        "video_id": entry["video_id"],
                        "playlist_added": entry.get("playlist_added", False),
                        "bytes_sent": entry.get("bytes_sent"),
                    }]
                else:
                    entry["parts"] = []
                for field in (
                    "video_id", "playlist_added", "bytes_sent",
                    "resumable_uri", "upload_size",
                ):
                    entry.pop(field, None)
                save_state(state)

            parts_state = entry["parts"]
            all_done = bool(parts_state) and all(
                part.get("video_id") for part in parts_state
            )

            if all_done:
                print(f"[略過] 已上傳過：{job['display_name']}")
            else:
                if job["convert"]:
                    media_parts = convert_to_mp4(files, cuts)
                else:
                    media_parts = [files[0]]

                if len(parts_state) != len(media_parts):
                    parts_state = [{} for _ in media_parts]
                    entry["parts"] = parts_state
                    save_state(state)

                for part_index, media_path in enumerate(media_parts, start=1):
                    part_entry = parts_state[part_index - 1]
                    title = part_title(job["title"], part_index, len(media_parts))
                    if part_entry.get("video_id"):
                        print(f"[略過] 已上傳過：{title} (ID: {part_entry['video_id']})")
                        continue
                    display_name = title
                    if len(media_parts) > 1:
                        display_name += f"（{part_index}/{len(media_parts)}）"
                    if job["convert"]:
                        display_name += "（已轉檔為 MP4）"
                    upload_video(
                        youtube, media_path, title, display_name,
                        CONFIG, part_entry, state
                    )

            # Add every uploaded part to the playlist (fills in missing ones on rerun)
            for part_entry in parts_state:
                if (
                    part_entry.get("video_id")
                    and playlist_id
                    and not part_entry.get("playlist_added")
                ):
                    add_video_to_playlist(youtube, playlist_id, part_entry["video_id"])
                    part_entry["playlist_added"] = True
                    save_state(state)

            all_done = bool(parts_state) and all(
                part.get("video_id") for part in parts_state
            )
            if all_done and job["convert"]:
                remove_cached_conversion(files, cuts=cuts)

            # Remove local files only after every part has been uploaded
            if (
                all_done
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
