# YouTube 批量上傳工具

用 Python + 官方 YouTube Data API v3 + OAuth 2.0，將本機一整個資料夾嘅影片批量上傳到 YouTube，並自動加入指定播放清單。

支援**斷點續傳**：上傳中途斷線、關機或撳 Ctrl+C，下次執行會自動由斷點繼續，已完成嘅影片唔會重複上傳。

---

## 功能特色

- 遞迴掃描指定資料夾，自動上傳所有支援格式嘅影片
- 上傳完成自動加入（或建立）指定播放清單，重跑唔會重複加入
- 進度記錄於 `upload_state.json`，已完成檔案重跑時直接跳過
- 未完成嘅上傳會保存 resumable session，重跑由斷點續傳
- 網絡斷線 / 429 / 5xx 自動 exponential backoff 重試
- 分塊上傳大小可調（預設 32MB，必須為 256KB 整數倍）
- 原子寫入進度檔，強制終止都唔會寫壞

---

## 系統需求

- macOS（Windows / Linux 亦適用，只需改路徑）
- Python 3.8 或以上
- 一個已建立頻道嘅 YouTube / Google 帳號
- 已啟用 YouTube Data API v3 嘅 Google Cloud 專案

---

## 1. 前置準備

### 1.1 安裝套件

喺 Terminal 執行：

```bash
pip3 install google-api-python-client google-auth-oauthlib google-auth-httplib2
```

> 如果 `pip3` 唔存在，先確認有冇裝 Python 3：`python3 --version`。
> 本機如果 `python` 指令唔存在，用 `python3` 代替（或自行建立 alias / wrapper）。

### 1.2 建立 YouTube API 憑證

1. 前往 [Google Cloud Console](https://console.cloud.google.com/)，建立新專案（或選用現有專案）。
2. 進入 **APIs & Services → Library**，搜尋 **YouTube Data API v3**，按 **Enable** 啟用。
3. 進入 **APIs & Services → OAuth consent screen**：
   - User type 選擇 **External**
   - 填寫 App name、User support email、Developer contact 等資料
   - 在 **Test users** 加入你將會用嚟上傳嘅 Google / YouTube 帳號
4. 進入 **APIs & Services → Credentials → Create Credentials → OAuth client ID**：
   - Application type 選擇 **Desktop app**
   - 建立後按 **Download JSON** 下載憑證
5. 將下載嘅 JSON 改名為 **`client_secrets.json`**，放喺同 `batch_upload.py` 同一個資料夾。

> `client_secrets.json` 入面有 `"installed"` 區塊就代表係正確嘅 Desktop App 憑證。

### 1.3 第一次登入授權

第一次執行 Script 時，會自動開啟瀏覽器要求登入 Google 帳號並授權。

- 揀啱你要上傳嘅 **YouTube channel**
- 授權成功後會喺同一個資料夾產生 `token.pickle`，之後再執行就唔使再登入

> `token.pickle` 等同登入憑證，**唔好分享或上載到任何地方**。

---

## 2. 影片清單

本專案版本**唔使用 CSV**：Script 會直接遞迴掃描 `CONFIG["root_folder"]` 入面所有影片檔案，以**檔案名稱（不含副檔名）作為影片標題**。

支援嘅副檔名（可喺 `CONFIG["video_extensions"]` 修改）：

```
.mp4  .mov  .mkv  .avi  .m4v
```

即係話，你只需要：

```text
/path/to/your/videos/
├── 影片 A.mp4
├── 影片 B.mp4
└── 子資料夾/
    └── 影片 C.mov
```

三條片都會被上傳，標題分別係「影片 A」、「影片 B」、「影片 C」。

如果你想用 CSV 指定每條片嘅標題、描述、標籤、權限等，需要另外擴充 Script（目前未支援）。

---

## 3. 設定與執行

### 3.1 修改設定

打開 `batch_upload.py`，修改最上面嘅 `CONFIG`：

```python
CONFIG = {
    "root_folder": "/path/to/your/videos",
    "playlist_name": "My Playlist",
    "privacy_status": "unlisted",
    "made_for_kids": False,
    "video_extensions": [".mp4", ".mov", ".mkv", ".avi", ".m4v"],
    "chunk_size_mb": 32,
    "max_retries": 5,
}
```

| 設定 | 說明 |
|---|---|
| `root_folder` | 影片來源資料夾，會遞迴掃描所有子資料夾 |
| `playlist_name` | 目標播放清單名稱；唔存在會自動建立。設為 `""` 就唔加入清單 |
| `privacy_status` | `public`（公開）/ `unlisted`（非公開，有 link 就睇到）/ `private`（私人） |
| `made_for_kids` | `True` = 兒童導向內容；`False` = 非兒童導向 |
| `video_extensions` | 要掃描嘅副檔名 |
| `chunk_size_mb` | 每個上傳分塊大小，必須為 256KB 整數倍；大檔案建議 16–32 |
| `max_retries` | 網絡錯誤 / 5xx 時每個分塊最多重試次數 |

### 3.2 執行

```bash
cd /path/to/youtube_video_upload
python batch_upload.py
```

如果系統冇 `python` 指令：

```bash
python3 batch_upload.py
```

執行後會：

1. 掃描資料夾，列出影片數量
2. 開啟瀏覽器做 OAuth 授權（第一次先需要）
3. 搵到或建立播放清單
4. 逐條影片上傳，顯示進度百分比
5. 上傳完成後加入播放清單

### 3.3 關於「草稿」與發佈

YouTube API **冇「草稿」概念**，上傳完成後影片即刻以 `privacy_status` 指定嘅狀態存在：

| `privacy_status` | 效果 |
|---|---|
| `private` | 只有頻道擁有者睇到，最接近「草稿」，之後可去 YouTube Studio 手動改公開 |
| `unlisted` | 唔會公開搜尋到，但有 link 嘅人睇到 |
| `public` | 上傳完成即公開 |

---

## 4. 斷點續傳與進度記錄

### 4.1 `upload_state.json`

Script 會喺同一個資料夾產生 `upload_state.json`，記錄每條影片：

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

- 每上傳完一個分塊就會更新一次
- 已上傳完成嘅檔案，下次執行會顯示 `[略過]` 並直接跳過
- 未完成嘅上傳會保存 `resumable_uri` 同 `bytes_sent`，下次執行自動續傳
- 如果檔案大小或修改時間變咗，會當作新版本重新上傳

### 4.2 中途斷咗點算？

直接再執行一次：

```bash
python batch_upload.py
```

- 已完成嘅影片：跳過
- 未完成嘅影片：由斷點繼續
- 播放清單：如果之前未加入成功，會補加入

> Google 嘅 resumable session 一般有效約 7 日；過期後會自動改為重新上傳該影片。

### 4.3 強制重新上傳

- 重新上傳**某一條**：喺 `upload_state.json` 刪除該檔案嘅 entry
- **全部**重新上傳：刪除成個 `upload_state.json`

（刪除前記得關閉正在執行嘅 Script。）

---

## 5. 常見問題

### `python: command not found`

macOS 新版本預設只有 `python3`，用 `python3 batch_upload.py` 即可。

### `HttpError 403: quotaExceeded`

YouTube Data API v3 預設每日配額為 **10,000 units**，而**每次上傳影片消耗 1,600 units**，即每日大約只可以上傳 **6 條影片**（其他操作亦會消耗少量配額）。

應對方法：

- 分幾日執行；Script 會記錄進度，第二日再跑會自動繼續
- 喺 Google Cloud Console 申請提高配額（Quota increase）

### `HttpError 403` 但唔係 quota

- 確認 `client_secrets.json` 屬於已啟用 YouTube Data API v3 嘅專案
- 確認登入嘅帳號已建立 YouTube channel
- 確認該帳號已加入 OAuth consent screen 嘅 Test users

### 上傳中途斷線 / 電腦休眠

Script 會自動重試；如果仍然失敗，再執行一次就會由斷點續傳。

### `token.pickle` 過期或想換帳號

刪除 `token.pickle`，再執行 Script 重新登入。

> 注意：如果 Google Cloud 專案嘅 OAuth consent screen 仍然係 **Testing** 狀態，refresh token 約 **7 日**後會失效，需要重新登入。想避免就要將 app 發佈（Publish app）。

### 出現 `FutureWarning` / `NotOpenSSLWarning`

例如 Python 3.9 已過支援期、urllib3 使用 LibreSSL 等警告，唔影響上傳功能，可以忽略。

### 上傳好慢

- 確認 `chunk_size_mb` 唔好太細（建議 16–32）
- 上傳速度主要取決於網絡頻寬；有線網絡通常比 Wi-Fi 穩定
- 大檔案（例如 9GB）本身需要較長時間，中斷後會續傳，唔使由頭嚟

---

## 6. 檔案結構

```text
youtube_video_upload/
├── batch_upload.py       # 主程式
├── client_secrets.json   # OAuth 憑證（自行下載，勿分享）
├── token.pickle          # 登入 token（首次執行後產生，勿分享）
├── upload_state.json     # 上傳進度記錄（自動產生）
└── README.md
```

---

## 7. 安全性提醒

以下檔案包含敏感資料，**切勿**上載到 GitHub、雲端硬碟或傳送給他人：

- `client_secrets.json`（OAuth client secret）
- `token.pickle`（等同你嘅登入授權）
- `upload_state.json`（包含你本機路徑同影片 ID）

如果憑證外洩，請即刻去 Google Cloud Console 刪除該 OAuth client ID 並重新建立。
