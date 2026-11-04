"""Video scanning, grouping and local file cleanup."""
import re
import subprocess
import sys
from pathlib import Path

# DVD-style VOB segments: VTS_01_1.VOB, VTS_01_2.VOB, ...
VOB_SEGMENT_RE = re.compile(r'^(?P<prefix>.+)_(?P<index>\d+)$')


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


def converted_source_path(job):
    """Path of the converted MP4, next to the source (or the VIDEO_TS parent)"""
    folder = job["files"][0].parent
    if folder.name.upper() == 'VIDEO_TS' and folder.parent != folder:
        folder = folder.parent
    return folder / f"{job['title']}.mp4"


def _combined_title(base, combined_names):
    """Custom combined name if configured, else '<base> [combined]'"""
    if combined_names and base in combined_names:
        return combined_names[base]
    return f"{base} [combined]"


def build_upload_jobs(
    video_files,
    convert_extensions,
    combine_mode='vob',
    combine_extensions=None,
    combined_names=None,
):
    """Turn scanned files into upload jobs, optionally merging segments.

    `combine_mode` controls grouping:
    - "vob" (default): merge multi-segment VOB title sets only.
    - "folder": additionally merge every file of a listed extension in the
      same folder, in natural order.
    - "none": every file is its own job.

    `combine_extensions` limits which extensions may be grouped.
    `combined_names` maps a group title (before the "[combined]" suffix) to a
    custom name. DVD menu files (*_0.VOB and VIDEO_TS.VOB) are always skipped.
    """
    if combine_extensions is None:
        combine_extensions = ['.vob']
    combine_extensions = {ext.lower() for ext in combine_extensions}

    jobs = []
    groups = {}

    for path in video_files:
        suffix = path.suffix.lower()

        if suffix not in convert_extensions:
            # Upload this format as-is
            jobs.append({
                "files": [path],
                "title": path.stem,
                "display_name": path.name,
                "convert": False,
            })
            continue

        if suffix == '.vob':
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

            combine = (
                combine_mode in ('vob', 'folder')
                and '.vob' in combine_extensions
            )
            if not combine:
                jobs.append({
                    "files": [path],
                    "title": path.stem,
                    "display_name": path.name,
                    "convert": True,
                })
                continue

            group_key = (
                'vob', str(path.parent.resolve()), match.group('prefix').lower()
            )
            group = groups.get(group_key)
            if group is None:
                group = {
                    "kind": "vob",
                    "folder": path.parent,
                    "prefix": match.group('prefix'),
                    "segments": [],
                }
                groups[group_key] = group
                jobs.append(group)
            group["segments"].append((index, path))
            continue

        if combine_mode == 'folder' and suffix in combine_extensions:
            group_key = ('ext', str(path.parent.resolve()), suffix)
            group = groups.get(group_key)
            if group is None:
                group = {
                    "kind": "ext",
                    "folder": path.parent,
                    "suffix": suffix,
                    "files": [],
                }
                groups[group_key] = group
                jobs.append(group)
            group["files"].append(path)
            continue

        # Other formats can be transcoded individually when configured
        jobs.append({
            "files": [path],
            "title": path.stem,
            "display_name": path.name,
            "convert": True,
        })

    folder_counts = {}
    for group in groups.values():
        folder = group["folder"]
        folder_counts[folder] = folder_counts.get(folder, 0) + 1

    for group in groups.values():
        folder = group["folder"]

        if group["kind"] == "vob":
            group["segments"].sort(key=lambda item: item[0])
            files = [path for _, path in group["segments"]]
            merged = len(files) > 1

            indexes = [index for index, _ in group["segments"]]
            if indexes != list(range(1, len(indexes) + 1)):
                print(
                    f"[警告] {folder.name} 的 VOB 分段不連續：{indexes}"
                    "（可能未複製完成，請確認後再上傳）"
                )

            if merged:
                title = folder_display_name(folder)
                if folder_counts[folder] > 1:
                    # Multiple groups in one folder: keep the titles distinct
                    title = f"{title} ({group['prefix']})"
                title = _combined_title(title, combined_names)
            else:
                title = files[0].stem

            group["files"] = files
            group["title"] = title
            group["display_name"] = (
                f"{title}（{len(files)} 段 VOB 合併）"
                if merged else files[0].name
            )
            group["convert"] = True
            group.pop("segments", None)
            group.pop("prefix", None)
            group.pop("kind", None)
            group.pop("folder", None)
            continue

        files = sorted(group["files"], key=natural_sort_key)
        if len(files) > 1:
            title = folder_display_name(folder)
            if folder_counts[folder] > 1:
                extension = group["suffix"].lstrip('.').upper()
                title = f"{title} ({extension})"
            title = _combined_title(title, combined_names)
            display_name = f"{title}（{len(files)} 個檔案合併）"
        else:
            title = files[0].stem
            display_name = files[0].name
        group["files"] = files
        group["title"] = title
        group["display_name"] = display_name
        group["convert"] = True
        group.pop("suffix", None)
        group.pop("kind", None)
        group.pop("folder", None)

    return jobs


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
