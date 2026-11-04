"""Parsing and lookup of cut ranges from cuts_config.json."""
import json
import re
import sys

from .config import CUTS_FILE, DEFAULT_CUT_CONFIG

CUT_DESCRIPTION_TEMPLATE = (
    "因違反 YouTube 政策，已剪走以下片段（共 {total}）：\n{cuts}"
)


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
    """Parse a list of 'start-end' strings from cuts_config.json"""
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


def _kept_segments(previous_cuts):
    """Map each kept golden segment to its position in the uploaded timeline"""
    segments = []
    previous_end = 0
    removed = 0
    for start, end in previous_cuts:
        segments.append((previous_end, start, previous_end - removed))
        removed += end - start
        previous_end = max(previous_end, end)
    segments.append((previous_end, None, previous_end - removed))
    return segments


def _map_uploaded_range(start, end, previous_cuts, key, batch_index):
    """Split an uploaded range at previous cuts and map it to golden intervals"""
    mapped = []
    for golden_start, golden_end, uploaded_start in _kept_segments(previous_cuts):
        if golden_end is None:
            uploaded_end = None
        else:
            uploaded_end = uploaded_start + (golden_end - golden_start)
        piece_start = max(start, uploaded_start)
        piece_end = end if uploaded_end is None else min(end, uploaded_end)
        if piece_start < piece_end:
            mapped.append((
                golden_start + (piece_start - uploaded_start),
                golden_start + (piece_end - uploaded_start),
            ))
    if not mapped:
        sys.exit(
            f"[錯誤] {CUTS_FILE.name} 內 \"{key}\" 第 {batch_index} 批範圍"
            "超出上傳版本範圍。"
        )
    return mapped


def parse_cut_batches(items, key):
    """Parse flat golden-timeline ranges or ordered batches.

    A flat array of strings is one batch on the golden timeline. An array of
    arrays is a sequence of batches: the first is on the golden timeline, and
    every later batch is on the timeline after all previous batches were cut.
    All batches are resolved back to golden-timeline ranges.
    """
    if not isinstance(items, list):
        sys.exit(f"[錯誤] {CUTS_FILE.name} 內 \"{key}\" 必須係時間範圍陣列。")
    if not items:
        return []
    if all(isinstance(item, str) for item in items):
        return parse_cut_ranges(items, key)
    if not all(isinstance(item, list) for item in items):
        sys.exit(
            f"[錯誤] {CUTS_FILE.name} 內 \"{key}\" 必須全部係時間範圍字串，"
            "或者全部係批次陣列。"
        )

    resolved = []
    for batch_index, batch in enumerate(items, start=1):
        mapped = []
        for start, end in parse_cut_ranges(batch, key):
            mapped.extend(
                _map_uploaded_range(
                    start, end, resolved, key, batch_index
                )
            )
        resolved = normalise_cuts(resolved + mapped)
    return resolved


def _validate_cut_settings(settings, cuts_file):
    if settings["cut_encoder"] not in ("libx264", "h264_videotoolbox"):
        sys.exit(
            f"[錯誤] {cuts_file.name} 嘅 cut_encoder "
            "只可以係 'libx264' 或 'h264_videotoolbox'。"
        )
    if isinstance(settings["cut_quality"], bool) or not isinstance(
        settings["cut_quality"], int
    ):
        sys.exit(f"[錯誤] {cuts_file.name} 嘅 cut_quality 必須係整數。")
    max_quality = 51 if settings["cut_encoder"] == "libx264" else 100
    if not 0 <= settings["cut_quality"] <= max_quality:
        sys.exit(
            f"[錯誤] {cuts_file.name} 嘅 cut_quality 必須介乎 0 至 "
            f"{max_quality}（{settings['cut_encoder']}）。"
        )


def load_cut_config(cuts_file=CUTS_FILE):
    """Load cuts_config.json as (settings, {title: [(start, end), ...]})"""
    settings = dict(DEFAULT_CUT_CONFIG)
    if not cuts_file.exists():
        return settings, {}
    try:
        with open(cuts_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        sys.exit(f"[錯誤] 無法讀取 {cuts_file.name}：{e}")
    if not isinstance(data, dict):
        sys.exit(f"[錯誤] {cuts_file.name} 格式錯誤，最外層必須係 JSON object。")

    for key in DEFAULT_CUT_CONFIG:
        if key in data:
            settings[key] = data.pop(key)
    _validate_cut_settings(settings, cuts_file)

    if "cuts" in data:
        cuts_data = data.pop("cuts")
        if not isinstance(cuts_data, dict):
            sys.exit(
                f"[錯誤] {cuts_file.name} 嘅 \"cuts\" 必須係 JSON object。"
            )
    else:
        cuts_data = data

    cuts = {}
    for key, items in cuts_data.items():
        if isinstance(items, dict):
            sys.exit(
                f"[錯誤] {cuts_file.name} 內 \"{key}\" 係舊格式；"
                "而家只支援 key → 時間範圍陣列。"
            )
        cuts[key] = parse_cut_batches(items, key)
    return settings, cuts


def load_cuts(cuts_file=CUTS_FILE):
    """Load only the title → cut ranges map from cuts_config.json"""
    return load_cut_config(cuts_file)[1]


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
    """Warn about an existing YouTube video before a re-upload"""
    video_id = entry.get("video_id")
    if video_id:
        print("  [提示] 舊版本仍然喺 YouTube，確認新片上傳成功後請手動刪除：")
        print(f"    - https://youtu.be/{video_id}")


def format_cut_ranges(cuts):
    """Format cut ranges as one bullet per line"""
    return "\n".join(
        f"- {format_timestamp(start)} - {format_timestamp(end)}"
        for start, end in normalise_cuts(cuts)
    )


def format_duration(seconds):
    """Format a duration as Chinese minutes and seconds"""
    total = int(round(seconds))
    minutes, secs = divmod(total, 60)
    if minutes == 0:
        return f"{secs} 秒"
    if secs == 0:
        return f"{minutes} 分鐘"
    return f"{minutes} 分 {secs} 秒"


def build_cut_description(template, cuts):
    """Build the upload description for a video with removed ranges"""
    normalised = normalise_cuts(cuts)
    if not normalised or not template:
        return ""
    total = sum(end - start for start, end in normalised)
    return (
        template
        .replace("{total}", format_duration(total))
        .replace("{cuts}", format_cut_ranges(normalised))
        .strip()
    )


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
