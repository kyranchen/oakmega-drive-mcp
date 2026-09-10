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

架構決策與取捨請見 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

---

## 目錄

- [方式一：使用已部署的服務](#方式一使用已部署的服務最快)
- [方式二：自行部署](#方式二自行部署)
- [設定項目](#設定項目)
- [OAuth 流程](#oauth-流程)
- [Token 存放方式](#token-存放方式)
- [疑難排解](#疑難排解)
- [功能範圍與已知限制](#功能範圍與已知限制)

---

## 方式一：使用已部署的服務（最快）

伺服器已部署在 Cloud Run，可直接安裝使用。

> **需要先加入測試使用者。** OAuth 同意畫面目前處於 Testing 模式（原因見
> [疑難排解](#google-說這個應用程式未經驗證)），只有名單內的 Google 帳號能完成授權。
> OakMega 提供的兩個 reviewer 帳號已加入。若需要其他帳號，請告知我加入名單。

### 1. 安裝 Claude Code

```bash
npm install -g @anthropic-ai/claude-code
```

### 2. 開啟一個新的 session 並登入

```bash
claude
```

> 用 `/plugin` 指令需要**終端機版本**的 Claude Code。桌面版 App 的 Code 分頁沒有這個介面。

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
2. 出現「Google hasn't verified this app」警告時，點 **Advanced** →
   **Go to oakmega-drive-mcp (unsafe)**（這是 Testing 模式的正常行為，說明見疑難排解）
3. 同意「查看你的 Google 雲端硬碟檔案」權限
4. 瀏覽器顯示授權完成後即可關閉

### 5. 開始提問

```
列出 Google Drive 資料夾內有什麼檔案
讀一下那個 PDF 的內容
那張圖片是什麼
```

---

## 方式二：自行部署

若要用你自己的 Google Cloud 專案和 Drive 資料夾，依下列步驟建立完整環境。

### 前置需求

- Google Cloud 帳號（會用到 Cloud Run 與 Firestore，用量在免費額度內）
- 已安裝並登入 [gcloud CLI](https://cloud.google.com/sdk/docs/install)
- Python 3.11 以上（僅本機開發需要）

### 1. 建立 Google Cloud 專案並啟用 API

```bash
gcloud projects create YOUR_PROJECT_ID
gcloud config set project YOUR_PROJECT_ID
```

```bash
gcloud services enable drive.googleapis.com run.googleapis.com firestore.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
```

### 2. 建立 Firestore 資料庫

```bash
gcloud firestore databases create --location=asia-east1
```

> **位置選定後無法變更。** 建議與 Cloud Run 使用同一個區域以降低延遲。

### 3. 設定 OAuth 同意畫面

前往 **Google Auth Platform → Branding**（[主控台](https://console.cloud.google.com/auth/branding)）：

1. User type 選 **External**
2. 填寫應用程式名稱與聯絡信箱
3. 在 **Data Access** 加入範圍：`https://www.googleapis.com/auth/drive.readonly`
4. 在 **Audience** 的 Test users 加入所有要使用這個 plugin 的 Google 帳號

> **不要按「Publish app」。** `drive.readonly` 屬於受限範圍（restricted scope），
> 發布到正式環境需要 Google 審查與安全評估，通常需要數週。維持 Testing 模式即可。

### 4. 建立 OAuth Client

前往 **Google Auth Platform → Clients** → **Create client**：

- Application type：**Web application**
- Authorized redirect URIs：先填 `http://localhost:8080/oauth/google/callback`

記下 **Client ID** 與 **Client secret**。

> Cloud Run 的網址要等第一次部署後才知道，所以這裡先只填 localhost，
> 部署完成後再回來新增第二個。

### 5. 第一次部署

```bash
git clone https://github.com/kyranchen/oakmega-drive-mcp.git
cd oakmega-drive-mcp/server
```

```bash
gcloud run deploy oakmega-drive-mcp --source=. --region=asia-east1 --allow-unauthenticated --set-env-vars="GOOGLE_CLIENT_ID=你的CLIENT_ID,GOOGLE_CLIENT_SECRET=你的CLIENT_SECRET,DRIVE_FOLDER_ID=你的資料夾ID,TOKEN_STORE_BACKEND=firestore,GCP_PROJECT_ID=你的PROJECT_ID,PUBLIC_BASE_URL=https://placeholder.example.com"
```

`DRIVE_FOLDER_ID` 是資料夾網址中的那串代碼：
`https://drive.google.com/drive/folders/`**`1AU6ZW-3NFxme6388YPe98bGWWh9q_E5Y`**

### 6. 用實際網址更新設定

部署完成後會顯示 Service URL。用它更新 `PUBLIC_BASE_URL`：

```bash
gcloud run services update oakmega-drive-mcp --region=asia-east1 --update-env-vars="PUBLIC_BASE_URL=https://你的服務網址"
```

> **這一步不能省。** 伺服器會用這個值產生 OAuth redirect URI 和 discovery 文件，
> 留著 placeholder 的話 Claude Code 會被導向錯誤的位址。

### 7. 回頭補上 redirect URI

回到 **Clients**，在剛才的 OAuth Client 新增第二個 Authorized redirect URI：

```
https://你的服務網址/oauth/google/callback
```

> Google 是**逐字元比對**的。少一個路徑片段、多一個結尾斜線、把 `localhost`
> 換成 `127.0.0.1`，都會得到 `redirect_uri_mismatch`。

### 8. 修改 plugin 設定並安裝

編輯 `plugin/.mcp.json`，把網址換成你的服務：

```json
{
  "mcpServers": {
    "google-drive": {
      "type": "http",
      "url": "https://你的服務網址/mcp"
    }
  }
}
```

推到你自己的 GitHub repo 後，依[方式一](#方式一使用已部署的服務最快)的步驟 3–5 安裝。

### 本機開發（選用）

```bash
cd server
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env      # 填入你的設定
.venv/bin/python -m uvicorn app.main:app --port 8080
```

本機開發時把 `TOKEN_STORE_BACKEND` 設為 `memory` 可完全不依賴 GCP。
若要在本機連 Firestore，需先執行一次：

```bash
gcloud auth application-default login
```

---

## 設定項目

| 環境變數 | 必填 | 說明 |
|---|---|---|
| `GOOGLE_CLIENT_ID` | ✅ | OAuth Client ID |
| `GOOGLE_CLIENT_SECRET` | ✅ | OAuth Client secret |
| `PUBLIC_BASE_URL` | ✅ | 服務自己的對外網址，結尾不加斜線 |
| `DRIVE_FOLDER_ID` | ✅ | 要讀取的資料夾 ID |
| `TOKEN_STORE_BACKEND` | | `firestore`（預設部署用）或 `memory`（本機開發） |
| `GCP_PROJECT_ID` | 用 firestore 時必填 | GCP 專案 ID |
| `FIRESTORE_DATABASE` | | 留空即使用預設資料庫 |
| `LOG_LEVEL` | | 預設 `INFO` |

必填欄位刻意沒有預設值 —— 缺少時服務會在啟動階段就失敗並指出缺哪一項，
而不是帶著錯誤設定安靜地跑起來。

**所有設定都從環境變數讀取，程式碼中沒有任何寫死的憑證。**
Cloud Run 上建議將 `GOOGLE_CLIENT_SECRET` 改由 Secret Manager 提供。

---

## OAuth 流程

這個服務同時扮演兩個角色，中間串起兩段獨立的 OAuth 交握：

```
Claude Code  ←──①──→  本服務（授權伺服器）  ←──②──→  Google（Drive API）
```

- **第 ① 段**：本服務是**授權伺服器**。Claude Code 動態註冊、走 PKCE 授權碼流程，
  取得一組只有本服務認得的 access token。
- **第 ② 段**：本服務是 **Google 的 client**。它保管使用者的 Google 憑證，
  並在需要時自動換發。

**Google 的 refresh token never leaves the server** —— Claude Code 拿到的
token 只在本服務內有意義，且可隨時撤銷。

詳細流程與設計取捨見 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

---

## Token 存放方式

儲存在 Firestore，分為五個集合：

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
  Application Default Credentials 取得身分，專案中沒有金鑰檔案存在。
- **授權碼一次性且交易式刪除。** 讀取與刪除包在 Firestore transaction 內，
  同一組授權碼並發兌換只會有一次成功。

---

## 疑難排解

### Google 說「這個應用程式未經驗證」

正常現象。`drive.readonly` 屬於 Google 的**受限範圍**，發布到正式環境需要
通過審查與第三方安全評估，時程通常以週計。本專案維持在 Testing 模式，
因此會出現這個警告。

點 **Advanced** → **Go to ... (unsafe)** 即可繼續。

### 授權時出現「Access blocked」或無法選擇帳號

該 Google 帳號不在 Testing 模式的測試使用者名單內。請在
**Google Auth Platform → Audience → Test users** 加入該帳號。

### `redirect_uri_mismatch`

伺服器送出的 redirect URI 與 OAuth Client 註冊的不一致。檢查：

1. `PUBLIC_BASE_URL` 結尾**沒有**斜線
2. OAuth Client 中註冊的是 `<PUBLIC_BASE_URL>/oauth/google/callback`（完整路徑）
3. 設定變更後可能需要一兩分鐘生效

### 列出來是空的，但資料夾裡明明有檔案

- 確認授權時使用的 Google 帳號**確實有該資料夾的存取權**
- 確認 `DRIVE_FOLDER_ID` 正確（是網址中 `/folders/` 後面那一段）

### PDF 回報「no text layer」

該 PDF 是掃描的圖片，沒有文字層。本服務不執行 OCR，這是刻意的範圍界定。

### 查看伺服器日誌

```bash
gcloud logging read 'resource.type=cloud_run_revision AND resource.labels.service_name=oakmega-drive-mcp' --limit=30 --freshness=15m --format="value(textPayload)"
```

---

## 功能範圍與已知限制

依作業範圍，**只實作讀取**：

| 功能 | 狀態 |
|---|---|
| OAuth 授權 | ✅ |
| 列出資料夾檔案（含子資料夾） | ✅ |
| 讀取檔案內容 | ✅ |
| 搜尋 / 寫入 / 監聽變更 | ❌ 不在範圍內 |

**支援的檔案類型**

| 類型 | 處理方式 |
|---|---|
| PDF | 抽取文字，附頁碼標記；掃描檔會明確回報無文字層 |
| 圖片（PNG / JPEG / GIF / WebP） | 原始位元組傳給 Claude，由其原生多模態能力判讀 |
| Google Docs / Sheets / Slides | 匯出為 Markdown / CSV / 純文字 |
| 純文字、JSON、XML | 直接解碼 |
| 其他 | 回報不支援，並列出支援的類型 |

**已知限制**

- 單一檔案上限 5MB
- 文字內容超過 10 萬字元會截斷，並在回應中標示
- 不執行 OCR
- 已註冊的 MCP client 不會自動清理
- `googleapiclient` 為同步函式庫，Drive 呼叫透過執行緒池執行

---

## 專案結構

```
.claude-plugin/marketplace.json   plugin marketplace 定義
plugin/                           Claude Code plugin（指向遠端 MCP server）
server/app/
  config.py                       所有設定的唯一來源
  errors.py                       領域例外
  oauth/                          兩段 OAuth 交握
    provider.py                     對 Claude Code：授權伺服器
    routes.py                       Google callback（兩段交握的接合點）
    google_flow.py                  對 Google：OAuth client
  storage/                        Token 儲存（Protocol + 兩種後端）
  drive/                          Drive 存取（不含 MCP 相關邏輯）
  mcp_server/                     MCP 協定層
scripts/deploy.sh                 一行部署
```
