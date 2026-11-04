"""Source preparation and media selection for the upload pipeline."""
from dataclasses import dataclass

from .files import converted_source_path, remove_local_file
from .ffmpeg import (
    convert_to_mp4, move_converted_to_source, remove_cached_conversion,
    verify_video,
)
from .golden import source_provenance_matches
from .state import record_golden_source, save_convert_state


@dataclass
class SourcePlan:
    files: list
    key: str
    golden: bool


def prepare_source(job, state, config):
    """Convert a source job into the files and state key to upload from"""
    files = job["files"]
    if job.get("golden"):
        return SourcePlan(files, str(files[0].resolve()), True)
    if not job["convert"]:
        return SourcePlan(files, str(files[0].resolve()), False)

    target = converted_source_path(job)
    golden_key = str(target.resolve())
    recorded = state.get(golden_key)
    if target.exists():
        if not recorded:
            raise SystemExit(
                f"[錯誤] golden 目標檔案已存在但未記錄喺 state：{target}\n"
                "        請先檢查／移除該檔案，或還原 convert_state.json。"
            )
        if not source_provenance_matches(recorded, files):
            raise SystemExit(
                f"[錯誤] golden 目標 {target} 屬於另一組來源，唔會重用。\n"
                "        請檢查 convert_state.json 嘅 golden_sources，"
                "或重新命名其中一個來源。"
            )
        job["files"] = [target]
        job["golden"] = True
        return SourcePlan([target], golden_key, True)

    converted = convert_to_mp4(files)
    try:
        verify_video(converted)
    except Exception:
        remove_cached_conversion(files)
        raise
    move_converted_to_source(converted, target)
    verify_video(target)

    record_golden_source(state, target, job["title"], files)
    save_convert_state(state)
    remove_original_sources(files, config)

    job["files"] = [target]
    job["golden"] = True
    return SourcePlan([target], golden_key, True)


def remove_original_sources(files, config):
    """Remove source files once their verified golden copy exists"""
    if not config["remove_original_after_conversion"]:
        return
    for source_file in files:
        if source_file.exists():
            remove_local_file(source_file, config["remove_original_permanent"])


def media_parts_for_plan(plan, cuts):
    """Return the media files to upload for a prepared plan"""
    if cuts:
        return [convert_to_mp4(plan.files, cuts)]
    return plan.files


def cleanup_plan_cache(plan, cuts):
    """Remove the conversion cache after a successful upload"""
    if cuts:
        remove_cached_conversion(plan.files, cuts)


def should_remove_sources(plan, config):
    """Whether post-upload source cleanup applies (golden files are never removed)"""
    return not plan.golden and config["remove_local_after_upload"]
