"""Upload and conversion progress tracking."""
import hashlib
import json
import os

from .config import CONVERT_STATE_FILE, STATE_FILE


def _atomic_write(path, data):
    tmp = path.with_name(path.name + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_upload_state(state_file=STATE_FILE):
    """Load upload_state.json; start fresh if the file is corrupt"""
    if state_file.exists():
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)
            if isinstance(state.get("uploads"), dict):
                return {"uploads": state["uploads"]}
        except (json.JSONDecodeError, OSError) as e:
            print(f"[警告] 進度檔 {state_file.name} 無法讀取（{e}），將重新開始記錄。")
    return {"uploads": {}}


def save_upload_state(state):
    """Atomically write the upload progress file"""
    _atomic_write(STATE_FILE, {"uploads": state["uploads"]})


def load_convert_state(state_file=CONVERT_STATE_FILE):
    """Load convert_state.json; fall back to legacy golden data in upload_state.json"""
    if state_file.exists():
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)
            if isinstance(state.get("golden_sources"), dict):
                return {"golden_sources": state["golden_sources"]}
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
                return {"golden_sources": goldens}
        except (json.JSONDecodeError, OSError):
            pass
    return {"golden_sources": {}}


def save_convert_state(state):
    """Atomically write the conversion progress file"""
    _atomic_write(
        CONVERT_STATE_FILE,
        {"golden_sources": state.get("golden_sources", {})},
    )


def get_entry(state, file_path):
    return state["uploads"].setdefault(str(file_path.resolve()), {})


def get_golden_paths(state):
    """Return the absolute golden source paths recorded in state"""
    return set(state.get("golden_sources", {}))


def record_golden_source(state, golden_path, title, source_files):
    """Record a converted MP4 as the golden source for a job"""
    state.setdefault("golden_sources", {})[str(golden_path.resolve())] = {
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
