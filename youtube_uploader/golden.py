"""Golden source detection and coexistence rules."""
from .files import converted_source_path


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
        if job.get("golden") or not job.get("convert"):
            continue
        target = converted_source_path(job)
        if str(target.resolve()) in golden and target.exists():
            job["skip_reason"] = "golden"
            skipped.append(job["display_name"])
    return skipped
