"""Video scanning, grouping and local file cleanup."""
import os
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


def _common_file_prefix(files):
    """Common prefix of the file stems in a folder, without trailing separators"""
    prefix = os.path.commonprefix([path.stem for path in files])
    prefix = prefix.rstrip('0123456789')
    return prefix.rstrip(' -_.')


def combined_name_base(folder, files, root_folder, name_source,
                       allow_prefix=True):
    """Base title for a combined job: folder, root folder, or file prefix"""
    if name_source == 'root' and root_folder:
        return Path(root_folder).name
    if name_source == 'prefix' and allow_prefix:
        prefix = _common_file_prefix(files)
        if prefix:
            return prefix
    return folder_display_name(folder)


def converted_output_path(job, output_folder):
    """Golden path directly inside output_folder"""
    return Path(output_folder).expanduser() / f"{job['title']}.mp4"


def converted_source_path(job):
    """Path of the converted MP4; job['output_path'] wins when set"""
    if job.get("output_path"):
        return Path(job["output_path"])
    folder = job["files"][0].parent
    if folder.name.upper() == 'VIDEO_TS' and folder.parent != folder:
        folder = folder.parent
    return folder / f"{job['title']}.mp4"


def _combined_title(base):
    """Default name for a combined group"""
    return f"{base} [combined]"


def build_upload_jobs(video_files, convert_extensions, combine=True,
                      root_folder=None, name_source='folder'):
    """Turn scanned files into upload jobs, optionally merging segments.

    `combine` merges VOB title sets and same-folder files of the same convert
    extension. When false, every file is its own job.
    `name_source` picks the combined title: "folder", "root" or "prefix".
    DVD menu files (*_0.VOB and VIDEO_TS.VOB) are always skipped.
    """
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

        if combine:
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

    folder_files = {}
    for path in video_files:
        folder_files.setdefault(path.parent, []).append(path)

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
                title = combined_name_base(
                    folder, folder_files[folder], root_folder,
                    name_source, allow_prefix=False,
                )
                if folder_counts[folder] > 1:
                    # Multiple groups in one folder: keep the titles distinct
                    title = f"{title} ({group['prefix']})"
                title = _combined_title(title)
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
            title = combined_name_base(
                folder, folder_files[folder], root_folder, name_source
            )
            if folder_counts[folder] > 1:
                extension = group["suffix"].lstrip('.').upper()
                title = f"{title} ({extension})"
            title = _combined_title(title)
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


def remove_empty_folders(file_path, root_folder):
    """Remove empty parent folders up to and including root_folder"""
    root = Path(root_folder).expanduser().resolve()
    folder = Path(file_path).expanduser().resolve().parent
    if folder != root and root not in folder.parents:
        return
    while True:
        try:
            folder.rmdir()
        except OSError:
            break
        if folder == root:
            break
        folder = folder.parent


def _remove_dir_if_empty(folder):
    """Remove one directory if it is empty (ignoring .DS_Store)"""
    try:
        folder.rmdir()
        return True
    except OSError:
        pass
    try:
        entries = list(folder.iterdir())
    except OSError:
        return False
    if entries and all(
        entry.name == '.DS_Store' and entry.is_file()
        for entry in entries
    ):
        try:
            for entry in entries:
                entry.unlink()
            folder.rmdir()
            return True
        except OSError:
            return False
    return False


def prune_empty_dirs(root_folder):
    """Remove empty folders under root, including root itself"""
    root = Path(root_folder).expanduser().resolve()
    if not root.exists() or not root.is_dir() or root.is_symlink():
        return
    directories = [
        path for path in root.rglob('*')
        if path.is_dir() and not path.is_symlink()
    ]
    directories.sort(key=lambda path: len(path.parts), reverse=True)
    for folder in directories:
        _remove_dir_if_empty(folder)
    _remove_dir_if_empty(root)
