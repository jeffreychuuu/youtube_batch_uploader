"""ffmpeg probing, conversion, splitting and conversion-cache management."""
import hashlib
import os
import shutil
import subprocess

from .config import CONVERTED_DIR
from .cuts import cuts_variant, format_timestamp, normalise_cuts
from .state import source_fingerprint

# ffmpeg encoding settings for the conversion (quality vs. speed/size)
CONVERT_CRF = 20
CONVERT_PRESET = 'medium'
# Bump when the conversion pipeline changes so old caches are not reused
CONVERT_VERSION = 2


def converted_cache_path(fingerprint):
    """Single-file cache path for a given conversion fingerprint"""
    return CONVERTED_DIR / f"{fingerprint}.mp4"


def converted_marker_path(fingerprint):
    """Marker file written only after a conversion has completed"""
    return CONVERTED_DIR / f"{fingerprint}.done"


def converted_partial_dir(fingerprint):
    """Temporary directory used while a split conversion is running"""
    return CONVERTED_DIR / f"{fingerprint}.partial"


def load_cached_parts(fingerprint):
    """Return the cached part paths if a complete conversion exists, else None"""
    marker = converted_marker_path(fingerprint)
    try:
        count = int(marker.read_text(encoding='utf-8').strip())
    except (OSError, ValueError):
        return None
    if count < 1:
        return None
    if count == 1:
        path = converted_cache_path(fingerprint)
        return [path] if path.exists() else None
    paths = [
        CONVERTED_DIR / f"{fingerprint}.part{index:03d}.mp4"
        for index in range(1, count + 1)
    ]
    return paths if all(path.exists() for path in paths) else None


def remove_cache_by_fingerprint(fingerprint):
    """Delete every cached artifact belonging to one conversion fingerprint"""
    paths = [
        converted_cache_path(fingerprint),
        converted_marker_path(fingerprint),
        CONVERTED_DIR / f"{fingerprint}.done.tmp",
        CONVERTED_DIR / f"{fingerprint}.part.mp4",
        CONVERTED_DIR / f"{fingerprint}.concat.txt",
    ]
    paths.extend(CONVERTED_DIR.glob(f"{fingerprint}.part*.mp4"))
    for path in paths:
        try:
            path.unlink()
        except OSError:
            pass
    shutil.rmtree(converted_partial_dir(fingerprint), ignore_errors=True)


def write_cache_marker(fingerprint, count):
    """Atomically record how many parts a completed conversion has"""
    marker = converted_marker_path(fingerprint)
    tmp = marker.with_name(marker.name + '.tmp')
    tmp.write_text(str(count), encoding='utf-8')
    os.replace(tmp, marker)


def conversion_fingerprint(files, part_duration_hours, cuts=None):
    """Cache key for a conversion; includes part length and (when present) cut ranges"""
    variant = f'v{CONVERT_VERSION};part={part_duration_hours}'
    normalised = normalise_cuts(cuts or [])
    if normalised:
        variant += f';cuts={cuts_variant(normalised)}'
    _, _, fingerprint = source_fingerprint(files, variant=variant)
    return fingerprint


def ffprobe_value(input_args, entries, ffprobe):
    """Read the first value from ffprobe for the given input"""
    result = subprocess.run(
        [
            ffprobe, '-v', 'error', *input_args,
            '-show_entries', entries,
            '-of', 'default=nw=1:nk=1',
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    lines = result.stdout.strip().splitlines()
    return lines[0].strip() if lines else None


def probe_frame_rate(files, ffprobe):
    """Get a usable frame-rate expression (e.g. '25/1') from the first valid input"""
    for file_path in files:
        value = ffprobe_value(['-i', str(file_path)], 'stream=r_frame_rate', ffprobe)
        if not value:
            continue
        try:
            num, den = value.split('/')
            if float(num) > 0 and float(den) > 0:
                return value
        except (ValueError, ZeroDivisionError):
            continue
    return None


def probe_sample_rate(files, ffprobe):
    """Get the audio sample rate from the first valid input"""
    for file_path in files:
        value = ffprobe_value(['-i', str(file_path)], 'stream=sample_rate', ffprobe)
        if value and value.isdigit() and int(value) > 0:
            return int(value)
    return None


def input_has_audio(input_args, ffprobe):
    """Check whether an input has an audio stream"""
    result = subprocess.run(
        [
            ffprobe, '-v', 'error', *input_args,
            '-select_streams', 'a:0',
            '-show_entries', 'stream=index',
            '-of', 'csv=p=0',
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f'ffprobe 無法讀取音訊資訊: {result.stderr.strip()}')
    return bool(result.stdout.strip())


def build_filter_graph(files, cuts, has_audio, fps_expr, sample_rate):
    """Re-time every input, merge the files, then remove the cut ranges.

    Re-timing with setpts/asetpts based on frame/sample counts ignores broken
    DVD timestamps (some VOBs jump to ~24 hours), which otherwise make ffmpeg
    duplicate frames and produce hours of frozen video.
    """
    graph = []
    count = len(files)
    for index in range(count):
        graph.append(f'[{index}:v:0]setpts=N/(({fps_expr})*TB)[nv{index}]')
        if has_audio:
            graph.append(f'[{index}:a:0]asetpts=N/({sample_rate}*TB)[na{index}]')

    if count > 1:
        if has_audio:
            inputs = ''.join(f'[nv{i}][na{i}]' for i in range(count))
            graph.append(f'{inputs}concat=n={count}:v=1:a=1[sv][sa]')
            sv, sa = 'sv', 'sa'
        else:
            inputs = ''.join(f'[nv{i}]' for i in range(count))
            graph.append(f'{inputs}concat=n={count}:v=1:a=0[sv]')
            sv, sa = 'sv', None
    else:
        sv = 'nv0'
        sa = 'na0' if has_audio else None

    cuts = normalise_cuts(cuts or [])
    if cuts:
        keep = []
        previous = 0
        for start, end in cuts:
            if start > previous:
                keep.append((previous, start))
            previous = max(previous, end)
        keep.append((previous, None))
        keep = [(start, end) for start, end in keep if end is None or end > start]
        if not keep:
            raise ValueError('cut 範圍覆蓋咗整段影片')

        if len(keep) > 1:
            graph.append(
                f'[{sv}]split={len(keep)}'
                + ''.join(f'[sv{i}]' for i in range(len(keep)))
            )
            if has_audio:
                graph.append(
                    f'[{sa}]asplit={len(keep)}'
                    + ''.join(f'[sa{i}]' for i in range(len(keep)))
                )
            sv_inputs = [f'sv{i}' for i in range(len(keep))]
            sa_inputs = [f'sa{i}' for i in range(len(keep))]
        else:
            sv_inputs = [sv]
            sa_inputs = [sa]

        for index, (start, end) in enumerate(keep):
            end_arg = '' if end is None else f':end={end}'
            graph.append(
                f'[{sv_inputs[index]}]trim=start={start}{end_arg},setpts=PTS-STARTPTS[cv{index}]'
            )
            if has_audio:
                graph.append(
                    f'[{sa_inputs[index]}]atrim=start={start}{end_arg},asetpts=PTS-STARTPTS[ca{index}]'
                )
        if has_audio:
            labels = ''.join(f'[cv{i}][ca{i}]' for i in range(len(keep)))
            graph.append(f'{labels}concat=n={len(keep)}:v=1:a=1[vcat][acat]')
            graph.append('[vcat]yadif=deint=interlaced[vout]')
            map_args = ['-map', '[vout]', '-map', '[acat]']
        else:
            labels = ''.join(f'[cv{i}]' for i in range(len(keep)))
            graph.append(f'{labels}concat=n={len(keep)}:v=1:a=0[vcat]')
            graph.append('[vcat]yadif=deint=interlaced[vout]')
            map_args = ['-map', '[vout]']
    else:
        graph.append(f'[{sv}]yadif=deint=interlaced[vout]')
        if has_audio:
            map_args = ['-map', '[vout]', '-map', f'[{sa}]']
        else:
            map_args = ['-map', '[vout]']

    return ';'.join(graph), map_args


def convert_to_mp4(files, part_duration_hours, cuts=None):
    """Merge (if needed) and convert source videos to MP4 (H.264/AAC) with ffmpeg.

    Long videos are split into parts according to part_duration_hours, and any
    ranges in `cuts` are removed before splitting. Finished parts are cached in
    CONVERTED_DIR so an interrupted upload resumes without re-encoding; the
    cache key covers the source files, part length and cut ranges, so changing
    any of them produces a fresh conversion.

    Returns a list of part paths (one item when no split is needed).
    """
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('找不到 ffmpeg，請先安裝（macOS: brew install ffmpeg）')
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        raise RuntimeError('找不到 ffprobe，轉檔需要 ffprobe（通常同 ffmpeg 一齊安裝）')

    cuts = normalise_cuts(cuts or [])
    part_hours = part_duration_hours
    try:
        fingerprint = conversion_fingerprint(files, part_hours, cuts=cuts)
    except OSError as e:
        raise RuntimeError(f'無法讀取來源檔案: {e}')

    cached = load_cached_parts(fingerprint)
    if cached:
        print(f"  [轉檔] 重用已轉換檔案: {len(cached)} 段")
        return cached

    # Remove leftovers from an interrupted previous attempt
    remove_cache_by_fingerprint(fingerprint)
    CONVERTED_DIR.mkdir(parents=True, exist_ok=True)

    input_args = []
    for file_path in files:
        input_args.extend(['-i', str(file_path)])

    fps_expr = probe_frame_rate(files, ffprobe)
    if not fps_expr:
        raise RuntimeError('無法讀取來源影片 frame rate，請檢查檔案')
    audio_flags = [input_has_audio(['-i', str(f)], ffprobe) for f in files]
    has_audio = any(audio_flags)
    if has_audio and not all(audio_flags):
        raise RuntimeError('部分來源檔案有音軌、部分冇，暫時唔支援呢種混合來源')
    sample_rate = probe_sample_rate(files, ffprobe) if has_audio else None
    if has_audio and not sample_rate:
        raise RuntimeError('無法讀取來源音訊 sample rate，請檢查檔案')

    if len(files) == 1:
        source_desc = files[0].name
    else:
        source_desc = f"合併 {len(files)} 段 VOB"
        print(f"  [轉檔] {source_desc} -> MP4（H.264/AAC），需時可能較長...")
        for file_path in files:
            print(f"    - {file_path.name}")

    split = part_hours > 0
    if split:
        segment_seconds = max(1, int(part_hours * 3600) - 1)
        partial_dir = converted_partial_dir(fingerprint)
        partial_dir.mkdir(parents=True, exist_ok=True)
        output_pattern = str(partial_dir / 'part%03d.mp4')
        if len(files) == 1:
            print(
                f"  [轉檔] {source_desc} -> MP4（H.264/AAC），"
                f"每 {part_hours:g} 小時一段，需時可能較長..."
            )
        output_args = [
            '-f', 'segment',
            '-segment_time', str(segment_seconds),
            '-segment_start_number', '1',
            '-reset_timestamps', '1',
            '-force_key_frames', f'expr:gte(t,n_forced*{segment_seconds})',
            output_pattern,
        ]
    else:
        partial_dir = None
        tmp_path = converted_cache_path(fingerprint).with_name(
            converted_cache_path(fingerprint).name + '.part.mp4'
        )
        if len(files) == 1:
            print(f"  [轉檔] {source_desc} -> MP4（H.264/AAC），需時可能較長...")
        output_args = [str(tmp_path)]

    filter_complex, map_args = build_filter_graph(
        files, cuts, has_audio, fps_expr, sample_rate
    )
    stream_args = ['-filter_complex', filter_complex, *map_args, '-sn']
    if not has_audio:
        stream_args.append('-an')
    if cuts:
        removed = sum(end - start for start, end in cuts)
        print(f"  [剪輯] 移除 {len(cuts)} 段，共 {format_timestamp(removed)}:")
        for start, end in cuts:
            print(f"    - {format_timestamp(start)} - {format_timestamp(end)}")

    cmd = [
        ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-stats',
        *input_args,
        *stream_args,
        '-c:v', 'libx264', '-preset', CONVERT_PRESET, '-crf', str(CONVERT_CRF),
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '192k',
        *output_args,
    ]

    try:
        result = subprocess.run(cmd)
    except OSError as e:
        raise RuntimeError(f'無法執行 ffmpeg: {e}')

    if result.returncode != 0:
        remove_cache_by_fingerprint(fingerprint)
        raise RuntimeError(f'ffmpeg 轉檔失敗（exit code {result.returncode}）')

    if split:
        temp_parts = [
            path for path in sorted(partial_dir.glob('part*.mp4'))
            if path.stat().st_size > 0
        ]
        if not temp_parts:
            remove_cache_by_fingerprint(fingerprint)
            raise RuntimeError('ffmpeg 沒有產生任何影片分段')
        if len(temp_parts) == 1:
            target = converted_cache_path(fingerprint)
            os.replace(temp_parts[0], target)
            result_parts = [target]
        else:
            result_parts = []
            for index, temp_path in enumerate(temp_parts, start=1):
                target = CONVERTED_DIR / f"{fingerprint}.part{index:03d}.mp4"
                os.replace(temp_path, target)
                result_parts.append(target)
        shutil.rmtree(partial_dir, ignore_errors=True)
    else:
        target = converted_cache_path(fingerprint)
        os.replace(tmp_path, target)
        result_parts = [target]

    write_cache_marker(fingerprint, len(result_parts))
    if len(result_parts) == 1:
        print(f"  [轉檔] 完成: {result_parts[0].name}")
    else:
        print(f"  [轉檔] 完成: 共 {len(result_parts)} 段")
    return result_parts


def remove_cached_conversion(files, part_duration_hours, cuts=None):
    """Delete every cached part for these source files, part length and cut ranges"""
    try:
        fingerprint = conversion_fingerprint(files, part_duration_hours, cuts)
    except OSError:
        return
    remove_cache_by_fingerprint(fingerprint)


def verify_video(path):
    """Raise RuntimeError unless path is a non-empty video with a duration"""
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError(f"轉檔輸出無效（空檔案）: {path}")
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        raise RuntimeError('找不到 ffprobe，請先安裝 ffmpeg')
    result = subprocess.run([
        ffprobe, '-v', 'error',
        '-show_entries', 'stream=codec_type:format=duration',
        '-of', 'default=nw=1', str(path),
    ], capture_output=True, text=True)
    if result.returncode != 0 or 'codec_type=video' not in result.stdout:
        raise RuntimeError(f"轉檔輸出無效（沒有視訊串流）: {path}")
    durations = [
        float(line.split('=', 1)[1])
        for line in result.stdout.splitlines()
        if line.startswith('duration=')
    ]
    if not durations or max(durations) <= 0:
        raise RuntimeError(f"轉檔輸出無效（duration 為 0）: {path}")


def copy_fingerprint(source, part_hours):
    """Cache key for a stream-copy split"""
    stat = source.stat()
    digest = hashlib.sha1()
    for value in (str(source.resolve()), stat.st_size, stat.st_mtime_ns, part_hours):
        digest.update(str(value).encode('utf-8'))
        digest.update(b'\0')
    return digest.hexdigest()[:20]


def copy_cache_dir(source, part_hours):
    """Cache directory for a stream-copy split"""
    return CONVERTED_DIR / f"copy-{copy_fingerprint(source, part_hours)}"


def split_without_encode(source, part_hours):
    """Split one file with stream copy; raise RuntimeError if ffmpeg cannot"""
    if part_hours <= 0:
        return [source]
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('找不到 ffmpeg，請先安裝')
    segment_seconds = max(1, int(part_hours * 3600) - 1)
    cache_dir = copy_cache_dir(source, part_hours)
    marker = cache_dir / 'done'
    cached = sorted(cache_dir.glob(f"part*{source.suffix}"))
    if marker.exists() and cached:
        return cached
    shutil.rmtree(cache_dir, ignore_errors=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(cache_dir / f"part%03d{source.suffix}")
    result = subprocess.run([
        ffmpeg, '-y', '-hide_banner', '-loglevel', 'error',
        '-i', str(source),
        '-map', '0:v:0', '-map', '0:a?', '-c', 'copy',
        '-f', 'segment', '-segment_time', str(segment_seconds),
        '-reset_timestamps', '1', '-segment_start_number', '1', pattern,
    ])
    parts = sorted(cache_dir.glob(f"part*{source.suffix}"))
    if result.returncode != 0 or not parts:
        shutil.rmtree(cache_dir, ignore_errors=True)
        raise RuntimeError(f'stream-copy 分段失敗: {source}')
    if len(parts) == 1:
        ffprobe = shutil.which('ffprobe')
        duration_text = (
            ffprobe_value(['-i', str(source)], 'format=duration', ffprobe)
            if ffprobe else None
        )
        try:
            duration = float(duration_text)
        except (TypeError, ValueError):
            duration = 0
        if duration > segment_seconds * 1.5:
            shutil.rmtree(cache_dir, ignore_errors=True)
            raise RuntimeError(f'keyframe 間距太大，無法用 stream-copy 分段: {source}')
    marker.write_text(str(len(parts)), encoding='utf-8')
    return parts


def remove_copy_cache(source, part_hours):
    """Delete a stream-copy split cache"""
    shutil.rmtree(copy_cache_dir(source, part_hours), ignore_errors=True)


def move_converted_to_source(converted, target):
    """Move a converted file into place without overwriting an existing file"""
    if target.exists():
        raise FileExistsError(f'目標檔案已存在，唔會覆蓋: {target}')
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + '.part')
    shutil.move(str(converted), str(tmp))
    os.replace(tmp, target)
    return target
