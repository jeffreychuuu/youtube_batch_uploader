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


_NAMESPACES = ("folders", "files")


def _empty_cuts():
    return {"folders": {}, "files": {}, "legacy": {}}


def _parse_cut_spec(spec, label):
    """Parse one namespaced entry: {"original": [...]} or {"parts": {"N": [...]}}"""
    if not isinstance(spec, dict):
        sys.exit(
            f"[錯誤] {CUTS_FILE.name} 內 \"{label}\" 必須係 object，"
            '例如 {"original": ["1:00-2:00"]} 或 {"parts": {"2": ["0:10-0:20"]}}。'
        )
    unknown = sorted(set(spec) - {"original", "parts"})
    if unknown:
        sys.exit(
            f"[錯誤] {CUTS_FILE.name} 內 \"{label}\" 有未知欄位：{', '.join(unknown)}。"
        )

    has_original = "original" in spec
    has_parts = "parts" in spec
    if has_original == has_parts:
        sys.exit(
            f"[錯誤] {CUTS_FILE.name} 內 \"{label}\" 必須且只能填 "
            "original 或 parts 其中一個。"
        )

    if has_original:
        return {"original": parse_cut_ranges(spec["original"], f"{label}.original")}

    parts_raw = spec["parts"]
    if not isinstance(parts_raw, dict):
        sys.exit(f"[錯誤] {CUTS_FILE.name} 內 \"{label}.parts\" 必須係 object。")
    parts = {}
    for number_text, items in parts_raw.items():
        if not (
            isinstance(number_text, str)
            and number_text.isdigit()
            and int(number_text) >= 1
        ):
            sys.exit(
                f"[錯誤] {CUTS_FILE.name} 內 \"{label}.parts\" 嘅 key "
                f"必須係正整數 part 號（例如 \"1\"、\"2\"）：{number_text!r}"
            )
        number = int(number_text)
        if number in parts:
            sys.exit(
                f"[錯誤] {CUTS_FILE.name} 內 \"{label}.parts\" 有重複 part 號：{number}。"
            )
        parts[number] = parse_cut_ranges(items, f"{label}.parts.{number_text}")
    if not parts:
        sys.exit(f"[錯誤] {CUTS_FILE.name} 內 \"{label}.parts\" 唔可以係空 object。")
    return {"parts": parts}


def load_cuts(cuts_file=CUTS_FILE):
    """Load cuts.json.

    Supports the namespaced format (`folders` for merged VOB jobs, `files` for
    single-file jobs) and the legacy flat format where the key is the job title,
    the first source path, or "<title> [part N]".
    """
    if not cuts_file.exists():
        return _empty_cuts()
    try:
        with open(cuts_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        sys.exit(f"[錯誤] 無法讀取 {cuts_file.name}：{e}")
    if not isinstance(data, dict):
        sys.exit(f"[錯誤] {cuts_file.name} 格式錯誤，最外層必須係 JSON object。")

    cuts = _empty_cuts()
    for key, value in data.items():
        if key in _NAMESPACES:
            if not isinstance(value, dict):
                sys.exit(f"[錯誤] {cuts_file.name} 內 \"{key}\" 必須係 object。")
            for name, spec in value.items():
                cuts[key][name] = _parse_cut_spec(spec, f"{key}.{name}")
        else:
            cuts["legacy"][key] = parse_cut_ranges(value, key)
    return cuts


def _as_cuts_structure(cuts_map):
    """Accept the loaded structure or a legacy flat mapping (older callers/tests)"""
    if not cuts_map:
        return _empty_cuts()
    if isinstance(cuts_map, dict) and any(
        key in cuts_map for key in ("folders", "files", "legacy")
    ):
        return {
            "folders": cuts_map.get("folders", {}),
            "files": cuts_map.get("files", {}),
            "legacy": cuts_map.get("legacy", {}),
        }
    return {"folders": {}, "files": {}, "legacy": dict(cuts_map)}


def job_cut_key(job):
    """Return (namespace, name) for this job's cuts.json lookup.

    Merged jobs (more than one source file) are keyed by folder title under
    `folders`; single-file jobs are keyed by file stem under `files`.
    """
    if len(job["files"]) > 1:
        return "folders", job["title"]
    return "files", job["files"][0].stem


def cut_entry_exists(cuts_map, namespace, name):
    """Whether cuts.json configures this job (namespaced or legacy title key)"""
    cuts = _as_cuts_structure(cuts_map)
    if name in cuts[namespace]:
        return True
    prefix = f"{name} [part "
    return any(
        key == name or (key.startswith(prefix) and key.endswith(']'))
        for key in cuts["legacy"]
    )


def iter_cut_entries(cuts_map):
    """Yield (display_name, used_key) for every configured cuts.json entry"""
    cuts = _as_cuts_structure(cuts_map)
    for key in cuts["legacy"]:
        yield key, key
    for namespace in _NAMESPACES:
        for name in cuts[namespace]:
            yield f"{namespace}.{name}", f"{namespace}:{name}"


def _part_offset_ranges(parts, part_duration_hours):
    """Convert {part_number: ranges} to full-timeline ranges"""
    segment_seconds = max(1, int(part_duration_hours * 3600) - 1)
    ranges = []
    for number, part_ranges in parts.items():
        offset = (number - 1) * segment_seconds
        ranges.extend((start + offset, end + offset) for start, end in part_ranges)
    return ranges


def part_cut_ranges(cuts_map, job, part_duration_hours, used_keys=None):
    """Convert '<title> [part N]' cut entries to the full video timeline"""
    if part_duration_hours <= 0:
        return []
    prefix = f"{job['title']} [part "
    segment_seconds = max(1, int(part_duration_hours * 3600) - 1)
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


def get_job_cuts(cuts_map, job, part_duration_hours, used_keys=None):
    """Look up namespaced entries and legacy title/path/part keys for a job"""
    cuts = _as_cuts_structure(cuts_map)
    ranges = []

    namespace, name = job_cut_key(job)
    spec = cuts[namespace].get(name)
    if spec is not None:
        if "original" in spec:
            ranges.extend(spec["original"])
        else:
            if part_duration_hours <= 0:
                sys.exit(
                    f"[錯誤] {CUTS_FILE.name} 內 \"{namespace}.{name}\" 用咗 parts，"
                    "但 part_duration_hours 係 0（唔分割）；"
                    "請改為 original，或設定分段長度。"
                )
            ranges.extend(_part_offset_ranges(spec["parts"], part_duration_hours))
        if used_keys is not None:
            used_keys.add(f"{namespace}:{name}")

    # Legacy flat keys: title, absolute first-file path, and "<title> [part N]".
    for key in (job["title"], str(job["files"][0].resolve())):
        if key in cuts["legacy"]:
            ranges.extend(cuts["legacy"][key])
            if used_keys is not None:
                used_keys.add(key)
    ranges.extend(part_cut_ranges(cuts["legacy"], job, part_duration_hours, used_keys))
    return normalise_cuts(ranges)


def job_part_numbers(cuts_map, job):
    """Part numbers configured for this job, from namespaced or legacy keys"""
    cuts = _as_cuts_structure(cuts_map)
    namespace, name = job_cut_key(job)
    numbers = set()

    spec = cuts[namespace].get(name)
    if spec is not None and "parts" in spec:
        numbers.update(spec["parts"])

    prefix = f"{job['title']} [part "
    for key in cuts["legacy"]:
        if key.startswith(prefix) and key.endswith(']'):
            number_text = key[len(prefix):-1].strip()
            if number_text.isdigit() and int(number_text) >= 1:
                numbers.add(int(number_text))
    return numbers


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
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
