"""Parsing and lookup of cut ranges from cuts.json."""
import json
import re
import sys

from .config import CUTS_FILE


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


def load_cuts(cuts_file=CUTS_FILE):
    """Load cuts.json as {title: [(start, end), ...]}"""
    if not cuts_file.exists():
        return {}
    try:
        with open(cuts_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        sys.exit(f"[錯誤] 無法讀取 {cuts_file.name}：{e}")
    if not isinstance(data, dict):
        sys.exit(f"[錯誤] {cuts_file.name} 格式錯誤，最外層必須係 JSON object。")

    cuts = {}
    for key, items in data.items():
        if isinstance(items, dict):
            sys.exit(
                f"[錯誤] {cuts_file.name} 內 \"{key}\" 係舊格式；"
                "而家只支援 key → 時間範圍陣列。"
            )
        cuts[key] = parse_cut_ranges(items, key)
    return cuts


def get_job_cuts(cuts_map, job, used_keys=None):
    """Look up cut ranges for a job by its title"""
    key = job["title"]
    ranges = cuts_map.get(key, [])
    if ranges and used_keys is not None:
        used_keys.add(key)
    return normalise_cuts(ranges)


def apply_entry_settings(entry, cuts):
    """Store the current cut settings on a job entry"""
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
    return json.dumps(
        [[start, end] for start, end in normalise_cuts(cuts)],
        separators=(',', ':'),
    )


def cuts_for_state(cuts):
    """JSON-serialisable cut ranges stored in upload_state.json"""
    return [[start, end] for start, end in normalise_cuts(cuts)]


def format_timestamp(seconds):
    """Format seconds as H:MM:SS or M:SS for logs"""
    seconds = int(round(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f'{hours}:{minutes:02d}:{secs:02d}'
    return f'{minutes}:{secs:02d}'
