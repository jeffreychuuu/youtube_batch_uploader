"""Golden source detection and coexistence rules."""
from .files import converted_source_path


def source_provenance_matches(recorded, files):
    """Whether the current source files are a subset of the recorded golden's sources

    A subset is allowed: originals may already have been removed after a
    verified export. A path that is not in the recorded set means the source
    group changed (for example a new VOB segment), so the golden cannot be
    trusted.
    """
    recorded_files = set(recorded.get("source_files") or [])
    current_files = {str(path.resolve()) for path in files}
    return bool(current_files) and current_files <= recorded_files


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
                "        請檢查 convert_state.json 嘅 golden_sources，"
                "或重新命名其中一個來源。"
            )
        job["skip_reason"] = "golden"
        skipped.append(job["display_name"])
    return skipped
