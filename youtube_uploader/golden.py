"""Golden source detection and coexistence rules."""
from .files import converted_source_path


def source_provenance_matches(recorded, files):
    """Whether a recorded golden belongs to the same source files"""
    recorded_files = recorded.get("source_files") or []
    current_files = sorted(str(path.resolve()) for path in files)
    return sorted(recorded_files) == current_files


def ensure_unique_converted_targets(jobs):
    """Refuse two convert jobs that would write the same golden file"""
    seen = {}
    for job in jobs:
        if not job.get("convert"):
            continue
        target = str(converted_source_path(job).resolve())
        if target in seen:
            raise SystemExit(
                f"[錯誤] 多個來源會輸出到同一個 golden 檔案：{target}\n"
                f"        - {seen[target]}\n"
                f"        - {job['display_name']}\n"
                "        請重新命名其中一個來源。"
            )
        seen[target] = job["display_name"]


def mark_golden_jobs(jobs, state):
    """Mark single-file jobs recorded as golden sources"""
    golden = state.get("golden_sources", {})
    for job in jobs:
        if len(job["files"]) != 1:
            continue
        key = str(job["files"][0].resolve())
        info = golden.get(key)
        if info:
            job["golden"] = True
            job["title"] = info.get("title", job["title"])
            source_files = info.get("source_files")
            job["cut_namespace"] = (
                "files"
                if source_files is not None and len(source_files) == 1
                else "folders"
            )


def skip_converted_source_jobs(jobs, state):
    """Skip source jobs whose recorded golden file already exists"""
    golden = state.get("golden_sources", {})
    skipped = []
    for job in jobs:
        if job.get("golden"):
            continue
        target = converted_source_path(job)
        recorded = golden.get(str(target.resolve()))
        if not (recorded and target.exists()):
            continue
        if not source_provenance_matches(recorded, job["files"]):
            raise SystemExit(
                f"[錯誤] golden 目標 {target} 已記錄畀另一組來源，"
                f"唔會跳過 {job['display_name']}。\n"
                "        請檢查 upload_state.json 嘅 golden_sources，"
                "或重新命名其中一個來源。"
            )
        job["skip_reason"] = "golden"
        skipped.append(job["display_name"])
    return skipped
