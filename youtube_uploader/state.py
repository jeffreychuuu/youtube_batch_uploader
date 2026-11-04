"""Upload and conversion progress tracking."""
import hashlib
import json
import os

from .config import CONVERT_STATE_FILE, STATE_FILE


def _atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _flatten_upload_entry(entry):
    """Drop legacy split/golden fields and flatten a single video entry"""
    if not isinstance(entry, dict):
        return {}
    entry = dict(entry)
    entry.pop("golden_exported", None)
    entry.pop("golden_source", None)
    entry.pop("part_duration_hours", None)
    parts = entry.pop("parts", None)
    if parts and not entry.get("video_id"):
        for part in parts:
            if part.get("video_id"):
                entry["video_id"] = part["video_id"]
                entry["playlist_added"] = part.get("playlist_added", False)
                entry["bytes_sent"] = part.get("bytes_sent")
                entry["resumable_uri"] = part.get("resumable_uri")
                entry["upload_size"] = part.get("upload_size")
                break
    return entry


def load_upload_state(state_file=STATE_FILE):
    """Load upload_state.json as {source path: entry}; start fresh if corrupt"""
    if state_file.exists():
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)
            if isinstance(state.get("uploads"), dict):
                raw = state["uploads"]
            elif isinstance(state, dict):
                raw = {
                    key: value for key, value in state.items()
                    if key not in ("uploads", "golden_sources")
                    and isinstance(value, dict)
                }
            else:
                raw = {}
            return {
                key: _flatten_upload_entry(entry)
                for key, entry in raw.items()
            }
        except (json.JSONDecodeError, OSError) as e:
            print(f"[警告] 進度檔 {state_file.name} 無法讀取（{e}），將重新開始記錄。")
    return {}


def save_upload_state(state):
    """Atomically write the flat upload progress file"""
    _atomic_write(STATE_FILE, state)


def load_convert_state(state_file=CONVERT_STATE_FILE):
    """Load convert_state.json as {golden path: info}; fall back to legacy data"""
    if state_file.exists():
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)
            if isinstance(state.get("golden_sources"), dict):
                return state["golden_sources"]
            if isinstance(state, dict):
                return {
                    key: value for key, value in state.items()
                    if isinstance(value, dict)
                }
        except (json.JSONDecodeError, OSError) as e:
            print(f"[警告] 轉檔記錄 {state_file.name} 無法讀取（{e}），將重新開始記錄。")
    elif STATE_FILE.exists():
        try:
            with open(STATE_FILE, 'r', encoding='utf-8') as f:
                legacy = json.load(f)
            goldens = legacy.get("golden_sources")
            if isinstance(goldens, dict) and goldens:
                print(
                    "[提示] 從 upload_state.json 讀取舊 golden_sources；"
                    "下次儲存會寫入 convert_state.json。"
                )
                return goldens
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def save_convert_state(state):
    """Atomically write the flat conversion progress file"""
    _atomic_write(CONVERT_STATE_FILE, state)


def get_entry(state, file_path):
    return state.setdefault(str(file_path.resolve()), {})


def get_golden_paths(state):
    """Return the absolute golden source paths recorded in state"""
    return set(state)


def record_golden_source(state, golden_path, title, source_files):
    """Record a converted MP4 as the golden source for a job"""
    state[str(golden_path.resolve())] = {
        "title": title,
        "source_files": [str(path.resolve()) for path in source_files],
    }


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
