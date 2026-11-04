# YouTube Batch Uploader

[English](README.md) | [繁體中文](README.zh-TW.md)

Upload every video in a local folder to YouTube in bulk using Python, the official YouTube Data API v3, and OAuth 2.0, with automatic addition to a specified playlist.

Supports **resumable uploads**: if the connection drops, the machine shuts down, or you press Ctrl+C mid-upload, the next run resumes from the breakpoint and never re-uploads completed videos.

---

## Features

- Recursively scans a folder and uploads every supported video
- VOB (DVD) support: multi-segment `VTS_01_1.VOB`, `VTS_01_2.VOB`, ... files are merged and transcoded to MP4 with ffmpeg; the merged video is titled `<folder> [combined]`
- Each video is uploaded as a single file
- Cut-list support: remove time ranges flagged by YouTube before upload (`config/cuts_config.json`)
- Golden source support: keep the converted MP4 and reuse it for later cuts and uploads; `convert_only.py` prepares golden files without YouTube authorization
- Optional ffmpeg transcoding for other formats (e.g. AVI) via `convert_extensions`
- Adds uploaded videos to a playlist (creating it if needed); reruns never add duplicates
- Progress is stored in `state/upload_state.json`; completed files are skipped on rerun
- Unfinished uploads keep their resumable session and continue from the breakpoint
- Automatic exponential backoff retry on network errors / 429 / 5xx
- Configurable chunk size (default 32 MB, must be a multiple of 256 KB)
- Progress file written atomically, so a forced termination cannot corrupt it
- Optionally removes non-golden local files after a successful upload (move to Trash or delete permanently); golden sources are always kept

---

## Requirements

- macOS (Windows / Linux also work; adjust paths as needed)
- Python 3.8 or later
- ffmpeg (required whenever conversion is needed — any format in `convert_extensions` — and whenever cuts are applied)
- A YouTube / Google account with a channel; uploads longer than 15 minutes require a verified account (YouTube's maximum is 12 hours / 256 GB per video)
- A Google Cloud project with the YouTube Data API v3 enabled

---

## 1. Prerequisites

### 1.1 Install dependencies

Run in Terminal:

```bash
pip3 install google-api-python-client google-auth-oauthlib google-auth-httplib2
```

> If `pip3` is not available, first check that Python 3 is installed: `python3 --version`.
> If the `python` command does not exist on your system, use `python3` instead (or create your own alias / wrapper).

### 1.2 Install ffmpeg (for conversion and cuts)

Some formats (for example VOB) cannot be uploaded to YouTube directly. The script converts any extension listed in `convert_extensions` to MP4 with ffmpeg, and applies cuts with ffmpeg, so ffmpeg must be on your `PATH`:

```bash
brew install ffmpeg          # macOS
# sudo apt install ffmpeg    # Debian / Ubuntu
```

Windows: download a build from [ffmpeg.org](https://ffmpeg.org/) and add it to `PATH`.

> ffmpeg is required for any extension listed in `convert_extensions` (default: `.vob`; add `.avi`, `.m2ts`, etc. to convert those too) and whenever cuts are applied. Formats outside `convert_extensions` are uploaded as-is.

### 1.3 Create YouTube API credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/) and create a new project (or select an existing one).
2. Go to **APIs & Services → Library**, search for **YouTube Data API v3**, and click **Enable**.
3. Go to **APIs & Services → OAuth consent screen**:
   - Select **External** as the user type
   - Fill in the app name, user support email, developer contact, and other details
   - Add the Google / YouTube account you will upload with under **Test users**
4. Go to **APIs & Services → Credentials → Create Credentials → OAuth client ID**:
   - Select **Desktop app** as the application type
   - After creating it, click **Download JSON** to download the credential
5. Rename the downloaded JSON to **`client_secrets.json`** and place it in the project root.

> A correct Desktop App credential contains an `"installed"` block in `client_secrets.json`.

### 1.4 First-time sign-in and authorization

The first run opens a browser asking you to sign in to your Google account and authorize access.

- Choose the **YouTube channel** you want to upload to
- After authorization, `token.pickle` is created in the project root; later runs will not ask you to sign in again

> `token.pickle` is equivalent to your login credential. **Never share it or upload it anywhere.**

---

## 2. Video List

The script recursively scans every video file under `root_folder` and uses the **file name (without extension) as the video title**. The only exception is a merged multi-segment VOB, which is titled `<folder> [combined]` (see below).

The upload scan reads the extensions in `video_extensions` in `config/upload_config.json` (default `[".mp4"]`, the golden output). Add other extensions only if those files are uploadable as-is.

**AVI** is officially supported by YouTube and is uploaded as-is by default. If a particular AVI file uses a codec YouTube cannot process, add `".avi"` to `convert_extensions` in `config/convert_config.json` to transcode it to MP4 with ffmpeg first.

**VOB** is not officially supported by YouTube, so `.vob` is in `convert_extensions` by default. The script then:

- Groups files such as `VTS_01_1.VOB`, `VTS_01_2.VOB`, ... by title set and merges them in numeric order into one video
- Titles the merged video `<folder> [combined]` (using the parent folder name when the files are inside `VIDEO_TS`)
- Skips DVD menu files (`*_0.VOB` and `VIDEO_TS.VOB`)
- Uses the original file name as the title for a standalone VOB
- Converts the source to a single MP4 (H.264/AAC) with ffmpeg
- Keeps the converted MP4 as a golden source next to the source (or its `VIDEO_TS` parent)
- Caches cut re-encodes in `.converted/` so an interrupted upload resumes without re-encoding; the cache is removed after a successful upload

In other words, you only need:

```text
/path/to/your/videos/
├── Video A.mp4
├── Video B.mp4
└── subfolder/
    └── Video C.mp4
```

All three videos will be uploaded with the titles "Video A", "Video B", and "Video C".

### 2.1 Cut-list (trim/remove flagged segments)

If YouTube flags a video and gives time ranges to remove, create `config/cuts_config.json`:

```json
{
  "cuts": {
    "My Video [combined]": [
      "21:55-22:45",
      "23:20-24:35",
      "24:45-26:25"
    ]
  }
}
```

All cut ranges live under the `cuts` key. A flat range list is a single batch on the **full, uncut golden video timeline**.

If YouTube flags an **already-cut upload**, add another batch instead of calculating offsets yourself:

```json
{
  "cuts": {
    "My Video [combined]": [
      ["21:55-22:45", "23:20-24:35"],
      ["25:10-26:40"]
    ]
  }
}
```

- The nested form is an ordered list of batches:
  - batch 1 uses the golden timeline
  - batch 2 uses the timeline after batch 1 was cut (the uploaded video)
  - batch 3 uses the timeline after batches 1+2, and so on
- Every batch is automatically translated back to the golden timeline; ffmpeg still runs **once** with the combined result.
- A batch range may span previously removed segments; it is split into the corresponding golden-timeline pieces.
- Example: batch 1 cuts `20:00-30:00`; YouTube flags `25:00-27:00` on the uploaded video. Adding it as batch 2 makes the script map it to `35:00-37:00` on the golden timeline.

**Cut encoder settings.** Two reserved top-level keys control how cut re-encodes are produced:

```json
{
  "cut_encoder": "h264_videotoolbox",
  "cut_quality": 65,
  "cuts": {
    "Video Title": ["20:00-30:00"]
  }
}
```

- `cut_encoder`: `libx264` (default) or `h264_videotoolbox` (macOS hardware encoder, much faster)
- `cut_quality`: `libx264` uses CRF `0`–`51` (lower = better quality, larger file); `h264_videotoolbox` uses `q:v` `0`–`100` (higher = better quality, larger file). `65` is a balanced mid-high hardware setting; use `70`–`75` for higher quality or `50`–`60` for smaller files.

These settings apply to every cut re-encode. Golden conversion uses `convert_encoder` / `convert_quality` in `config/convert_config.json` (default `libx264` CRF 20).

**Automatic description.** Every upload with cuts automatically gets `因違反 YouTube 政策，已剪走以下片段（共 X）：` followed by the removed ranges, where `X` is the total removed duration.

Other rules:

- Inside `cuts`, the key is the video title: `<folder> [combined]` for a combined group, or the filename (without extension) for a single file.
- Time formats: `MM:SS` or `HH:MM:SS`. Separate start/end with `-`, `–`, `—`, `~`, or `to`.
- JSON does not support comments; `config/cuts_config.example.json` shows the format.
- The ranges are removed during the ffmpeg conversion.
- Changing the cut list invalidates the conversion cache. If the job was already uploaded and the source files still exist, the script re-processes it and uploads a corrected version. The old YouTube video is **not** deleted automatically.
- Golden files are never removed by `remove_local_after_upload`; original source removal is controlled by `remove_original_after_conversion`.
- `config/cuts_config.json` is gitignored; see `config/cuts_config.example.json` for the format.

---

## 3. Configuration and Usage

### 3.1 Edit the configuration

Settings live in separate files. Copy the examples, then edit them:

```bash
cp config/convert_config.example.json config/convert_config.json
cp config/upload_config.example.json config/upload_config.json

# optional: only if you already have cut ranges
cp config/cuts_config.example.json config/cuts_config.json
```

**`config/convert_config.json`** — what to scan and how to build golden MP4s:

```json
{
  "root_folder": "/path/to/your/videos",
  "convert_extensions": [".vob"],
  "combine": true,
  "combined_name_source": "folder",
  "output_folder": "",
  "convert_encoder": "libx264",
  "convert_quality": 20,
  "remove_original_after_conversion": false,
  "remove_original_permanent": false,
  "remove_empty_folders": false
}
```

| Setting | Description |
|---|---|
| `root_folder` | Source folder for videos; all subfolders are scanned recursively |
| `convert_extensions` | Extensions transcoded to MP4 with ffmpeg (default `[".vob"]`); add `".avi"` to force AVI transcoding. The convert script scans exactly these extensions |
| `combine` | `true` (default) merges VOB title sets and groups same-folder files of **any** extension listed in `convert_extensions`, in natural order; `false` makes every file its own job |
| `combined_name_source` | Combined title source: `folder` (default, immediate folder name), `root` (configured `root_folder` name), or `prefix` (common prefix of all video filenames in that folder; VOB groups fall back to the folder name) |
| `output_folder` | Empty (default) keeps the golden MP4 next to its source. Set a folder to write goldens directly into it; point `upload_config.root_folder` at it for uploads. Titles must be unique — conversion stops instead of overwriting |
| `convert_encoder` | Encoder for golden conversion: `libx264` (default, best compression) or `h264_videotoolbox` (macOS hardware, faster but less efficient) |
| `convert_quality` | CRF `0`–`51` for `libx264` (lower = better quality, larger file); `q:v` `0`–`100` for `h264_videotoolbox` (higher = better quality, larger file) |
| `remove_original_after_conversion` | `true` = remove the original files after the converted MP4 is verified; also cleans up sources of already-converted jobs on a later run |
| `remove_original_permanent` | Removal method for conversion-time cleanup: `false` = move to Trash (default) / `true` = delete permanently |
| `remove_empty_folders` | `true` = after conversion cleanup, also remove source folders that become empty, up to (but not including) `root_folder`. Only meaningful when `remove_original_after_conversion` is `true` |

**`config/upload_config.json`** — how to scan and publish (fully independent):

```json
{
  "root_folder": "/path/to/your/videos",
  "video_extensions": [".mp4"],
  "playlist_name": "My Playlist",
  "privacy_status": "unlisted",
  "made_for_kids": false,
  "chunk_size_mb": 32,
  "max_retries": 5,
  "remove_local_after_upload": false,
  "remove_local_permanent": false
}
```

| Setting | Description |
|---|---|
| `root_folder` | Source folder for uploads; scanned independently from the convert config |
| `video_extensions` | Extensions to scan for uploads (default `[".mp4"]`, the golden output). Add others only if they are uploadable as-is |
| `playlist_name` | Target playlist name; created automatically if it does not exist. Set to `""` to skip adding to a playlist |
| `privacy_status` | `public` / `unlisted` (visible via link) / `private` |
| `made_for_kids` | `True` = content made for kids; `False` = not made for kids |
| `chunk_size_mb` | Size of each upload chunk; must be a multiple of 256 KB. 16–32 recommended for large files |
| `max_retries` | Maximum retries per chunk on network errors / 5xx |
| `remove_local_after_upload` | `true` = remove the local source after a successful upload; `false` = keep it (default). Golden files are never removed |
| `remove_local_permanent` | Removal method for post-upload cleanup: `false` = move to Trash (recoverable, default) / `true` = delete permanently (irreversible) |

Your local config files live in `config/` and are listed in `.gitignore`, so they **will not be committed**; you can safely use your own paths even after forking this repo. They are **fully independent**: `config/upload_config.json` never reads `config/convert_config.json`, and the cut list is read only when uploading. Missing files fall back to the built-in defaults.

The cut list and its encoder settings live in `config/cuts_config.json`; see section 2.1.

**Combining files.** `combine` decides whether files are merged before conversion. `true` (default) merges VOB title sets and groups same-folder files of **any** extension listed in `convert_extensions` (for example `.avi`, `.mkv`, or `.m2ts` segments), in natural order. `false` makes every source file its own conversion job and upload. A combined group is named `<folder> [combined]` and produces one golden MP4 and one upload; with `false`, each source file keeps its own filename as the title and gets its own golden MP4 and upload. Formats outside `convert_extensions` are uploaded as-is and are not combined. `combined_name_source` controls the combined title: `folder` (default) uses the immediate folder name, `root` uses the configured `root_folder` name, and `prefix` uses the common prefix of all video filenames in that folder (for example `Event-100-00.AVI` + `Event-100-01.AVI` → `Event-100 [combined]`).

**Converted sources and golden files.** A converted file always becomes a *golden source*: later runs cut and upload from it instead of re-reading the originals. If `output_folder` is set, goldens are written directly into that folder; set `upload_config.root_folder` to that folder so `batch_upload.py` finds them. Two jobs that produce the same title stop the run instead of overwriting. `remove_empty_folders` can clean up source folders left empty after original files are removed. Uploads with cuts are re-encoded; uploads without cuts use the file as-is. `remove_original_after_conversion` is the only setting that removes the original files at conversion time — `remove_local_after_upload` never removes a golden file. A golden job uses its recorded title as the `config/cuts_config.json` key, so cuts keep working after conversion. Two sources that would write the same golden file stop the run instead of overwriting.

### 3.2 Run

Upload existing golden/direct files (no conversion):

```bash
cd /path/to/youtube_batch_uploader
python3 batch_upload.py
```

This run will:

1. Scan `root_folder` for the extensions in `video_extensions` (default `[".mp4"]`, the golden output) and report how many upload items were found
2. Open the browser for OAuth authorization (first run only)
3. Find or create the playlist
4. Cut the files with ffmpeg if needed, then upload them
5. Add each uploaded video to the playlist

To convert first, run `python3 convert_only.py` (section 3.3). To do both with one command:

```bash
python3 convert_and_upload.py
```

That runs conversion with `config/convert_config.json`, then upload with `config/upload_config.json`.

### 3.3 Convert only (no upload)

To prepare golden MP4s without uploading anything, run:

```bash
python3 convert_only.py
```

This path never authenticates with YouTube and never uploads. It reads `config/convert_config.json` only; `config/upload_config.json` is not needed. It records converted sources in `state/convert_state.json` (separate from `state/upload_state.json`). It converts every job whose extension is in `convert_extensions`, respects `remove_original_after_conversion` / `remove_original_permanent`, skips files that already have a golden source (removing their originals when `remove_original_after_conversion` is set), and prints a converted/skipped/failed summary. Cuts are ignored here; the golden source is always the uncut full video.

### 3.4 "Draft" and publishing

The YouTube API **has no concept of a "draft"**: once an upload completes, the video immediately exists in the state specified by `privacy_status`:

| `privacy_status` | Effect |
|---|---|
| `private` | Visible only to the channel owner; closest to a "draft". You can publish it manually later in YouTube Studio |
| `unlisted` | Not discoverable in search, but anyone with the link can watch it |
| `public` | Published immediately after upload |

### 3.5 Automatic local cleanup after upload

Set the following in `config/upload_config.json`:

```json
"remove_local_after_upload": true,
"remove_local_permanent": false
```

| `remove_local_permanent` | Effect |
|---|---|
| `false` (default) | Move the video to the system Trash (Finder on macOS); it can be restored from the Trash |
| `true` | Delete the file permanently; **cannot be undone** |

Notes:

- A local file is removed only after the video **uploads successfully and returns a video ID**
- If an upload fails, the local file is not deleted
- This cleanup applies only to non-golden upload sources; converted golden MP4s are always kept. Use `remove_original_after_conversion` to remove the original VOB/AVI files.
- A removal failure (e.g. insufficient permissions) only prints a warning and does not interrupt other videos
- On macOS, the first use of trash mode (`remove_local_permanent: false`) may prompt "Terminal wants to control Finder"; allow it
- If `send2trash` is installed, it is used first, avoiding the Finder permission prompt:

  ```bash
  pip3 install send2trash
  ```

---

## 4. Resumable Uploads and Progress Tracking

### 4.1 `upload_state.json` and `convert_state.json`

Both files live in `state/`. `upload_state.json` records each uploaded video; `convert_state.json` records the golden source index. They are independent:

```json
{
  "/path/to/videos/Folder [combined].mp4": {
    "size": 4596858880,
    "mtime": 1700000000.0,
    "cuts": [[10, 20]],
    "local_removed": false,
    "video_id": "xxxxxxxxxxx",
    "playlist_added": true,
    "bytes_sent": 566007357,
    "resumable_uri": null,
    "upload_size": 566007357
  }
}
```

```json
{
  "/path/to/videos/Folder [combined].mp4": {
    "title": "Folder [combined]",
    "source_files": ["/path/to/videos/Folder/VTS_01_1.VOB", "..."]
  }
}
```

- `upload_state.json` is a flat map: source path → one upload entry
- Updated after every uploaded chunk
- A completed video is skipped on the next run
- An unfinished upload keeps its `resumable_uri` and `bytes_sent`, and resumes automatically
- If the file size or modification time changes, it is treated as a new version and re-uploaded

### 4.2 What if the upload stops midway?

Just run the script again:

```bash
python3 batch_upload.py
```

- Completed videos: skipped
- Unfinished videos: resumed from the breakpoint
- Playlist: already-uploaded videos that were not added previously get added now

> Google resumable sessions are typically valid for about 7 days; after expiry, that video is automatically re-uploaded from the start.

### 4.3 Force a re-upload

- Re-upload **a single file**: delete that file's entry from `state/upload_state.json`
- Re-upload **everything**: delete the entire `state/upload_state.json`

(Close any running script before deleting.)

---

## 5. FAQ

### `python: command not found`

Recent macOS versions ship only `python3`. Use `python3 batch_upload.py`.

### `HttpError 403: quotaExceeded`

The YouTube Data API v3 has a default daily quota of **10,000 units**, and **each video upload costs 1,600 units**, so you can upload roughly **6 videos per day** (other operations also consume small amounts of quota).

Workarounds:

- Spread the uploads over several days; the script records progress and continues automatically the next day
- Request a quota increase in Google Cloud Console

### `HttpError 403` but not quota

- Confirm that `client_secrets.json` belongs to a project with the YouTube Data API v3 enabled
- Confirm that the signed-in account has a YouTube channel
- Confirm that the account is listed under Test users on the OAuth consent screen

### `找不到 ffmpeg` when converting VOB

Install ffmpeg (see section 1.2) and confirm it is on `PATH`:

```bash
ffmpeg -version
```

Without ffmpeg, jobs that need conversion fail; formats outside `convert_extensions` still upload normally.

### AVI upload fails or plays incorrectly

AVI is officially supported by YouTube, but some codecs found inside AVI files are not. Add `".avi"` to `convert_extensions` in `config/convert_config.json`; `convert_only.py` (or `convert_and_upload.py`) will transcode AVI to MP4 (H.264/AAC) before it is uploaded.

### VOB conversion is slow or uses a lot of disk space

Merging and re-encoding DVD video is CPU-intensive. The converted MP4 is kept as a golden source next to the source; cut re-encodes are cached in `.converted/` until the upload succeeds. Keep free disk space roughly equal to the total source size.

### Cut re-encoding is slow

Set the hardware encoder in `config/cuts_config.json`:

```json
"cut_encoder": "h264_videotoolbox",
"cut_quality": 70
```

On macOS this is typically about 3× faster than `libx264` CRF 20 on the same content, with a similar file size.

### Upload is rejected because the video is too long

Unverified YouTube accounts can only upload videos up to 15 minutes. Verify your account at <https://www.youtube.com/verify> to upload longer videos (verified accounts can upload up to 12 hours / 256 GB per video). The tool uploads each video as a single file.

### Connection drops / computer sleeps during upload

The script retries automatically; if it still fails, run it again and it resumes from the breakpoint.

### `token.pickle` expired or you want to switch accounts

Delete `token.pickle`, then run the script again to sign in.

> Note: if the OAuth consent screen of your Google Cloud project is still in **Testing** status, refresh tokens expire after about **7 days** and you will need to sign in again. Publish the app to avoid this.

### `FutureWarning` / `NotOpenSSLWarning` appears

Warnings such as Python 3.9 being past end-of-life or urllib3 using LibreSSL do not affect uploads and can be ignored.

### Uploads are slow

- Make sure `chunk_size_mb` is not too small (16–32 recommended)
- Upload speed mainly depends on network bandwidth; a wired connection is usually more stable than Wi-Fi
- Large files (e.g. 9 GB) naturally take longer; if interrupted, they resume instead of starting over

---

## 6. File Structure

```text
youtube_batch_uploader/
├── batch_upload.py       # Upload entry point (python3 batch_upload.py)
├── convert_only.py       # Convert entry point (python3 convert_only.py)
├── convert_and_upload.py # Convert then upload in one command
├── youtube_uploader/     # Implementation package
│   ├── cli.py            # Upload flow and job orchestration
│   ├── convert.py        # Conversion-only flow
│   ├── golden.py         # Golden source detection and coexistence
│   ├── pipeline.py       # Source preparation and media selection
│   ├── config.py         # Config loading and file paths
│   ├── cuts.py           # Cut-list parsing, batch mapping and description
│   ├── state.py          # upload/convert state tracking
│   ├── files.py          # Scanning, grouping and local cleanup
│   ├── ffmpeg.py         # Probing, conversion and cache
│   └── youtube_api.py    # OAuth, playlists and resumable uploads
├── tests/                # Unit tests (python3 -m unittest)
├── config/               # Settings (examples committed; local files not)
│   ├── convert_config.example.json # Example convert settings
│   ├── upload_config.example.json  # Example upload settings
│   ├── cuts_config.example.json    # Cut-list example
│   ├── convert_config.json         # Your local convert settings
│   ├── upload_config.json          # Your local upload settings
│   └── cuts_config.json            # Your local cut-list
├── client_secrets.json   # OAuth credential (download yourself; do not share)
├── token.pickle          # Login token (created on first run; do not share)
├── .converted/           # Temporary MP4 conversions (safe to delete)
├── state/                # Generated runtime files (not committed)
│   ├── upload_state.json
│   └── convert_state.json
├── README.md             # English documentation
└── README.zh-TW.md       # Traditional Chinese documentation
```

Run the unit tests with:

```bash
python3 -m unittest
```

---

## 7. Security Notice

The following files contain sensitive data. **Never** upload them to GitHub, cloud drives, or send them to anyone:

- `client_secrets.json` (OAuth client secret)
- `token.pickle` (equivalent to your login authorization)
- `state/upload_state.json` (contains local paths and video IDs)
- `state/convert_state.json` (contains local paths)

If your credentials leak, immediately delete the OAuth client ID in Google Cloud Console and create a new one.
