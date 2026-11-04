"""Convert source videos to golden MP4s without uploading."""
from .files import (
    build_upload_jobs, converted_output_path, prune_empty_dirs,
    scan_video_files,
)
from .golden import (
    ensure_unique_converted_targets, mark_golden_jobs,
    skip_converted_source_jobs,
)
from .pipeline import prepare_source, remove_original_sources
from .state import load_convert_state, save_convert_state


def run_convert_only(config):
    """Convert every convert-extension job and stop; never touches YouTube"""
    video_files = scan_video_files(
        config["root_folder"], config["convert_extensions"]
    )
    convert_extensions = {ext.lower() for ext in config["convert_extensions"]}
    jobs = build_upload_jobs(
        video_files,
        convert_extensions,
        config["combine"],
        root_folder=config["root_folder"],
        name_source=config["combined_name_source"],
    )
    if not jobs:
        print(f"[提示] 在 {config['root_folder']} 內找不到相容的影片檔案。")
        return {"converted": 0, "skipped": 0, "failed": 0}

    output_folder = config.get("output_folder")
    if output_folder:
        for job in jobs:
            if job.get("convert"):
                job["output_path"] = str(
                    converted_output_path(job, output_folder)
                )
    ensure_unique_converted_targets(jobs)

    state = load_convert_state()
    mark_golden_jobs(jobs, state)
    skipped_sources = skip_converted_source_jobs(jobs, state)
    for job in jobs:
        if job.get("skip_reason") == "golden":
            remove_original_sources(job["files"], config)

    pending = [
        job for job in jobs
        if not job.get("skip_reason") and not job.get("golden")
    ]
    skipped = len(skipped_sources)
    converted = 0
    failed = 0

    for idx, job in enumerate(pending, start=1):
        if not job["convert"]:
            print(f"[略過] 唔需要轉檔：{job['display_name']}")
            skipped += 1
            continue
        print(f"\n[轉檔 {idx}/{len(pending)}] {job['display_name']}")
        try:
            prepare_source(job, state, config)
            converted += 1
        except KeyboardInterrupt:
            print("\n\n[已中斷] 進度已儲存。")
            save_convert_state(state)
            if config.get("remove_empty_folders"):
                prune_empty_dirs(config["root_folder"])
            return {
                "converted": converted, "skipped": skipped, "failed": failed,
            }
        except Exception as error:
            print(f"  [例外錯誤] {job['display_name']}: {error}")
            failed += 1

    save_convert_state(state)
    if config.get("remove_empty_folders"):
        prune_empty_dirs(config["root_folder"])
    print(
        f"\n[轉檔完成] 成功 {converted}，跳過 {skipped}，失敗 {failed}"
    )
    return {"converted": converted, "skipped": skipped, "failed": failed}
