"""ffmpeg probing, conversion and conversion-cache management."""
import os
import shutil
import subprocess

from .config import CONVERTED_DIR
from .cuts import cuts_variant, format_timestamp, normalise_cuts
from .state import source_fingerprint

# ffmpeg encoding settings for the conversion (quality vs. speed/size)
CONVERT_CRF = 20
CONVERT_PRESET = 'medium'
CUT_QUALITY = 65
# Bump when the conversion pipeline changes so old caches are not reused
CONVERT_VERSION = 3


def describe_inputs(files):
    """Human-readable description of the conversion inputs"""
    if len(files) == 1:
        return files[0].name
    suffixes = sorted({
        path.suffix.lstrip('.').upper()
        for path in files
        if path.suffix
    })
    kind = '/'.join(suffixes) if suffixes else '影片'
    return f"合併 {len(files)} 個 {kind} 檔案"


def video_encode_args(encoder=None, quality=None):
    """Video encoder arguments for golden conversion or cut re-encoding"""
    if encoder == 'h264_videotoolbox':
        value = CUT_QUALITY if quality is None else quality
        return ['-c:v', 'h264_videotoolbox', '-q:v', str(value)]
    value = CONVERT_CRF if quality is None else quality
    return ['-c:v', 'libx264', '-preset', CONVERT_PRESET, '-crf', str(value)]


def converted_cache_path(fingerprint):
    """Single-file cache path for a given conversion fingerprint"""
    return CONVERTED_DIR / f"{fingerprint}.mp4"


def remove_cache_by_fingerprint(fingerprint):
    """Delete every cached artifact belonging to one conversion fingerprint"""
    paths = [
        converted_cache_path(fingerprint),
        CONVERTED_DIR / f"{fingerprint}.part.mp4",
    ]
    for path in paths:
        try:
            path.unlink()
        except OSError:
            pass


def conversion_fingerprint(files, cuts=None, encoder=None, quality=None):
    """Cache key for a conversion; includes cuts and encoder settings"""
    variant = f'v{CONVERT_VERSION}'
    normalised = normalise_cuts(cuts or [])
    if normalised:
        variant += f';cuts={cuts_variant(normalised)}'
    if encoder:
        variant += f';enc={encoder};q={quality}'
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
            raise ValueError('剪接範圍覆蓋咗整段影片')

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


def convert_to_mp4(files, cuts=None, encoder=None, quality=None):
    """Merge (if needed) and convert source videos to one MP4 (H.264/AAC).

    Any ranges in `cuts` are removed. The output is cached in CONVERTED_DIR so
    an interrupted upload resumes without re-encoding; the cache key covers the
    source files, cut ranges and encoder settings, so changing any of them
    produces a fresh conversion.
    """
    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('找不到 ffmpeg，請先安裝（macOS: brew install ffmpeg）')
    ffprobe = shutil.which('ffprobe')
    if not ffprobe:
        raise RuntimeError('找不到 ffprobe，轉檔需要 ffprobe（通常同 ffmpeg 一齊安裝）')

    cuts = normalise_cuts(cuts or [])
    try:
        fingerprint = conversion_fingerprint(
            files, cuts=cuts, encoder=encoder, quality=quality
        )
    except OSError as e:
        raise RuntimeError(f'無法讀取來源檔案: {e}')

    target = converted_cache_path(fingerprint)
    if target.exists() and target.stat().st_size > 0:
        print(f"  [轉檔] 重用已轉換檔案: {target.name}")
        return target

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

    source_desc = describe_inputs(files)
    if len(files) > 1:
        for file_path in files:
            print(f"    - {file_path.name}")
    print(f"  [轉檔] {source_desc} -> MP4（H.264/AAC），需時可能較長...")

    tmp_path = target.with_name(target.name + '.part.mp4')

    filter_complex, map_args = build_filter_graph(
        files, cuts, has_audio, fps_expr, sample_rate
    )
    stream_args = ['-filter_complex', filter_complex, *map_args, '-sn']
    if not has_audio:
        stream_args.append('-an')
    if cuts:
        removed = sum(end - start for start, end in cuts)
        print(f"  [剪接] 移除 {len(cuts)} 段，共 {format_timestamp(removed)}:")
        for start, end in cuts:
            print(f"    - {format_timestamp(start)} - {format_timestamp(end)}")

    cmd = [
        ffmpeg, '-y', '-hide_banner', '-loglevel', 'error', '-stats',
        *input_args,
        *stream_args,
        *video_encode_args(encoder, quality),
        '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-b:a', '192k',
        str(tmp_path),
    ]

    try:
        result = subprocess.run(cmd)
    except OSError as e:
        raise RuntimeError(f'無法執行 ffmpeg: {e}')

    if result.returncode != 0:
        remove_cache_by_fingerprint(fingerprint)
        raise RuntimeError(f'ffmpeg 轉檔失敗（exit code {result.returncode}）')

    os.replace(tmp_path, target)
    print(f"  [轉檔] 完成: {target.name}")
    return target


def remove_cached_conversion(files, cuts=None, encoder=None, quality=None):
    """Delete the cached conversion for these source files and settings"""
    try:
        fingerprint = conversion_fingerprint(
            files, cuts, encoder=encoder, quality=quality
        )
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


def move_converted_to_source(converted, target):
    """Move a converted file into place without overwriting an existing file"""
    if target.exists():
        raise FileExistsError(f'目標檔案已存在，唔會覆蓋: {target}')
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + '.part')
    shutil.move(str(converted), str(tmp))
    os.replace(tmp, target)
    return target
