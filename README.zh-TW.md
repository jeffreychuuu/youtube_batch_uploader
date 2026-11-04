# YouTube 批量上傳工具

[English](README.md) | [繁體中文](README.zh-TW.md)

使用 Python、官方 YouTube Data API v3 與 OAuth 2.0，將本機整個資料夾的影片批量上傳到 YouTube，並自動加入指定的播放清單。

支援**斷點續傳**：上傳中途斷線、關機或按下 Ctrl+C，下次執行會自動從斷點繼續，已完成的影片不會重複上傳。

---

## 功能特色

- 遞迴掃描指定資料夾，自動上傳所有支援格式的影片
- 支援 VOB（DVD）：同一資料夾的 `VTS_01_1.VOB`、`VTS_01_2.VOB`… 會按編號合併並用 ffmpeg 轉成 MP4，合併後的影片以資料夾名稱為標題
- 長片會按 `part_duration_hours` 分段（預設每 1 小時一段），標題加上 `[part 1]`、`[part 2]`
- 支援 cut-list：上傳前剪走被 YouTube flag 嘅時間範圍（`cuts.json`）
- 可透過 `convert_extensions` 指定其他格式（例如 AVI）先用 ffmpeg 轉檔
- 上傳完成後自動加入（或建立）指定播放清單，重跑不會重複加入
- 進度記錄於 `upload_state.json`，已完成的檔案重跑時直接跳過
- 未完成的上傳會保存 resumable session，重跑時從斷點續傳
- 網絡斷線 / 429 / 5xx 自動以 exponential backoff 重試
- 分塊上傳大小可調整（預設 32MB，必須為 256KB 的整數倍）
- 原子寫入進度檔，強制終止也不會寫壞
- 可設定上傳成功後自動移除本機影片（移至垃圾桶或永久刪除）

---

## 系統需求

- macOS（Windows / Linux 亦適用，只需調整路徑）
- Python 3.8 或以上
- ffmpeg（只在需要上傳 VOB，或把其他格式加入 `convert_extensions` 時需要）
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

### 1.2 安裝 ffmpeg（VOB 需要）

VOB 無法直接上傳到 YouTube，Script 會用 ffmpeg 合併並轉成 MP4，因此 ffmpeg 必須在 `PATH` 上：

```bash
brew install ffmpeg          # macOS
# sudo apt install ffmpeg    # Debian / Ubuntu
```

Windows：由 [ffmpeg.org](https://ffmpeg.org/) 下載並加入 `PATH`。

> 只有 `convert_extensions` 列出的格式才需要 ffmpeg（預設 `.vob`）。其他格式（包括 AVI）會直接上傳。

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
5. 將下載的 JSON 改名為 **`client_secrets.json`**，放在與 `batch_upload.py` 相同的資料夾。

> `client_secrets.json` 內含有 `"installed"` 區塊，就代表是正確的 Desktop App 憑證。

### 1.4 第一次登入授權

第一次執行 Script 時，會自動開啟瀏覽器要求登入 Google 帳號並授權。

- 選擇你要上傳的 **YouTube channel**
- 授權成功後會在同一資料夾產生 `token.pickle`，之後再執行就不需要重新登入

> `token.pickle` 等同於登入憑證，**請勿分享或上載到任何地方**。

---

## 2. 影片清單

本專案版本**不使用 CSV**：Script 會直接遞迴掃描 `CONFIG["root_folder"]` 內所有影片檔案，並以**檔案名稱（不含副檔名）作為影片標題**。唯一例外是多段 VOB 合併後的影片，會以資料夾名稱為標題（見下文）。

支援的副檔名（可在 `CONFIG["video_extensions"]` 修改）：

```
.mp4  .mov  .mkv  .avi  .m4v  .vob
```

**AVI** 是 YouTube 官方支援的格式，預設會直接上傳。如果某個 AVI 使用了 YouTube 無法處理的編碼，可以在 `config.json` 的 `convert_extensions` 加入 `".avi"`，先轉成 MP4 再上傳。

**VOB** 不在 YouTube 官方支援清單內，因此 `.vob` 預設會列入 `convert_extensions`。Script 會：

- 將同一資料夾、同一 title set 的 `VTS_01_1.VOB`、`VTS_01_2.VOB`… 按編號合併成一段影片
- 合併後的影片以資料夾名稱為標題（如果 VOB 放在 `VIDEO_TS` 內，會改用上一層資料夾名稱）
- 略過 DVD 選單檔案（`*_0.VOB` 與 `VIDEO_TS.VOB`）
- 單一 VOB 則沿用原本的檔名作為標題
- 長片按 `part_duration_hours` 分段（預設每 1 小時），每段標題為 `<名稱> [part N]`
- 分段只適用於經 ffmpeg 處理的影片（VOB，或加入 `convert_extensions` 的格式）
- 將轉檔結果（可能有多段）暫存在 `.converted/`，上傳中斷後重跑毋須重新編碼；所有分段上傳成功後自動刪除

也就是說，你只需要：

```text
/path/to/your/videos/
├── 影片 A.mp4
├── 影片 B.mp4
└── 子資料夾/
    └── 影片 C.mov
```

三條影片都會被上傳，標題分別是「影片 A」、「影片 B」、「影片 C」。

如果你想用 CSV 指定每條影片的標題、描述、標籤、權限等，需要另外擴充 Script（目前尚未支援）。

### 2.1 Cut-list：剪走被 YouTube flag 嘅片段

如果 YouTube 話某條片違規並列出要移除嘅時間範圍，喺 script 隔離建立 `cuts.json`：

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

- `folders` 嘅 key 對應合併 VOB job（資料夾名；同一個資料夾有多個 title set 就加 `(VTS_01)` / `(VTS_02)`）。`files` 嘅 key 對應單檔 job（檔名不含副檔名）。
- 每個 entry **必須且只能**填 `original` 或 `parts` 其中一個；兩者同時出現、或者兩者都冇，都會報錯：
  - `original`：範圍係指 **合併後完整影片嘅 timeline**（同 YouTube 顯示嗰條 timeline 一樣），喺分段之前剪。
  - `parts`：範圍相對於某個 part，例如 `"parts": {"2": ["5:00-6:00"]}`。Script 會用 `part_duration_hours` 自動加返該 part 嘅起始 offset，唔使你手動計完整 timeline。需要開啟分段（`part_duration_hours > 0`）。
- 時間格式：`MM:SS` 或 `HH:MM:SS`；開始/結束用 `-`、`–`、`—`、`~` 或 `to` 分隔。
- 舊嘅 flat 格式仍然支援：key 可以係 job 標題、第一個來源檔嘅絕對路徑、或 `"<title> [part N]"`，value 係範圍陣列。
- JSON 唔支援 comment；`cuts.example.json` 只示範兩種合法 entry 寫法，冇任何 comment 語法。
- 剪輯會喺 ffmpeg 轉檔時進行，之後先照常分段。
- 改 cut-list 會令轉檔 cache 失效。如果條片之前已上傳、來源檔案仍然存在，script 會重新處理並上傳修正版；舊嘅 YouTube 影片唔會自動刪除。
- 本機來源檔案嘅刪除一律跟隨 `remove_local_after_upload`，設有 cut-list 都一樣。
- `cuts.json` 已加入 `.gitignore`；格式可參考 `cuts.example.json`。

---

## 3. 設定與執行

### 3.1 修改設定

複製 `config.example.json` 為 `config.json`，然後修改其中的設定：

```bash
cp config.example.json config.json
```

```json
{
  "root_folder": "/path/to/your/videos",
  "playlist_name": "My Playlist",
  "privacy_status": "unlisted",
  "made_for_kids": false,
  "video_extensions": [".mp4", ".mov", ".mkv", ".avi", ".m4v", ".vob"],
  "convert_extensions": [".vob"],
  "part_duration_hours": 1,
  "chunk_size_mb": 32,
  "max_retries": 5,
  "remove_local_after_upload": false,
  "remove_local_permanent": false,
  "export_converted_source": false,
  "remove_original_after_conversion": false,
  "remove_original_permanent": false
}
```

`config.json` 已加入 `.gitignore`，**不會被 commit**；即使你 fork 了這個 repo，也可以安全地放置自己的路徑。沒有 `config.json` 時，Script 會使用內建的預設值。

| 設定 | 說明 |
|---|---|
| `root_folder` | 影片來源資料夾，會遞迴掃描所有子資料夾 |
| `playlist_name` | 目標播放清單名稱；不存在會自動建立。設為 `""` 就不加入任何清單 |
| `privacy_status` | `public`（公開）/ `unlisted`（不公開，有連結即可觀看）/ `private`（私人） |
| `made_for_kids` | `True` = 兒童導向內容；`False` = 非兒童導向 |
| `video_extensions` | 要掃描的副檔名 |
| `convert_extensions` | 上傳前先用 ffmpeg 轉成 MP4 的副檔名（預設 `[".vob"]`）；加入 `".avi"` 可強制 AVI 轉檔。呢個設定只管轉檔，唔再限制 cut——所有格式都接受 cuts |
| `part_duration_hours` | 每段長度（小時），預設 `1`；`0` = 不分段。**所有 upload** 都適用，包括 MP4/MOV/AVI。未驗證帳戶可設 `0.25`（15 分鐘上限） |
| `chunk_size_mb` | 每個上傳分塊的大小，必須為 256KB 的整數倍；大檔案建議 16–32 |
| `max_retries` | 網絡錯誤 / 5xx 時每個分塊的最多重試次數 |
| `remove_local_after_upload` | `true` = 上傳成功後移除本機來源；`false` = 保留（預設）。Golden 檔案永遠唔會被移除 |
| `remove_local_permanent` | 上傳後清理嘅移除方式：`false` = 移至垃圾桶（可還原，預設）/ `true` = 永久刪除（無法還原） |
| `export_converted_source` | `true` = 將轉檔後嘅 MP4 保留喺來源旁邊（或 `VIDEO_TS` 上一層），並作為 source of record 重用 |
| `remove_original_after_conversion` | `true` = 轉檔後 MP4 驗證成功就移除原始檔案（需要 `export_converted_source`） |
| `remove_original_permanent` | 轉檔時清理嘅移除方式：`false` = 移至垃圾桶（預設）/ `true` = 永久刪除。同 `remove_local_permanent` 分開 |

**轉檔來源與 golden source。** 開啟 `export_converted_source` 之後，轉檔後嘅檔案會成為 *golden source*：之後嘅 run 會由佢剪、分段、上傳，唔再重新讀取原始檔案。冇 cut 嘅上傳會用 stream copy 分段（快、唔重新 encode）；有 cut 嘅上傳先會 re-encode。`remove_original_after_conversion` 係唯一會喺轉檔階段移除原始檔案嘅設定——`remove_local_after_upload` 永遠唔會刪除 golden 檔案。

### 3.2 執行

```bash
cd /path/to/youtube_batch_uploader
python batch_upload.py
```

如果系統沒有 `python` 指令：

```bash
python3 batch_upload.py
```

執行後會：

1. 掃描資料夾、合併多段 VOB，並列出上傳項目數量
2. 開啟瀏覽器進行 OAuth 授權（第一次才需要）
3. 找到或建立播放清單
4. 如有 VOB，先用 ffmpeg 轉成 MP4，並按設定分段
5. 逐條上傳影片（或分段），顯示進度百分比
6. 上傳完成後加入播放清單

### 3.3 關於「草稿」與發佈

YouTube API **沒有「草稿」概念**，上傳完成後影片即會以 `privacy_status` 指定的狀態存在：

| `privacy_status` | 效果 |
|---|---|
| `private` | 只有頻道擁有者可以觀看，最接近「草稿」，之後可在 YouTube Studio 手動改為公開 |
| `unlisted` | 不會出現在公開搜尋結果，但有連結的人可以觀看 |
| `public` | 上傳完成即公開 |

### 3.4 上傳後自動清理本機檔案

在 `config.json` 設定：

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
- 合併或分段上傳的 VOB，要等所有分段都上傳成功先會移除來源檔案
- 有 cut-list 嘅影片亦一樣，會跟 `remove_local_after_upload` 清理本機檔案
- 之前已經上傳過的影片，重跑時亦會按照此設定清理
- 移除失敗（例如權限不足）只會顯示警告，不會中斷其他影片
- `remove_local_permanent: false`（垃圾桶模式）在 macOS 首次使用時，系統可能會詢問「終端機想控制 Finder」，需要允許
- 如果系統已安裝 `send2trash`，會優先使用它，避免 Finder 權限提示：

  ```bash
  pip3 install send2trash
  ```

---

## 4. 斷點續傳與進度記錄

### 4.1 `upload_state.json`

Script 會在同一資料夾產生 `upload_state.json`，記錄每條影片：

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

- `parts` 內每個分段有一個 entry；單段影片只有一個 entry
- 每上傳完一個分塊就會更新一次
- 已上傳完成的分段，下次執行會直接跳過
- 未完成的分段會保存 `resumable_uri` 與 `bytes_sent`，下次執行自動續傳
- 如果檔案大小或修改時間改變，會當作新版本重新上傳
- 更改 `part_duration_hours` 只會套用於新項目或未完成項目；已完成上傳的影片唔會自動重新上傳
- 舊版本寫落嘅 state（頂層 `video_id`）會自動遷移

### 4.2 中途斷了怎麼辦？

直接再執行一次：

```bash
python batch_upload.py
```

- 已完成的影片：跳過
- 未完成的影片：從斷點繼續
- 播放清單：如果之前未能加入成功，會補加入

> Google 的 resumable session 一般有效約 7 日；過期後會自動改為重新上傳該影片。

### 4.3 強制重新上傳

- 重新上傳**某一條**影片：在 `upload_state.json` 刪除該檔案的 entry
- **全部**重新上傳：刪除整個 `upload_state.json`

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

### 上傳 VOB 時出現 `找不到 ffmpeg`

請先安裝 ffmpeg（見 1.2），並確認它在 `PATH` 上：

```bash
ffmpeg -version
```

沒有 ffmpeg 時，VOB 項目會顯示錯誤並跳過，其他格式仍會正常上傳。

### AVI 上傳失敗或播放有問題

AVI 是 YouTube 官方支援的格式，但 AVI 內含的某些編碼未必支援。在 `config.json` 的 `convert_extensions` 加入 `".avi"`，Script 便會先將 AVI 轉成 MP4（H.264/AAC）再上傳。

### VOB 轉檔很慢或佔用大量磁碟空間

合併與重新編碼 DVD 影片相當耗用 CPU。轉檔結果會暫存在 `.converted/` 直到上傳成功，請預留大約等於來源總大小的可用空間。

### 上傳時被指影片太長

未驗證的 YouTube 帳戶只可以上傳 15 分鐘以內的影片。可以前往 <https://www.youtube.com/verify> 驗證帳戶，或將 `config.json` 的 `"part_duration_hours"` 設為 `0.25`，每 15 分鐘分割一段。已驗證帳戶每段最多 12 小時 / 256 GB。

### 影片分成比預期更多段

`part_duration_hours` 控制每段長度（預設 `1`，設 `0` 即不分段）。設定只會套用於新項目或未完成項目；已完成上傳的影片唔會自動重新上傳。如要重新處理某個項目，請喺 `upload_state.json` 刪除該 entry。

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
├── batch_upload.py       # 入口（python3 batch_upload.py）
├── youtube_uploader/     # 實作 package
│   ├── cli.py            # 上傳流程與流程控制
│   ├── config.py         # config.json 載入與檔案路徑
│   ├── cuts.py           # cuts.json 解析與 part offset
│   ├── state.py          # upload_state.json 進度記錄
│   ├── files.py          # 掃描、VOB 分組與本機檔案清理
│   ├── ffmpeg.py         # 探測、轉檔、分段與 cache
│   └── youtube_api.py    # OAuth、播放清單與續傳上傳
├── tests/                # 單元測試（python3 -m unittest）
├── config.example.json   # 設定範例（可 commit）
├── config.json           # 你的本機設定（不會被 commit）
├── client_secrets.json   # OAuth 憑證（自行下載，請勿分享）
├── token.pickle          # 登入 token（首次執行後產生，請勿分享）
├── upload_state.json     # 上傳進度記錄（自動產生）
├── cuts.json             # 本機 cut-list（不會 commit）
├── cuts.example.json     # cut-list 範例
├── .converted/           # ffmpeg 轉檔暫存（自動產生，可安全刪除）
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
- `upload_state.json`（包含你本機的路徑與影片 ID）

如果憑證外洩，請立即前往 Google Cloud Console 刪除該 OAuth client ID 並重新建立。
