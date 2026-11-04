# YouTube 批量上傳工具

[English](README.md) | [繁體中文](README.zh-TW.md)

使用 Python、官方 YouTube Data API v3 與 OAuth 2.0，將本機整個資料夾的影片批量上傳到 YouTube，並自動加入指定的播放清單。

支援**斷點續傳**：上傳中途斷線、關機或按下 Ctrl+C，下次執行會自動從斷點繼續，已完成的影片不會重複上傳。

---

## 功能特色

- 遞迴掃描指定資料夾，自動上傳所有支援格式的影片
- 支援 VOB（DVD）：同一資料夾的 `VTS_01_1.VOB`、`VTS_01_2.VOB`… 會按編號合併並用 ffmpeg 轉成 MP4，合併後的影片以 `<folder> [combined]` 為標題
- 每條片會以單一檔案上傳
- 支援剪接（cut-list）：上傳前剪走被 YouTube flag 嘅時間範圍（`config/cuts_config.json`）
- 支援 golden source：保留轉檔後嘅 MP4，之後再由佢剪／上傳；`convert_only.py` 可以喺毋須 YouTube 授權嘅情況下準備 golden 檔案
- 可透過 `convert_extensions` 指定其他格式（例如 AVI）先用 ffmpeg 轉檔
- 上傳完成後自動加入（或建立）指定播放清單，重跑不會重複加入
- 進度記錄於 `state/upload_state.json`，已完成的檔案重跑時直接跳過
- 未完成的上傳會保存 resumable session，重跑時從斷點續傳
- 網絡斷線 / 429 / 5xx 自動以 exponential backoff 重試
- 分塊上傳大小可調整（預設 32MB，必須為 256KB 的整數倍）
- 原子寫入進度檔，強制終止也不會寫壞
- 可設定上傳成功後自動移除非 golden 本機檔案（移至垃圾桶或永久刪除）；golden source 永遠保留

---

## 系統需求

- macOS（Windows / Linux 亦適用，只需調整路徑）
- Python 3.8 或以上
- ffmpeg（任何需要轉檔嘅格式，即 `convert_extensions` 入面嘅檔案，以及執行剪接時都需要）
- 一個已建立頻道的 YouTube / Google 帳號；上傳超過 15 分鐘的影片需要驗證帳戶（YouTube 上限為每段 12 小時 / 256 GB）
- 已啟用 YouTube Data API v3 的 Google Cloud 專案

---

## 1. 前置準備

### 1.1 安裝套件

在 Terminal 執行：

```bash
pip3 install google-api-python-client google-auth-oauthlib google-auth-httplib2
```

> 如果沒有 `pip3`，請先確認是否已安裝 Python 3：`python3 --version`。
> 本機如果沒有 `python` 指令，請改用 `python3`（或自行建立 alias / wrapper）。

### 1.2 安裝 ffmpeg（轉檔與剪接需要）

有啲格式（例如 VOB）無法直接上傳到 YouTube。Script 會用 ffmpeg 將 `convert_extensions` 入面任何格式轉成 MP4，亦會用 ffmpeg 執行剪接，因此 ffmpeg 必須在 `PATH` 上：

```bash
brew install ffmpeg          # macOS
# sudo apt install ffmpeg    # Debian / Ubuntu
```

Windows：由 [ffmpeg.org](https://ffmpeg.org/) 下載並加入 `PATH`。

> `convert_extensions` 入面任何格式（預設 `.vob`；加入 `.avi`、`.m2ts` 等就會轉埋）同所有剪接都需要 ffmpeg。唔喺 `convert_extensions` 嘅格式會直接上傳。

### 1.3 建立 YouTube API 憑證

1. 前往 [Google Cloud Console](https://console.cloud.google.com/)，建立新專案（或選用現有專案）。
2. 進入 **APIs & Services → Library**，搜尋 **YouTube Data API v3**，按 **Enable** 啟用。
3. 進入 **APIs & Services → OAuth consent screen**：
   - User type 選擇 **External**
   - 填寫 App name、User support email、Developer contact 等資料
   - 在 **Test users** 加入你將會用來上傳的 Google / YouTube 帳號
4. 進入 **APIs & Services → Credentials → Create Credentials → OAuth client ID**：
   - Application type 選擇 **Desktop app**
   - 建立後按 **Download JSON** 下載憑證
5. 將下載的 JSON 改名為 **`client_secrets.json`**，放在專案根目錄。

> `client_secrets.json` 內含有 `"installed"` 區塊，就代表是正確的 Desktop App 憑證。

### 1.4 第一次登入授權

第一次執行 Script 時，會自動開啟瀏覽器要求登入 Google 帳號並授權。

- 選擇你要上傳的 **YouTube channel**
- 授權成功後會在專案根目錄產生 `token.pickle`，之後再執行就不需要重新登入

> `token.pickle` 等同於登入憑證，**請勿分享或上載到任何地方**。

---

## 2. 影片清單

Script 會直接遞迴掃描 `root_folder` 內所有影片檔案，並以**檔案名稱（不含副檔名）作為影片標題**。唯一例外是多段 VOB 合併後的影片，會以 `<folder> [combined]` 為標題（見下文）。

上傳只會掃描 `config/upload_config.json` 嘅 `video_extensions`（預設 `[".mp4"]`，即 golden 輸出）。只有可以直接上傳嘅格式先好加其他 extension。

**AVI** 是 YouTube 官方支援的格式，預設會直接上傳。如果某個 AVI 使用了 YouTube 無法處理的編碼，可以在 `config/convert_config.json` 的 `convert_extensions` 加入 `".avi"`，先轉成 MP4 再上傳。

**VOB** 不在 YouTube 官方支援清單內，因此 `.vob` 預設會列入 `convert_extensions`。Script 會：

- 將同一資料夾、同一 title set 的 `VTS_01_1.VOB`、`VTS_01_2.VOB`… 按編號合併成一段影片
- 合併後的影片以 `<folder> [combined]` 為標題（如果 VOB 放在 `VIDEO_TS` 內，會改用上一層資料夾名稱）
- 略過 DVD 選單檔案（`*_0.VOB` 與 `VIDEO_TS.VOB`）
- 單一 VOB 則沿用原本的檔名作為標題
- 用 ffmpeg 將來源轉成單一 MP4（H.264/AAC）
- 將轉檔後嘅 MP4 保留喺來源旁邊（或 `VIDEO_TS` 上一層）做 golden source
- 剪接 re-encode 會暫存喺 `.converted/`，上傳中斷後重跑毋須重新編碼；上傳成功後自動刪除

也就是說，你只需要：

```text
/path/to/your/videos/
├── 影片 A.mp4
├── 影片 B.mp4
└── 子資料夾/
    └── 影片 C.mp4
```

三條影片都會被上傳，標題分別是「影片 A」、「影片 B」、「影片 C」。

### 2.1 剪接（cut-list）：剪走被 YouTube flag 嘅片段

以下所講嘅「剪接」就係將指定時間範圍由影片移除。如果 YouTube 話某條片違規並列出要移除嘅時間範圍，喺 `config/` 建立 `cuts_config.json`：

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

所有剪接範圍都放喺 `cuts` key 下面。Flat range list 係單一批次，時間以**完整未剪 golden 影片 timeline** 為準。

如果 YouTube flag 嘅係**已經剪過嘅版本**，唔使自己計 offset，加多一批就得：

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

- Nested 格式係有次序嘅批次：
  - 第 1 批用 golden timeline
  - 第 2 批用第 1 批剪完之後嘅 timeline（即係上傳版本）
  - 第 3 批用第 1+2 批剪完之後嘅 timeline，如此類推
- Script 會自動將每批轉返 golden timeline，最後合併成一組剪接範圍，**ffmpeg 只行一次**。
- 批次範圍可以跨越之前已剪走嘅片段；script 會自動將佢拆開，對應返 golden timeline 嘅各部分。
- 例子：第 1 批剪 `20:00-30:00`；YouTube 喺上傳版本 flag `25:00-27:00`。將佢加做第 2 批，script 會自動對應返 golden timeline 嘅 `35:00-37:00`。

**剪接 encoder 設定。** `cuts_config.json` 最頂可以有兩個 reserved keys，控制剪接 re-encode 點做：

```json
{
  "cut_encoder": "h264_videotoolbox",
  "cut_quality": 65,
  "cuts": {
    "Video Title": ["20:00-30:00"]
  }
}
```

- `cut_encoder`：`libx264`（預設）或 `h264_videotoolbox`（macOS 硬體 encoder，快好多）
- `cut_quality`：`libx264` 用 CRF `0`–`51`（越低畫質越好、檔案越大）；`h264_videotoolbox` 用 `q:v` `0`–`100`（越高畫質越好、檔案越大）。`65` 係中等偏高嘅硬體設定；想更好畫質可以用 `70`–`75`，想檔案細啲可以用 `50`–`60`。

呢兩個設定會套用到所有剪接 re-encode；Golden 轉檔用 `config/convert_config.json` 嘅 `convert_encoder` / `convert_quality`（預設 `libx264` CRF 20）。

**自動 description。** 所有有剪接嘅上傳會自動加上 `因違反 YouTube 政策，已剪走以下片段（共 X）：`，後面列出被剪走嘅範圍；`X` 係總共剪走嘅時間。

其他規則：

- `cuts` 入面嘅 key 係影片標題：合併 group 用 `<folder> [combined]`，單檔用檔名（不含副檔名）。
- 時間格式：`MM:SS` 或 `HH:MM:SS`；開始/結束用 `-`、`–`、`—`、`~` 或 `to` 分隔。
- JSON 唔支援 comment；格式可參考 `config/cuts_config.example.json`。
- 剪接會喺 ffmpeg 轉檔時進行。
- 改剪接清單會令轉檔 cache 失效。如果條片之前已上傳、來源檔案仍然存在，script 會重新處理並上傳修正版；舊嘅 YouTube 影片唔會自動刪除。
- Golden 檔案永遠唔會被 `remove_local_after_upload` 刪除；原檔移除由 `remove_original_after_conversion` 控制。
- `config/cuts_config.json` 已加入 `.gitignore`；格式可參考 `config/cuts_config.example.json`。

---

## 3. 設定與執行

### 3.1 修改設定

設定分成幾個檔案。複製範例，然後修改：

```bash
cp config/convert_config.example.json config/convert_config.json
cp config/upload_config.example.json config/upload_config.json

# 可選：只有已經有剪接範圍時才需要
cp config/cuts_config.example.json config/cuts_config.json
```

**`config/convert_config.json`** — 掃描乜嘢、點樣產生 golden MP4：

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

| 設定 | 說明 |
|---|---|
| `root_folder` | 影片來源資料夾，會遞迴掃描所有子資料夾 |
| `convert_extensions` | 先用 ffmpeg 轉成 MP4 的副檔名（預設 `[".vob"]`）；加入 `".avi"` 可強制 AVI 轉檔。Convert script 只會掃描呢啲副檔名 |
| `combine` | `true`（預設）會合併 VOB title set，同一個 folder 內**任何** `convert_extensions` 格式嘅檔案亦會按 natural order 合併；`false` 每條片獨立 job |
| `combined_name_source` | 合併 job 嘅命名來源：`folder`（預設，用所在資料夾名）、`root`（用 `root_folder` 名）或 `prefix`（同一個資料夾內所有影片檔名嘅共同 prefix；VOB 會 fallback 用資料夾名） |
| `output_folder` | 留空（預設）會將 golden MP4 放喺來源旁邊。設定資料夾就會將 golden 直接放去該 folder；記得將 `upload_config.root_folder` 指去呢個 folder 以便上傳。標題必須唯一，否則轉檔會停低，唔會覆蓋 |
| `convert_encoder` | Golden 轉檔用嘅 encoder：`libx264`（預設，壓縮效率最好）或 `h264_videotoolbox`（macOS 硬體，快但效率較低） |
| `convert_quality` | `libx264` 用 CRF `0`–`51`（越低畫質越好、檔案越大）；`h264_videotoolbox` 用 `q:v` `0`–`100`（越高畫質越好、檔案越大） |
| `remove_original_after_conversion` | `true` = 轉檔後 MP4 驗證成功就移除原始檔案；之後再行 convert 亦會清走已轉檔 job 嘅原檔 |
| `remove_original_permanent` | 轉檔時清理嘅移除方式：`false` = 移至垃圾桶（預設）/ `true` = 永久刪除 |
| `remove_empty_folders` | `true` = 轉檔清理後，如果來源資料夾變成空，就一併移除（會向上清理，但唔會移除 `root_folder` 本身）。只喺 `remove_original_after_conversion` 為 `true` 時有效 |

**`config/upload_config.json`** — 掃描、發佈（完全獨立）：

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

| 設定 | 說明 |
|---|---|
| `root_folder` | 上傳用嘅來源資料夾；同 convert config 完全獨立 |
| `video_extensions` | 上傳要掃描嘅副檔名（預設 `[".mp4"]`，即 golden 輸出）。只有可以直接上傳嘅格式先好加其他 extension |
| `playlist_name` | 目標播放清單名稱；不存在會自動建立。設為 `""` 就不加入任何清單 |
| `privacy_status` | `public`（公開）/ `unlisted`（不公開，有連結即可觀看）/ `private`（私人） |
| `made_for_kids` | `True` = 兒童導向內容；`False` = 非兒童導向 |
| `chunk_size_mb` | 每個上傳分塊的大小，必須為 256KB 的整數倍；大檔案建議 16–32 |
| `max_retries` | 網絡錯誤 / 5xx 時每個分塊的最多重試次數 |
| `remove_local_after_upload` | `true` = 上傳成功後移除本機來源；`false` = 保留（預設）。Golden 檔案永遠唔會被移除 |
| `remove_local_permanent` | 上傳後清理嘅移除方式：`false` = 移至垃圾桶（可還原，預設）/ `true` = 永久刪除（無法還原） |

你嘅本機 config 檔案都放在 `config/`，並已加入 `.gitignore`，**不會被 commit**；即使你 fork 了這個 repo，也可以安全地放置自己的路徑。各 config **完全獨立**：`config/upload_config.json` 永遠唔會讀 `config/convert_config.json`，剪接清單只會喺上傳時讀取。冇檔案時會用內建預設值。

剪接清單同 encoder 設定放喺 `config/cuts_config.json`，詳見 2.1。

**檔案合併。** `combine` 決定轉檔前要唔要合併檔案。`true`（預設）會合併 VOB title set，同一個 folder 內**任何** `convert_extensions` 格式嘅檔案亦會按 natural order 合併（例如 `.avi`、`.mkv` 或 `.m2ts` 分段）；`false` 每個來源檔各自一個轉檔 job 同 upload。合併後嘅一組默認叫 `<folder> [combined]`，並會產生一個 golden MP4、一個 upload；用 `false` 時每個來源檔保留自己嘅 filename 做 title，各自有 golden MP4 同 upload。唔喺 `convert_extensions` 嘅格式會直接上傳，唔會合併。`combined_name_source` 控制合併後嘅標題：`folder`（預設）用所在資料夾名、`root` 用 `root_folder` 名、`prefix` 用同一個資料夾內所有影片檔名嘅共同 prefix（例如 `Event-100-00.AVI` + `Event-100-01.AVI` → `Event-100 [combined]`）。

**轉檔來源與 golden source。** 轉檔後嘅檔案一定會成為 *golden source*：之後嘅 run 會由佢剪、上傳，唔再重新讀取原始檔案。如果設定咗 `output_folder`，golden 會直接寫入該 folder；記得將 `upload_config.root_folder` 指去該 folder，`batch_upload.py` 才搵到。如果兩個 job 產生同一個標題，轉檔會停低，唔會覆蓋。`remove_empty_folders` 可以清走原檔移除後變成空嘅來源資料夾。有剪接先會 re-encode。`remove_original_after_conversion` 係唯一會喺轉檔階段移除原始檔案嘅設定——`remove_local_after_upload` 永遠唔會刪除 golden 檔案。Golden job 用 recorded title 做 `config/cuts_config.json` key，所以 `cuts_config.json` 照樣有效。如果兩個來源會寫出同一個 golden 檔案，程式會直接報錯，唔會覆蓋。

### 3.2 執行

只上傳現有 golden／直接可上傳嘅檔案（唔會轉檔）：

```bash
cd /path/to/youtube_batch_uploader
python3 batch_upload.py
```

執行後會：

1. 按 `video_extensions`（預設 `[".mp4"]`，即 golden 輸出）掃描 `root_folder`，列出上傳項目數量
2. 開啟瀏覽器進行 OAuth 授權（第一次才需要）
3. 找到或建立播放清單
4. 將檔案剪接後上傳
5. 上傳完成後加入播放清單

想先轉檔，行 `python3 convert_only.py`（見 3.3）。想一句 command 做晒：

```bash
python3 convert_and_upload.py
```

呢個會先用 `config/convert_config.json` 轉檔，再用 `config/upload_config.json` 上傳。

### 3.3 只轉檔（唔上傳）

如果想預先準備 golden MP4、完全唔上傳：

```bash
python3 convert_only.py
```

呢條路徑**永遠唔會**進行 YouTube 授權、亦唔會上傳。只需要 `config/convert_config.json`，唔需要 `config/upload_config.json`。轉檔記錄會寫入 `state/convert_state.json`（同 `state/upload_state.json` 分開）。佢會將所有 `convert_extensions` 入面嘅 job 轉檔，跟隨 `remove_original_after_conversion` / `remove_original_permanent` 決定要唔要移走原檔，跳過已經有 golden source 嘅檔案（`remove_original_after_conversion` 開啟時會順便清走呢啲 job 嘅原檔），最後印出成功／跳過／失敗數量。剪接喺呢個模式會被忽略；golden source 一定係完整未剪版本。

### 3.4 關於「草稿」與發佈

YouTube API **沒有「草稿」概念**，上傳完成後影片即會以 `privacy_status` 指定的狀態存在：

| `privacy_status` | 效果 |
|---|---|
| `private` | 只有頻道擁有者可以觀看，最接近「草稿」，之後可在 YouTube Studio 手動改為公開 |
| `unlisted` | 不會出現在公開搜尋結果，但有連結的人可以觀看 |
| `public` | 上傳完成即公開 |

### 3.5 上傳後自動清理本機檔案

在 `config/upload_config.json` 設定：

```json
"remove_local_after_upload": true,
"remove_local_permanent": false
```

| `remove_local_permanent` | 效果 |
|---|---|
| `false`（預設） | 將影片移至系統垃圾桶（macOS 使用 Finder），可以在垃圾桶還原 |
| `true` | 直接永久刪除檔案，**無法還原** |

注意：

- 只會在影片**上傳成功並取得 video ID** 之後才移除本機檔案
- 如果上傳失敗，本機檔案不會被刪除
- 呢個清理只適用於非 golden 嘅上傳來源；轉檔後嘅 golden MP4 永遠會保留。原檔（VOB/AVI）移除請用 `remove_original_after_conversion`。
- 移除失敗（例如權限不足）只會顯示警告，不會中斷其他影片
- `remove_local_permanent: false`（垃圾桶模式）在 macOS 首次使用時，系統可能會詢問「終端機想控制 Finder」，需要允許
- 如果系統已安裝 `send2trash`，會優先使用它，避免 Finder 權限提示：

  ```bash
  pip3 install send2trash
  ```

---

## 4. 斷點續傳與進度記錄

### 4.1 `upload_state.json` 與 `convert_state.json`

兩個檔案都放喺 `state/`。`upload_state.json` 記錄每條已上傳影片；`convert_state.json` 記錄 golden source index。兩者完全獨立：

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

- `upload_state.json` 係 flat map：來源路徑 → 單一上傳 entry
- 每上傳完一個分塊就會更新一次
- 已完成上傳的影片，下次執行會直接跳過
- 未完成的上傳會保存 `resumable_uri` 與 `bytes_sent`，下次執行自動續傳
- 如果檔案大小或修改時間改變，會當作新版本重新上傳

### 4.2 中途斷了怎麼辦？

直接再執行一次：

```bash
python3 batch_upload.py
```

- 已完成的影片：跳過
- 未完成的影片：從斷點繼續
- 播放清單：如果之前未能加入成功，會補加入

> Google 的 resumable session 一般有效約 7 日；過期後會自動改為重新上傳該影片。

### 4.3 強制重新上傳

- 重新上傳**某一條**影片：在 `state/upload_state.json` 刪除該檔案的 entry
- **全部**重新上傳：刪除整個 `state/upload_state.json`

（刪除前請先關閉正在執行的 Script。）

---

## 5. 常見問題

### `python: command not found`

macOS 新版本預設只有 `python3`，使用 `python3 batch_upload.py` 即可。

### `HttpError 403: quotaExceeded`

YouTube Data API v3 的預設每日配額為 **10,000 units**，而**每次上傳影片消耗 1,600 units**，即每日大約只能上傳 **6 條影片**（其他操作亦會消耗少量配額）。

應對方法：

- 分幾日執行；Script 會記錄進度，第二日再跑會自動繼續
- 在 Google Cloud Console 申請提高配額（Quota increase）

### `HttpError 403` 但不是配額問題

- 確認 `client_secrets.json` 屬於已啟用 YouTube Data API v3 的專案
- 確認登入的帳號已建立 YouTube channel
- 確認該帳號已加入 OAuth consent screen 的 Test users

### 轉換 VOB 時出現 `找不到 ffmpeg`

請先安裝 ffmpeg（見 1.2），並確認它在 `PATH` 上：

```bash
ffmpeg -version
```

沒有 ffmpeg 時，需要轉檔嘅項目會顯示錯誤並跳過；唔喺 `convert_extensions` 嘅格式仍會正常上傳。

### AVI 上傳失敗或播放有問題

AVI 是 YouTube 官方支援的格式，但 AVI 內含的某些編碼未必支援。在 `config/convert_config.json` 的 `convert_extensions` 加入 `".avi"`，`convert_only.py`（或 `convert_and_upload.py`）便會先將 AVI 轉成 MP4（H.264/AAC），之後再上傳。

### VOB 轉檔很慢或佔用大量磁碟空間

合併與重新編碼 DVD 影片相當耗用 CPU。轉檔後嘅 MP4 會保留喺來源旁邊做 golden source；剪接 re-encode 會暫存喺 `.converted/` 直到上傳成功，請預留大約等於來源總大小的可用空間。

### 剪接 re-encode 好慢

喺 `config/cuts_config.json` 用 macOS 硬體 encoder：

```json
"cut_encoder": "h264_videotoolbox",
"cut_quality": 70
```

同一內容下，通常比 `libx264` CRF 20 快大約 3 倍，檔案大小相若。

### 上傳時被指影片太長

未驗證的 YouTube 帳戶只可以上傳 15 分鐘以內的影片。請前往 <https://www.youtube.com/verify> 驗證帳戶，之後就可以上傳更長影片（已驗證帳戶每段最多 12 小時 / 256 GB）。本工具會將每條片以單一檔案上傳。

### 上傳中途斷線 / 電腦休眠

Script 會自動重試；如果仍然失敗，再執行一次就會從斷點續傳。

### `token.pickle` 過期或想更換帳號

刪除 `token.pickle`，再執行 Script 重新登入。

> 注意：如果 Google Cloud 專案的 OAuth consent screen 仍然是 **Testing** 狀態，refresh token 約 **7 日**後會失效，需要重新登入。想避免此情況，請將 app 發佈（Publish app）。

### 出現 `FutureWarning` / `NotOpenSSLWarning`

例如 Python 3.9 已過支援期、urllib3 使用 LibreSSL 等警告，不會影響上傳功能，可以忽略。

### 上傳很慢

- 確認 `chunk_size_mb` 不要太小（建議 16–32）
- 上傳速度主要取決於網絡頻寬；有線網絡通常比 Wi-Fi 穩定
- 大檔案（例如 9GB）本身需要較長時間，中斷後會續傳，不需要從頭開始

---

## 6. 檔案結構

```text
youtube_batch_uploader/
├── batch_upload.py       # 上傳入口（python3 batch_upload.py）
├── convert_only.py       # 轉檔入口（python3 convert_only.py）
├── convert_and_upload.py # 一句 command：先轉檔，再上傳
├── youtube_uploader/     # 實作 package
│   ├── cli.py            # 上傳流程與流程控制
│   ├── convert.py        # 只轉檔流程
│   ├── golden.py         # Golden source 偵測與共存規則
│   ├── pipeline.py       # 來源準備與媒體選擇
│   ├── config.py         # config 載入與檔案路徑
│   ├── cuts.py           # 剪接清單解析、批次對應與 description
│   ├── state.py          # upload／convert state 進度記錄
│   ├── files.py          # 掃描、合併與本機檔案清理
│   ├── ffmpeg.py         # 探測、轉檔與 cache
│   └── youtube_api.py    # OAuth、播放清單與續傳上傳
├── tests/                # 單元測試（python3 -m unittest）
├── config/               # 設定（範例可 commit；本機設定不會 commit）
│   ├── convert_config.example.json # 轉檔設定範例
│   ├── upload_config.example.json  # 上傳設定範例
│   ├── cuts_config.example.json    # 剪接清單範例
│   ├── convert_config.json         # 你的本機轉檔設定
│   ├── upload_config.json          # 你的本機上傳設定
│   └── cuts_config.json            # 你的本機剪接清單
├── client_secrets.json   # OAuth 憑證（自行下載，請勿分享）
├── token.pickle          # 登入 token（首次執行後產生，請勿分享）
├── .converted/           # ffmpeg 轉檔暫存（可安全刪除）
├── state/                # 執行時產生嘅檔案（不會 commit）
│   ├── upload_state.json
│   └── convert_state.json
├── README.md             # 英文說明文件
└── README.zh-TW.md       # 繁體中文說明文件
```

執行單元測試：

```bash
python3 -m unittest
```

---

## 7. 安全性提醒

以下檔案包含敏感資料，**切勿**上載到 GitHub、雲端硬碟或傳送給他人：

- `client_secrets.json`（OAuth client secret）
- `token.pickle`（等同於你的登入授權）
- `state/upload_state.json`（包含你本機的路徑與影片 ID）
- `state/convert_state.json`（包含你本機的路徑）

如果憑證外洩，請立即前往 Google Cloud Console 刪除該 OAuth client ID 並重新建立。
