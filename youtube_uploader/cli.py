"""Command-line flow: scan, upload and clean up."""
import sys

from googleapiclient.errors import HttpError

from .config import load_upload_config
from .cuts import (
    apply_entry_settings,
    cuts_for_state,
    get_job_cuts,
    load_cuts,
    normalise_cuts,
    print_old_video_links,
)
from .ffmpeg import remove_cached_conversion
from .files import build_upload_jobs, remove_local_file, scan_video_files
from .golden import mark_golden_jobs
from .pipeline import (
    cleanup_plan_cache,
    media_parts_for_plan,
    prepare_source,
    should_remove_sources,
)
from .state import (
    get_entry, is_same_source, load_convert_state, load_upload_state,
    refresh_entry, save_upload_state,
)
from .youtube_api import (
    add_video_to_playlist,
    get_authenticated_service,
    get_or_create_playlist,
    upload_video,
)


def _ensure_unique_job_keys(jobs, cuts_map):
    """Refuse ambiguous cuts_config.json matches when two jobs share the same title"""
    seen = {}
    for job in jobs:
        title = job["title"]
        if title in seen and title in cuts_map:
            sys.exit(
                f"[錯誤] 兩個影片項目共用同一個 cuts_config.json key \"{title}\"：\n"
                f"        - {seen[title]['display_name']}\n"
                f"        - {job['display_name']}\n"
                "        請重新命名其中一個資料夾／檔案。"
            )
        seen.setdefault(title, job)


def main(config=None):
    if config is None:
        config = load_upload_config()
    chunk_bytes = config["chunk_size_mb"] * 1024 * 1024
    if chunk_bytes % (256 * 1024) != 0:
        sys.exit("[錯誤] chunk_size_mb 換算後必須是 256KB 的整數倍。")

    video_files = scan_video_files(config["root_folder"], config["video_extensions"])
    jobs = build_upload_jobs(video_files, set(), combine=False)
    if not jobs:
        print(f"[提示] 在 {config['root_folder']} 內找不到相容的影片檔案。")
        return

    print(
        f"[掃描完成] 共找到 {len(video_files)} 個影片檔案，"
        f"合併為 {len(jobs)} 個上傳項目。"
    )
    state = load_upload_state()
    convert_state = load_convert_state()
    cuts_map = load_cuts()
    mark_golden_jobs(jobs, convert_state)
    _ensure_unique_job_keys(jobs, cuts_map)
    used_cut_keys = set()
    for job in jobs:
        get_job_cuts(cuts_map, job, used_cut_keys)
    unused_cut_keys = sorted(set(cuts_map) - used_cut_keys)
    if unused_cut_keys:
        print(f"[警告] cuts_config.json 有未匹配任何影片嘅 key：{', '.join(unused_cut_keys)}")
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
            plan = prepare_source(job, convert_state, config)
            files = plan.files
            key = plan.key
            entry = state.get(key)
            cuts = get_job_cuts(cuts_map, job)
            stored_cuts = entry.get("cuts") if entry else None
            try:
                stored_cuts_norm = normalise_cuts(stored_cuts or [])
            except (TypeError, ValueError):
                stored_cuts_norm = []
            cuts_added_or_changed = bool(cuts) and cuts != stored_cuts_norm
            cuts_removed = not cuts and bool(stored_cuts_norm)
            completed = bool(entry) and bool(entry.get("video_id"))

            if entry is None:
                entry = get_entry(state, files[0])
                refresh_entry(entry, files)
                apply_entry_settings(entry, cuts)
                save_upload_state(state)
            elif not is_same_source(entry, files):
                print(f"[變更] 檔案已修改，重新處理：{job['display_name']}")
                print_old_video_links(entry)
                remove_cached_conversion(files, stored_cuts_norm)
                refresh_entry(entry, files)
                apply_entry_settings(entry, cuts)
                save_upload_state(state)
            elif cuts_added_or_changed:
                print(f"[變更] cut 範圍已更新，重新處理：{job['display_name']}")
                print_old_video_links(entry)
                remove_cached_conversion(files, stored_cuts_norm)
                refresh_entry(entry, files)
                apply_entry_settings(entry, cuts)
                save_upload_state(state)
            elif cuts_removed:
                if not completed:
                    print(
                        "  [錯誤] 此影片之前有 cut-list，但 cuts_config.json 已移除相關 key；"
                        "為安全起見唔會自動上傳未剪版本。\n"
                        "        如確定要上傳未剪版本，請先刪除 upload_state.json 內該 entry。"
                    )
                    continue
                entry["cuts"] = []
                save_upload_state(state)
            else:
                if "cuts" not in entry:
                    entry["cuts"] = cuts_for_state(cuts)
                    save_upload_state(state)

            all_done = bool(entry.get("video_id"))

            if all_done:
                print(f"[略過] 已上傳過：{job['display_name']}")
            else:
                media_parts = media_parts_for_plan(plan, cuts)
                media_path = media_parts[0]
                title = job["title"]
                display_name = title
                if job["convert"] or plan.golden:
                    display_name += "（已轉檔為 MP4）"
                upload_video(
                    youtube, media_path, title, display_name,
                    config, entry, state
                )

            # Add the uploaded video to the playlist (fills in missing on rerun)
            if (
                entry.get("video_id")
                and playlist_id
                and not entry.get("playlist_added")
            ):
                add_video_to_playlist(youtube, playlist_id, entry["video_id"])
                entry["playlist_added"] = True
                save_upload_state(state)

            all_done = bool(entry.get("video_id"))
            if all_done:
                cleanup_plan_cache(plan, cuts)

            # Remove local files only after the upload succeeds
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
                    save_upload_state(state)
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
