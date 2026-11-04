"""Upload progress tracking in upload_state.json."""
import hashlib
import json
import os

from .config import STATE_FILE


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
