"""Command-line flow: scan, convert, upload and clean up."""
import shutil
import sys

from googleapiclient.errors import HttpError

from .config import load_config
from .cuts import (
    apply_entry_settings,
    cut_entry_exists,
    cuts_for_state,
    get_job_cuts,
    iter_cut_entries,
    job_cut_key,
    job_part_numbers,
    load_cuts,
    normalise_cuts,
    print_old_video_links,
)
from .ffmpeg import remove_cached_conversion
from .files import build_upload_jobs, remove_local_file, scan_video_files
from .golden import mark_golden_jobs, skip_converted_source_jobs
from .pipeline import (
    cleanup_plan_cache,
    media_parts_for_plan,
    prepare_source,
    should_remove_sources,
)
from .state import get_entry, is_same_source, load_state, refresh_entry, save_state
from .youtube_api import (
    add_video_to_playlist,
    get_authenticated_service,
    get_or_create_playlist,
    upload_video,
)


def part_title(base_title, index, total):
    """Title for one part; a single-part upload keeps the original title"""
    return base_title if total == 1 else f"{base_title} [part {index}]"


def _ensure_unique_job_keys(jobs, cuts_map):
    """Refuse ambiguous cuts.json matches when two jobs share the same key"""
    seen = {}
    for job in jobs:
        namespace, name = job_cut_key(job)
        marker = (namespace, name)
        if marker in seen and cut_entry_exists(cuts_map, namespace, name):
            sys.exit(
                f"[錯誤] 兩個影片項目共用同一個 cuts.json key "
                f"\"{namespace}.{name}\"：\n"
                f"        - {seen[marker]['display_name']}\n"
                f"        - {job['display_name']}\n"
                "        請重新命名其中一個資料夾／檔案。"
            )
        seen.setdefault(marker, job)


def main():
    config = load_config()
    chunk_bytes = config["chunk_size_mb"] * 1024 * 1024
    if chunk_bytes % (256 * 1024) != 0:
        sys.exit("[錯誤] chunk_size_mb 換算後必須是 256KB 的整數倍。")

    video_files = scan_video_files(config["root_folder"], config["video_extensions"])
    convert_extensions = {ext.lower() for ext in config["convert_extensions"]}
    jobs = build_upload_jobs(video_files, convert_extensions)
    if not jobs:
        print(f"[提示] 在 {config['root_folder']} 內找不到相容的影片檔案。")
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
    mark_golden_jobs(jobs, state)
    skipped = skip_converted_source_jobs(jobs, state)
    if skipped:
        print(f"[略過] 已匯出 golden source：{', '.join(skipped)}")
    jobs = [job for job in jobs if not job.get("skip_reason")]
    if skipped and not any(job.get("golden") for job in jobs):
        sys.exit(
            "[錯誤] 有 golden source 記錄，但喺掃描範圍搵唔到對應 MP4；"
            "請確認 root_folder 包含 golden 檔案。"
        )
    _ensure_unique_job_keys(jobs, cuts_map)
    part_hours = config["part_duration_hours"]
    used_cut_keys = set()
    for job in jobs:
        get_job_cuts(cuts_map, job, part_hours, used_cut_keys)
    unused_cut_keys = sorted(
        display
        for display, used_key in iter_cut_entries(cuts_map)
        if used_key not in used_cut_keys
    )
    if unused_cut_keys:
        print(f"[警告] cuts.json 有未匹配任何影片嘅 key：{', '.join(unused_cut_keys)}")
    youtube = get_authenticated_service()

    # Get or create the playlist ID
    playlist_id = None
    if config["playlist_name"]:
        playlist_id = get_or_create_playlist(
            youtube,
            config["playlist_name"],
            config["privacy_status"]
        )

    # Upload each video and add it to the playlist
    for idx, job in enumerate(jobs, start=1):
        print(f"\n處理第 [{idx}/{len(jobs)}] 部影片...")
        try:
            plan = prepare_source(job, state, config)
            files = plan.files
            key = plan.key
            entry = state["uploads"].get(key)
            cuts = get_job_cuts(cuts_map, job, part_hours)
            stored_hours = entry.get("part_duration_hours") if entry else None
            stored_cuts = entry.get("cuts") if entry else None
            try:
                stored_cuts_norm = normalise_cuts(stored_cuts or [])
            except (TypeError, ValueError):
                stored_cuts_norm = []
            # Older entries have no stored part length, so they used the current setting
            cache_hours = stored_hours if stored_hours is not None else part_hours
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
                remove_cached_conversion(files, cache_hours, stored_cuts_norm)
                refresh_entry(entry, files)
                apply_entry_settings(entry, part_hours, cuts)
                save_state(state)
            elif cuts_added_or_changed:
                print(f"[變更] cut 範圍已更新，重新處理：{job['display_name']}")
                print_old_video_links(entry)
                remove_cached_conversion(files, cache_hours, stored_cuts_norm)
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
                remove_cached_conversion(files, cache_hours, stored_cuts_norm)
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
                media_parts = media_parts_for_plan(plan, part_hours, cuts)

                requested_parts = (
                    job_part_numbers(cuts_map, job) if part_hours > 0 else set()
                )
                if requested_parts and max(requested_parts) > len(media_parts):
                    print(
                        f"  [錯誤] cuts.json 指定咗 part {max(requested_parts)}，"
                        f"但 {job['display_name']} 實際只有 {len(media_parts)} 個 part；"
                        "請檢查 cuts.json 嘅 parts 設定。"
                    )
                    continue

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
                    if job["convert"] or plan.golden:
                        display_name += "（已轉檔為 MP4）"
                    upload_video(
                        youtube, media_path, title, display_name,
                        config, part_entry, state
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
            if all_done:
                cleanup_plan_cache(plan, part_hours, cuts)

            # Remove local files only after every part has been uploaded
            if (
                all_done
                and should_remove_sources(plan, config)
                and not entry.get("local_removed")
            ):
                all_removed = True
                for source_file in files:
                    if not source_file.exists():
                        continue
                    try:
                        remove_local_file(source_file, config["remove_local_permanent"])
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
