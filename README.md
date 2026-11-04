# YouTube Batch Uploader

[English](README.md) | [繁體中文](README.zh-TW.md)

Upload every video in a local folder to YouTube in bulk using Python, the official YouTube Data API v3, and OAuth 2.0, with automatic addition to a specified playlist.

Supports **resumable uploads**: if the connection drops, the machine shuts down, or you press Ctrl+C mid-upload, the next run resumes from the breakpoint and never re-uploads completed videos.

---

## Features

- Recursively scans a folder and uploads every supported video
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
- A YouTube / Google account with a channel
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

### 1.2 Create YouTube API credentials

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

### 1.3 First-time sign-in and authorization

The first run opens a browser asking you to sign in to your Google account and authorize access.

- Choose the **YouTube channel** you want to upload to
- After authorization, `token.pickle` is created in the same folder; later runs will not ask you to sign in again

> `token.pickle` is equivalent to your login credential. **Never share it or upload it anywhere.**

---

## 2. Video List

This version **does not use a CSV file**: the script recursively scans every video file under `CONFIG["root_folder"]` and uses the **file name (without extension) as the video title**.

Supported extensions (editable in `CONFIG["video_extensions"]`):

```
.mp4  .mov  .mkv  .avi  .m4v
```

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

---

## 3. Configuration and Usage

### 3.1 Edit the configuration

Copy `config.example.json` to `config.json`, then edit the settings:

```bash
cp config.example.json config.json
```

```json
{
  "root_folder": "/path/to/your/videos",
  "playlist_name": "My Playlist",
  "privacy_status": "unlisted",
  "made_for_kids": false,
  "video_extensions": [".mp4", ".mov", ".mkv", ".avi", ".m4v"],
  "chunk_size_mb": 32,
  "max_retries": 5,
  "remove_local_after_upload": false,
  "remove_local_permanent": false
}
```

`config.json` is listed in `.gitignore` and **will not be committed**; you can safely use your own paths even after forking this repo. If `config.json` is missing, the script uses the built-in defaults.

| Setting | Description |
|---|---|
| `root_folder` | Source folder for videos; all subfolders are scanned recursively |
| `playlist_name` | Target playlist name; created automatically if it does not exist. Set to `""` to skip adding to a playlist |
| `privacy_status` | `public` / `unlisted` (visible via link) / `private` |
| `made_for_kids` | `True` = content made for kids; `False` = not made for kids |
| `video_extensions` | Extensions to scan |
| `chunk_size_mb` | Size of each upload chunk; must be a multiple of 256 KB. 16–32 recommended for large files |
| `max_retries` | Maximum retries per chunk on network errors / 5xx |
| `remove_local_after_upload` | `true` = remove the local video after a successful upload; `false` = keep it (default) |
| `remove_local_permanent` | Removal method: `false` = move to Trash (recoverable, default) / `true` = delete permanently (irreversible) |

### 3.2 Run

```bash
cd /path/to/youtube_batch_uploader
python batch_upload.py
```

If the system has no `python` command:

```bash
python3 batch_upload.py
```

The run will:

1. Scan the folder and report how many videos were found
2. Open the browser for OAuth authorization (first run only)
3. Find or create the playlist
4. Upload each video one by one, showing a progress percentage
5. Add each uploaded video to the playlist

### 3.3 "Draft" and publishing

The YouTube API **has no concept of a "draft"**: once an upload completes, the video immediately exists in the state specified by `privacy_status`:

| `privacy_status` | Effect |
|---|---|
| `private` | Visible only to the channel owner; closest to a "draft". You can publish it manually later in YouTube Studio |
| `unlisted` | Not discoverable in search, but anyone with the link can watch it |
| `public` | Published immediately after upload |

### 3.4 Automatic local cleanup after upload

Set the following in `config.json`:

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
    "/path/to/your/videos/video1.mp4": {
      "size": 104857600,
      "mtime": 1700000000.0,
      "video_id": "xxxxxxxxxxx",
      "playlist_added": true,
      "bytes_sent": 104857600
    }
  }
}
```

- Updated after every uploaded chunk
- Files that are already fully uploaded are skipped on the next run
- Unfinished uploads keep their `resumable_uri` and `bytes_sent`, and resume automatically on the next run
- If the file size or modification time changes, it is treated as a new version and re-uploaded

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
├── batch_upload.py       # Main script
├── config.example.json   # Example configuration (committed)
├── config.json           # Your local configuration (not committed)
├── client_secrets.json   # OAuth credentials (download yourself, never share)
├── token.pickle          # Login token (created on first run, never share)
├── upload_state.json     # Upload progress record (created automatically)
├── README.md             # English documentation
└── README.zh-TW.md       # Traditional Chinese documentation
```

---

## 7. Security Notice

The following files contain sensitive data. **Never** upload them to GitHub, cloud drives, or send them to anyone:

- `client_secrets.json` (OAuth client secret)
- `token.pickle` (equivalent to your login authorization)
- `upload_state.json` (contains local paths and video IDs)

If your credentials leak, immediately delete the OAuth client ID in Google Cloud Console and create a new one.
