"""Source preparation and media-part selection for the upload pipeline."""
from dataclasses import dataclass

from .files import converted_source_path, remove_local_file
from .ffmpeg import (
    convert_to_mp4, move_converted_to_source, remove_cached_conversion,
    remove_copy_cache, split_without_encode, verify_video,
)
from .state import migrate_completed_entry, record_golden_source, save_state


@dataclass
class SourcePlan:
    files: list
    key: str
    mode: str          # "convert_pipeline" or "single"
    golden: bool


def prepare_source(job, state, config):
    """Convert a source job into the files and state key to upload from"""
    files = job["files"]
    if job.get("golden"):
        return SourcePlan(files, str(files[0].resolve()), "single", True)
    if not job["convert"]:
        return SourcePlan(files, str(files[0].resolve()), "single", False)
    if not config["export_converted_source"]:
        return SourcePlan(files, str(files[0].resolve()), "convert_pipeline", False)

    target = converted_source_path(job)
    golden_key = str(target.resolve())
    recorded = state.get("golden_sources", {}).get(golden_key)
    if target.exists():
        if not recorded:
            raise SystemExit(
                f"[錯誤] golden 目標檔案已存在但未記錄喺 state：{target}\n"
                "        請先檢查／移除該檔案，或還原 upload_state.json。"
            )
        if golden_key not in state["uploads"]:
            migrate_completed_entry(state, str(files[0].resolve()), target)
            save_state(state)
        job["files"] = [target]
        job["golden"] = True
        return SourcePlan([target], golden_key, "single", True)

    converted = convert_to_mp4(files, 0, [])[0]
    verify_video(converted)
    move_converted_to_source(converted, target)
    verify_video(target)

    old_key = str(files[0].resolve())
    record_golden_source(state, target, job["title"], files)
    if golden_key not in state["uploads"]:
        migrate_completed_entry(state, old_key, target)
    old_entry = state["uploads"].get(old_key)
    if old_entry is not None:
        old_entry["golden_exported"] = True
        old_entry["golden_source"] = golden_key
    save_state(state)

    if config["remove_original_after_conversion"]:
        for source_file in files:
            if source_file.exists():
                remove_local_file(
                    source_file, config["remove_original_permanent"]
                )

    job["files"] = [target]
    job["golden"] = True
    return SourcePlan([target], golden_key, "single", True)


def media_parts_for_plan(plan, part_hours, cuts):
    """Return the media files to upload for a prepared plan"""
    if plan.mode == "convert_pipeline":
        return convert_to_mp4(plan.files, part_hours, cuts)
    source = plan.files[0]
    if cuts:
        return convert_to_mp4(plan.files, part_hours, cuts)
    if part_hours > 0:
        try:
            return split_without_encode(source, part_hours)
        except RuntimeError as error:
            print(f"  [警告] stream-copy 分段失敗，改用重新編碼: {error}")
            return convert_to_mp4(plan.files, part_hours, [])
    return plan.files


def cleanup_plan_cache(plan, part_hours, cuts):
    """Remove conversion / copy caches after a successful upload"""
    if plan.mode == "convert_pipeline" or cuts:
        remove_cached_conversion(plan.files, part_hours, cuts)
    elif part_hours > 0:
        remove_copy_cache(plan.files[0], part_hours)


def should_remove_sources(plan, config):
    """Whether post-upload source cleanup applies (golden files are never removed)"""
    return not plan.golden and config["remove_local_after_upload"]
