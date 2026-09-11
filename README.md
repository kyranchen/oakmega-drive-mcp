# Google Drive Reader — Claude Code Plugin

透過自然語言讀取指定 Google Drive 資料夾的 Claude Code plugin。

```
你：列出 Google Drive 資料夾內有什麼檔案
Claude：資料夾內有 2 個檔案：
        1. D-Link Router Manual v2.00.pdf — PDF，583.6KB
        2. 代理人 — PNG 圖片，305.6KB

你：讀一下那個路由器手冊，電源指示燈橘燈恆亮代表什麼
Claude：根據手冊，橘燈恆亮代表開機中、恢復出廠預設中，或重新啟動中。

你：那張叫「代理人」的圖片是什麼內容
Claude：這是一份技術架構說明文件的截圖，標題為「AI 業務開發代理人建置 —
        系統架構規劃」，案號 2026NV000034⋯
```

伺服器已部署在 Google Cloud Run，依下列步驟安裝即可使用。
技術選擇與取捨見 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

---

## 安裝

### 1. 安裝 Claude Code

```bash
npm install -g @anthropic-ai/claude-code
```

> `/plugin` 指令需要**終端機版本**的 Claude Code。桌面版 App 的 Code 分頁沒有這個介面。

### 2. 開啟 session

```bash
claude
```

首次使用需登入 Claude 帳號。

### 3. 安裝 plugin

```
/plugin marketplace add kyranchen/oakmega-drive-mcp
```

```
/plugin install google-drive-reader@oakmega-drive-mcp
```

### 4. 授權 Google 帳號

```
/mcp
```

在清單中選擇 **google-drive**，點選 **Authorize**。瀏覽器會開啟 Google 登入頁面：

1. 選擇你的 Google 帳號
2. 出現「Google hasn't verified this app」時，點 **Advanced** →
   **Go to oakmega-drive-mcp (unsafe)**
   （這是 Testing 模式的正常行為，原因見[疑難排解](#google-說這個應用程式未經驗證)）
3. 同意「查看你的 Google 雲端硬碟檔案」
4. 瀏覽器顯示授權完成後即可關閉

> **需要在測試使用者名單內。** OAuth 同意畫面處於 Testing 模式，
> 只有名單中的 Google 帳號能完成授權。OakMega 提供的兩個 reviewer 帳號已加入。
> 若需要其他帳號，請告知我加入。

### 5. 開始提問

```
列出 Google Drive 資料夾內有什麼檔案
讀一下那個 PDF 的內容
那張圖片是什麼
```

---

## 支援的檔案類型

| 類型 | 處理方式 |
|---|---|
| PDF | 抽取文字並附頁碼標記；掃描檔會明確回報沒有文字層 |
| 圖片（PNG / JPEG / GIF / WebP） | 原始位元組傳給 Claude，由其原生多模態能力判讀 |
| Google Docs / Sheets / Slides | 匯出為 Markdown / CSV / 純文字 |
| 純文字、JSON、XML | 直接解碼 |
| 其他 | 回報不支援，並列出支援的類型 |

依作業範圍，**只實作讀取** —— 不支援搜尋、寫入或監聽變更。

---

## OAuth 流程

服務同時扮演兩個角色，串起兩段獨立的交握：

```
Claude Code  ←──①──→  本服務（授權伺服器）  ←──②──→  Google（Drive API）
```

- **第 ① 段**：Claude Code 動態註冊、走 PKCE 授權碼流程，
  取得一組**只有本服務認得**的 access token
- **第 ② 段**：本服務作為 Google 的 client，保管使用者憑證並自動換發

**Google 的 refresh token 不會離開伺服器。** Claude Code 持有的 token
在其他地方沒有意義，且可隨時撤銷而不影響使用者的 Google 授權。

詳細流程見 [ARCHITECTURE](docs/ARCHITECTURE.md#2-為什麼是兩段-oauth-handshake)。

---

## Token 存放方式

儲存於 Firestore，分為五個集合：

| 集合 | 內容 | 生命週期 |
|---|---|---|
| `credentials` | Google 的 access / refresh token | 長期，直到使用者撤銷 |
| `clients` | 動態註冊的 MCP client | 長期 |
| `access_tokens` | 本服務簽發的 token（**僅存 SHA-256 雜湊**） | 24 小時 |
| `pending_auth` | 授權流程中暫存的請求 | 10 分鐘，用畢即刪 |
| `auth_codes` | 本服務簽發的授權碼 | 10 分鐘，一次性 |

三點設計說明：

- **簽發的 token 只存雜湊值。** 資料庫外洩不會直接得到可用的憑證。
- **不使用任何 service account 金鑰檔。** Cloud Run 透過
  Application Default Credentials 取得身分，專案中沒有金鑰檔存在。
- **授權碼一次性且交易式刪除。** 讀取與刪除包在 Firestore transaction 內，
  同一組授權碼並發兌換只會有一次成功。

所有設定皆由環境變數提供，**程式碼中沒有任何寫死的憑證**。

---

## 疑難排解

### Google 說「這個應用程式未經驗證」

正常現象。`drive.readonly` 屬於 Google 的**受限範圍**，發布到正式環境需要
通過審查與第三方安全評估，時程以週計。本專案維持 Testing 模式，因此會出現警告。

點 **Advanced** → **Go to ... (unsafe)** 即可繼續。

### 授權時出現「Access blocked」或無法選擇帳號

該 Google 帳號不在測試使用者名單內。請告知我要加入的帳號。

### 提示「這個 Google 帳號看不到設定的資料夾」

授權時選到了沒有該資料夾存取權的 Google 帳號。在 `/mcp` 中重新授權，
選擇資料夾分享對象的那個帳號。

### Windows 終端機顯示中文為亂碼

終端機編碼設定，與 plugin 無關。執行 `chcp 65001` 切換為 UTF-8，
並建議使用 Windows Terminal 搭配含中文字形的字型（例如 Microsoft JhengHei Mono）。

### PDF 回報「no text layer」

該 PDF 是掃描的圖片，沒有文字層。本服務不執行 OCR，這是刻意的範圍界定。

### Plugin 裝了但看不到工具

確認在**終端機版本**的 Claude Code 中操作，並在安裝後重新開啟 session。

---

## 專案結構

```
.claude-plugin/marketplace.json   plugin marketplace 定義
plugin/                           Claude Code plugin（指向遠端 MCP server）
server/app/
  config.py                       所有設定的唯一來源
  errors.py                       領域例外
  oauth/                          兩段 OAuth Handshake
    provider.py                     對 Claude Code：授權伺服器
    routes.py                       Google callback（兩段Handshake的接合點）
    google_flow.py                  對 Google：OAuth client
  storage/                        Token 儲存（Protocol + Firestore / 記憶體兩種後端）
  drive/                          Drive 存取（不含 MCP 相關邏輯）
  mcp_server/                     MCP 協定層
server/tests/                     單元測試（`pytest` 執行，不需網路）
```

---

## 已知限制

- 圖片 3.5MB 上限；其他檔案 5MB 上限
- 抽出的文字超過 10 萬字元會截斷，並在回應中標示
- 不執行 OCR
- `googleapiclient` 為同步函式庫，Drive 呼叫透過執行緒池執行

完整的取捨紀錄見 [ARCHITECTURE](docs/ARCHITECTURE.md#已知限制)。
