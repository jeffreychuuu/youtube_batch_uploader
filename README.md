# YouTube Batch Uploader

[English](README.md) | [繁體中文](README.zh-TW.md)

Upload every video in a local folder to YouTube in bulk using Python, the official YouTube Data API v3, and OAuth 2.0, with automatic addition to a specified playlist.

Supports **resumable uploads**: if the connection drops, the machine shuts down, or you press Ctrl+C mid-upload, the next run resumes from the breakpoint and never re-uploads completed videos.

---

## Features

- Recursively scans a folder and uploads every supported video
- VOB (DVD) support: multi-segment `VTS_01_1.VOB`, `VTS_01_2.VOB`, ... files are merged and transcoded to MP4 with ffmpeg; the merged video is titled after its folder
- Long videos are split into parts (default every 1 hour) and titled `<name> [part 1]`, `<name> [part 2]`, ...
- Cut-list support: remove time ranges flagged by YouTube before upload (`cuts.json`)
- Golden source support: keep the converted MP4 and reuse it for later cuts and uploads; `convert_only.py` prepares golden files without YouTube authorization
- Optional ffmpeg transcoding for other formats (e.g. AVI) via `convert_extensions`
- Adds uploaded videos to a playlist (creating it if needed); reruns never add duplicates
- Progress is stored in `upload_state.json`; completed files are skipped on rerun
- Unfinished uploads keep their resumable session and continue from the breakpoint
- Automatic exponential backoff retry on network errors / 429 / 5xx
- Configurable chunk size (default 32 MB, must be a multiple of 256 KB)
- Progress file written atomically, so a forced termination cannot corrupt it
- Optionally removes local videos after a successful upload (move to Trash or delete permanently)

---

## Requirements

- macOS (Windows / Linux also work; adjust paths as needed)
- Python 3.8 or later
- ffmpeg (only required when uploading VOB, or other formats added to `convert_extensions`)
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

### 1.2 Install ffmpeg (for VOB)

VOB files cannot be uploaded to YouTube directly. The script merges and converts them to MP4 with ffmpeg, so ffmpeg must be on your `PATH`:

```bash
brew install ffmpeg          # macOS
# sudo apt install ffmpeg    # Debian / Ubuntu
```

Windows: download a build from [ffmpeg.org](https://ffmpeg.org/) and add it to `PATH`.

> This is only needed for extensions listed in `convert_extensions` (default: `.vob`). Other formats, including AVI, are uploaded as-is.

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
5. Rename the downloaded JSON to **`client_secrets.json`** and place it in the same folder as `batch_upload.py`.

> A correct Desktop App credential contains an `"installed"` block in `client_secrets.json`.

### 1.4 First-time sign-in and authorization

The first run opens a browser asking you to sign in to your Google account and authorize access.

- Choose the **YouTube channel** you want to upload to
- After authorization, `token.pickle` is created in the same folder; later runs will not ask you to sign in again

> `token.pickle` is equivalent to your login credential. **Never share it or upload it anywhere.**

---

## 2. Video List

This version **does not use a CSV file**: the script recursively scans every video file under `CONFIG["root_folder"]` and uses the **file name (without extension) as the video title**. The only exception is a merged multi-segment VOB, which is titled after its folder (see below).

Supported extensions (editable in `CONFIG["video_extensions"]`):

```
.mp4  .mov  .mkv  .avi  .m4v  .vob
```

**AVI** is officially supported by YouTube and is uploaded as-is by default. If a particular AVI file uses a codec YouTube cannot process, add `".avi"` to `convert_extensions` in `config.json` to transcode it to MP4 with ffmpeg first.

**VOB** is not officially supported by YouTube, so `.vob` is in `convert_extensions` by default. The script then:

- Groups files such as `VTS_01_1.VOB`, `VTS_01_2.VOB`, ... by title set and merges them in numeric order into one video
- Titles the merged video after its folder (using the parent folder name when the files are inside `VIDEO_TS`)
- Skips DVD menu files (`*_0.VOB` and `VIDEO_TS.VOB`)
- Uses the original file name as the title for a standalone VOB
- Splits long results into parts according to `part_duration_hours` (default 1 hour); each part is titled `<name> [part N]`
- Splitting applies to videos processed by ffmpeg (VOB, or other formats added to `convert_extensions`)
- Caches the converted MP4 parts in `.converted/` so an interrupted upload resumes without re-encoding; the cache is removed after all parts upload successfully

In other words, you only need:

```text
/path/to/your/videos/
├── Video A.mp4
├── Video B.mp4
└── subfolder/
    └── Video C.mov
```

All three videos will be uploaded with the titles "Video A", "Video B", and "Video C".

If you want to specify each video's title, description, tags, or privacy with a CSV file, you need to extend the script (currently unsupported).

### 2.1 Cut-list: remove flagged segments

If YouTube flags a video and gives time ranges to remove, create `cuts.json` next to the script:

```json
{
  "folders": {
    "My Video": {
      "original": [
        "21:55-22:45",
        "23:20-24:35",
        "24:45-26:25"
      ]
    }
  }
}
```

- `folders` keys match merged VOB jobs (the folder name; add `(VTS_01)` / `(VTS_02)` when one folder contains multiple title sets). `files` keys match single-file jobs (the file name without extension).
- Each entry must contain **exactly one** of `original` or `parts`. An entry with both, or with neither, is rejected:
  - `original`: ranges on the **full merged video timeline** (the same timeline YouTube shows), removed before splitting.
  - `parts`: ranges relative to a specific part, e.g. `"parts": {"2": ["5:00-6:00"]}`. The script adds that part's start offset using `part_duration_hours`, so you do not need to calculate the full-timeline time. Requires splitting (`part_duration_hours > 0`).
- Time formats: `MM:SS` or `HH:MM:SS`. Separate start/end with `-`, `–`, `—`, `~`, or `to`.
- The legacy flat format is still accepted: a key that is the job title, the resolved first source path, or `"<title> [part N]"`, with an array of ranges as the value.
- JSON does not support comments; `cuts.example.json` shows the two valid entry shapes without any comment syntax.
- The ranges are removed during the ffmpeg conversion; the remaining video is then split normally.
- Changing the cut list invalidates the conversion cache. If the job was already uploaded and the source files still exist, the script re-processes it and uploads a corrected version. The old YouTube video is **not** deleted automatically.
- Local source removal follows `remove_local_after_upload` as usual, even when a cut list is set.
- `cuts.json` is gitignored; see `cuts.example.json` for the format.

---

## 3. Configuration and Usage

### 3.1 Edit the configuration

Settings are split into two files. Copy both examples, then edit them:

```bash
cp convert_config.example.json convert_config.json
cp upload_config.example.json upload_config.json
```

**`convert_config.json`** — what to scan and how to build golden MP4s:

```json
{
  "root_folder": "/path/to/your/videos",
  "convert_extensions": [".vob"],
  "combine": true,
  "export_converted_source": false,
  "remove_original_after_conversion": false,
  "remove_original_permanent": false
}
```

| Setting | Description |
|---|---|
| `root_folder` | Source folder for videos; all subfolders are scanned recursively |
| `convert_extensions` | Extensions transcoded to MP4 with ffmpeg (default `[".vob"]`); add `".avi"` to force AVI transcoding. The convert script scans exactly these extensions |
| `combine` | `true` (default) merges VOB title sets and same-folder files of the same convert extension, in natural order; `false` makes every file its own job |
| `export_converted_source` | `true` = keep the converted MP4 next to the source (or its `VIDEO_TS` parent) and reuse it as the source of record |
| `remove_original_after_conversion` | `true` = remove the original files after the converted MP4 is verified (requires `export_converted_source`) |
| `remove_original_permanent` | Removal method for conversion-time cleanup: `false` = move to Trash (default) / `true` = delete permanently |

**`upload_config.json`** — how to scan, split and publish (fully independent):

```json
{
  "root_folder": "/path/to/your/videos",
  "video_extensions": [".mp4"],
  "playlist_name": "My Playlist",
  "privacy_status": "unlisted",
  "made_for_kids": false,
  "part_duration_hours": 1,
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
| `part_duration_hours` | Length of each part in hours (default `1`; `0` = never split). Applies to **every** upload, including MP4/MOV/AVI. Set `0.25` for unverified accounts (15-minute limit) |
| `chunk_size_mb` | Size of each upload chunk; must be a multiple of 256 KB. 16–32 recommended for large files |
| `max_retries` | Maximum retries per chunk on network errors / 5xx |
| `remove_local_after_upload` | `true` = remove the local source after a successful upload; `false` = keep it (default). Golden files are never removed |
| `remove_local_permanent` | Removal method for post-upload cleanup: `false` = move to Trash (recoverable, default) / `true` = delete permanently (irreversible) |

Both files are listed in `.gitignore` and **will not be committed**; you can safely use your own paths even after forking this repo. They are **fully independent**: `upload_config.json` never reads `convert_config.json`. Missing files fall back to the built-in defaults.

**Combining files.** `combine` decides whether files are merged before conversion. `true` (default) merges VOB title sets and same-folder files of the same convert extension (for example `.m2ts` segments), in natural order. `false` makes every file its own upload. A combined group is named `<folder> [combined]` and produces one golden MP4 and one upload (split by `part_duration_hours`); with `false`, each source file keeps its own filename as the title and gets its own golden MP4 and upload.

**Converted sources and golden files.** When `export_converted_source` is enabled, a converted file becomes a *golden source*: later runs cut, split and upload from it instead of re-reading the originals. Cut-free uploads are split with stream copy (fast, no re-encode); uploads with cuts are re-encoded. `remove_original_after_conversion` is the only setting that removes the original files at conversion time — `remove_local_after_upload` never removes a golden file. A golden job keeps the cuts namespace of its source (`folders` for merged VOB jobs, `files` for single-file jobs), so existing `cuts.json` keys keep working after conversion. Two sources that would write the same golden file stop the run instead of overwriting.

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
4. Cut and split the files with ffmpeg, then upload each part one by one
5. Add each uploaded video to the playlist

To convert first, run `python3 convert_only.py` (section 3.3). To do both with one command:

```bash
python3 convert_and_upload.py
```

That runs conversion with `convert_config.json`, then upload with `upload_config.json`.

### 3.3 Convert only (no upload)

To prepare golden MP4s without uploading anything, run:

```bash
python3 convert_only.py
```

This path never authenticates with YouTube and never uploads. It reads `convert_config.json` only; `upload_config.json` is not needed. It requires `export_converted_source: true`. It converts every job whose extension is in `convert_extensions`, respects `remove_original_after_conversion` / `remove_original_permanent`, skips files that already have a golden source, and prints a converted/skipped/failed summary. Cuts and `part_duration_hours` are ignored here; the golden source is always the uncut full video.

### 3.4 "Draft" and publishing

The YouTube API **has no concept of a "draft"**: once an upload completes, the video immediately exists in the state specified by `privacy_status`:

| `privacy_status` | Effect |
|---|---|
| `private` | Visible only to the channel owner; closest to a "draft". You can publish it manually later in YouTube Studio |
| `unlisted` | Not discoverable in search, but anyone with the link can watch it |
| `public` | Published immediately after upload |

### 3.5 Automatic local cleanup after upload

Set the following in `upload_config.json`:

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
- For a merged or split VOB upload, all source segments are removed only after every part has uploaded successfully
- Videos uploaded previously are also cleaned up on rerun according to this setting
- A removal failure (e.g. insufficient permissions) only prints a warning and does not interrupt other videos
- On macOS, the first use of trash mode (`remove_local_permanent: false`) may prompt "Terminal wants to control Finder"; allow it
- If `send2trash` is installed, it is used first, avoiding the Finder permission prompt:

  ```bash
  pip3 install send2trash
  ```

---

## 4. Resumable Uploads and Progress Tracking

### 4.1 `upload_state.json`

The script creates `upload_state.json` in the same folder, recording each video:

```json
{
  "uploads": {
    "/path/to/your/videos/My Video/VTS_01_1.VOB": {
      "size": 4596858880,
      "mtime": 1700000000.0,
      "part_duration_hours": 1,
      "local_removed": true,
      "parts": [
        {
          "upload_size": 566007357,
          "bytes_sent": 566007357,
          "video_id": "xxxxxxxxxxx",
          "playlist_added": true
        }
      ]
    }
  }
}
```

- `parts` contains one entry per uploaded part; a single-video job has one entry
- Updated after every uploaded chunk
- Parts that are already fully uploaded are skipped on the next run
- Unfinished parts keep their `resumable_uri` and `bytes_sent`, and resume automatically on the next run
- If the file size or modification time changes, it is treated as a new version and re-uploaded
- Changing `part_duration_hours` applies to new or unfinished jobs; completed jobs are not re-uploaded automatically
- State files written by older versions (with a top-level `video_id`) are migrated automatically

### 4.2 What if the upload stops midway?

Just run the script again:

```bash
python batch_upload.py
```

- Completed videos: skipped
- Unfinished videos: resumed from the breakpoint
- Playlist: already-uploaded videos that were not added previously get added now

> Google resumable sessions are typically valid for about 7 days; after expiry, that video is automatically re-uploaded from the start.

### 4.3 Force a re-upload

- Re-upload **a single file**: delete that file's entry from `upload_state.json`
- Re-upload **everything**: delete the entire `upload_state.json`

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

### `找不到 ffmpeg` when uploading VOB

Install ffmpeg (see section 1.2) and confirm it is on `PATH`:

```bash
ffmpeg -version
```

Without ffmpeg, VOB jobs fail with an error but other formats still upload normally.

### AVI upload fails or plays incorrectly

AVI is officially supported by YouTube, but some codecs found inside AVI files are not. Add `".avi"` to `convert_extensions` in `convert_config.json`; the script will transcode AVI to MP4 (H.264/AAC) before uploading.

### VOB conversion is slow or uses a lot of disk space

Merging and re-encoding DVD video is CPU-intensive. The result is cached in `.converted/` until the upload succeeds, so keep free disk space roughly equal to the total source size.

### Upload is rejected because the video is too long

Unverified YouTube accounts can only upload videos up to 15 minutes. Either verify your account at <https://www.youtube.com/verify>, or set `"part_duration_hours": 0.25` in `upload_config.json` to split every 15 minutes. Verified accounts can upload up to 12 hours / 256 GB per video.

### Videos were split into more parts than expected

`part_duration_hours` controls the part length (default `1`; set `0` to never split). The setting applies to new or unfinished jobs; completed uploads are not re-uploaded automatically. To re-process a completed job, delete its entry from `upload_state.json`.

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
│   ├── pipeline.py       # Source preparation and part selection
│   ├── config.py         # convert/upload config loading and file paths
│   ├── cuts.py           # cuts.json parsing and part offsets
│   ├── state.py          # upload_state.json tracking
│   ├── files.py          # Scanning, grouping and local cleanup
│   ├── ffmpeg.py         # Probing, conversion, splitting and cache
│   └── youtube_api.py    # OAuth, playlists and resumable uploads
├── tests/                # Unit tests (python3 -m unittest)
├── convert_config.example.json # Example convert settings (committed)
├── upload_config.example.json  # Example upload settings (committed)
├── convert_config.json   # Your local convert settings (not committed)
├── upload_config.json    # Your local upload settings (not committed)
├── client_secrets.json   # OAuth credentials (download yourself, never share)
├── token.pickle          # Login token (created on first run, never share)
├── upload_state.json     # Upload progress record (created automatically)
├── cuts.json             # Local cut-list for flagged videos (not committed)
├── cuts.example.json     # Cut-list example
├── .converted/           # Temporary MP4 conversions (created automatically, safe to delete)
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
- `upload_state.json` (contains local paths and video IDs)

If your credentials leak, immediately delete the OAuth client ID in Google Cloud Console and create a new one.
