# 架構說明

技術選擇與取捨的紀錄。安裝與使用方式見 [README](../README.md)。

---

## 目錄

- [整體形狀](#整體形狀)
- [1. 為什麼是 Cloud Run](#1-為什麼是-cloud-run)
- [2. 為什麼是兩段 OAuth Handshake](#2-為什麼是兩段-oauth-handshake)
- [3. 為什麼是 opaque token 而非 JWT](#3-為什麼是-opaque-token-而非-jwt)
- [4. 為什麼是 Firestore](#4-為什麼是-firestore)
- [5. 檔案內容的處理](#5-檔案內容的處理)
- [關於存取範圍](#關於存取範圍)
- [已知限制](#已知限制)

---

## 整體形狀

```
Claude Code  ←──①──→  本服務（Cloud Run）  ←──②──→  Google Drive API
                            ↓
                        Firestore
```

服務同時是兩個角色:對 Claude Code 而言是**授權伺服器**,對 Google 而言是
**OAuth client**。這個雙重身分是整份設計的核心。

### 完整流程

```
1. Claude Code 打 /mcp
   → 401 + WWW-Authenticate（指向 protected-resource metadata）

2. 讀取兩份 discovery 文件

3. POST /register，帶自己的 localhost callback
   → 取得 client_id                            [provider.register_client]
   ↑ 動態註冊是必要的，不是選項：Claude Code 的 callback 是
     http://localhost:<隨機埠>/callback，埠號執行前不可知，無法預先註冊。
     開放註冊是安全的 —— 註冊本身不授予任何存取權，真正的關卡是
     Google 的同意畫面，需要真人操作。

4. 瀏覽器開啟 /authorize
   → 暫存請求，回傳 Google 同意畫面網址        [provider.authorize]
                                                 ↑ 第一次接觸 Google

5. 使用者同意 → Google 導回 /oauth/google/callback
   → 換取 Google token、存入 Firestore
   → 簽發本服務的授權碼、導回 Claude Code      [oauth/routes.py]

6. POST /token（帶 PKCE verifier）
   → SDK 驗證 PKCE → 簽發本服務的 access token
                                       [provider.exchange_authorization_code]

7. 之後每次請求
   → 解析 token → 取得 subject → 讀取該使用者的 Google 憑證
                                       [provider.load_access_token → drive/client]
```

第 1、2、3、6 步的協定細節由 MCP SDK 處理;本專案負責的是其中的決策。

---

## 1. 為什麼是 Cloud Run

**選項**:Cloudflare Workers、Cloud Run、傳統 VM。

初期評估過 Cloudflare Workers,最後選 Cloud Run,主要理由是**語言契合**:
OakMega 的技術棧是 Python/Flask,用 Python 實作對團隊接手比較自然。
兩者都是請求驅動、可縮到零的無伺服器平台,維運負擔沒有實質差異。

**取捨**:Workers 冷啟動較快、部署是推送腳本而非建置映像檔。Cloud Run 需要
建置容器(每次約 3 分鐘),但換來完整的 Python 生態系 —— `pypdf`、
`google-api-python-client` 都能直接使用。

**沒選 VM 的原因**:這個服務只在有人使用時才需要運算,長時間閒置。
為它維護一台常駐機器,成本和維運負擔都不成比例。

---

## 2. 為什麼是兩段 OAuth Handshake

最直覺的做法是讓 Claude Code 直接與 Google 完成授權,本服務只轉發請求。
**這樣 Google 的 refresh token 會落在 client 端。**

那是一把六個月有效、能讀取整個 Drive 的憑證,會被寫進使用者機器上的設定檔,
而服務端完全失去控制權 —— 無法撤銷、無法輪替、無法得知它被如何使用。

因此改為兩段:

| | 誰對誰 | 本服務的角色 |
|---|---|---|
| 第 ① 段 | Claude Code ↔ 本服務 | 授權伺服器 |
| 第 ② 段 | 本服務 ↔ Google | OAuth client |

Claude Code 取得的是**只有本服務認得的 token**。它無法拿去 Google 做任何事,
而本服務可以隨時撤銷它,不影響使用者的 Google 授權。

Google 的憑證從頭到尾只存在於伺服器與 Firestore。

### `subject` 是串起兩段的那條線

Google 的 `sub`(而非 email,因為 email 可能變更)在 callback 階段寫入我們
簽發的授權碼,再帶到 access token,最後用來查出該使用者的 Google 憑證:

```
MCP token → SHA-256 → access_tokens 文件 → subject → credentials 文件 → Drive
```

刻意設計成**兩次查表**而非把 Google token 直接放進 access token,好處是:

- 撤銷分離 —— 刪除某個 MCP token 不影響 Google 授權
- 一位使用者可有多個 MCP token(多台裝置),共用同一份 Google 憑證
- Google token 換發後只需更新一處

---

## 3. 為什麼是 opaque token 而非 JWT

JWT 的主要優勢是**免查表**:token 自帶簽章,驗證時不需要碰資料庫。
**但在這個架構下那個優勢不存在。**

一次 tool 呼叫的實際路徑:

```
store.get_access_token(hash)     → 取得 subject        ← JWT 能省掉這次
store.get_credentials(subject)   → 取得 Google 憑證     ← 省不掉
```

第二次查表是必然的 —— 不論 token 是什麼形式,都必須從儲存層取出該使用者的
Google 憑證才能呼叫 Drive API。而那份憑證不可能放進 JWT(放了就等於把
Google token 交給 client,回到[第 2 節](#2-為什麼是兩段-oauth-handshake)否決的設計)。

因此實際的取捨是:

| | Opaque（採用） | JWT |
|---|---|---|
| 每次請求的資料庫讀取 | 2 次 | 1 次 |
| 可撤銷 | ✅ 刪除記錄即刻生效 | ❌ 到期前無法失效 |
| 簽章金鑰輪替 | 不需要 | 需要 |

用「無法撤銷」換取一次 Firestore 讀取,在這個架構下不划算。

若是驗證完即可服務、不需再查任何資料的 API,JWT 省下的是**唯一**一次查表,
結論會相反。

### 撤銷如何生效

`load_access_token` 每次請求都讀取儲存層,因此刪除記錄的下一個請求就會失敗。
`/revoke` 端點(RFC 7009)已實作並掛載。

撤銷只影響本服務簽發的 token —— 使用者的 Google 授權不受影響,
重新連線不需要再次同意。

### 簽發的 token 只存雜湊

原始值僅在簽發當下回傳給 client,不落地。資料庫被讀取(外洩、備份、
Console、錯誤追蹤服務)不會直接得到可用的憑證 —— 與密碼儲存同樣的理由。

## 4. 為什麼是 Firestore

**這不是可選的加分項,而是平台特性造成的必要條件。**

OAuth 流程橫跨三個獨立的 HTTP 請求(`/authorize`、Google callback、`/token`),
而 Cloud Run 會在多個實例間分配請求且**沒有 session affinity** ——
請求 A 存進 1 號容器記憶體的狀態,請求 B 可能打到 2 號容器。

此外 Cloud Run 預設縮到零,閒置後容器會被回收,記憶體狀態隨之消失。

考慮過的替代方案:

| 方案 | 不採用的原因 |
|---|---|
| Cloud SQL | 需常駐執行個體,月費數十美元;儲存的是 key-value,用不到 SQL |
| Memorystore (Redis) | Cloud Run 連線需設定 VPC connector,設定成本高於實作 Firestore |
| Secret Manager | 形狀不符 —— 適用於應用層級機密,而非 per-user 資料 |

Firestore 的決定性優勢是**不需要金鑰檔**:Cloud Run 透過
Application Default Credentials 使用附掛的 service account,
程式碼中沒有任何憑證。金鑰檔是最容易外洩的資產(誤 commit、放進 CI 設定、
在團隊間傳遞),能不存在最好。

### 儲存層抽象

`TokenStore` Protocol 有兩個實作:Firestore(部署)與記憶體(本機開發)。
切換只需改一個環境變數。

這不是為了抽象而抽象 —— 本機開發時能完全不依賴 GCP,讓 OAuth 流程
(這個專案除錯成本最高的部分)可以在筆電上獨立驗證,不必同時處理
Firestore 權限問題。

### 交易式的一次性授權碼

`pop_pending` 與 `pop_code` 包在 Firestore transaction 內。記憶體實作靠 GIL
免費取得「讀取即刪除」的原子性;跨網路時讀取與刪除是兩次獨立呼叫,
中間的空隙足以讓同一組授權碼被兌換兩次。

已實測驗證:10 個並發請求兌換同一組授權碼,恰好 1 個成功、9 個回報不存在。

過期檢查仍在讀取時執行。Firestore 的原生 TTL policy 刪除是盡力而為
(可能延遲至 24 小時),因此屬於空間回收機制,不能作為正確性保證。

---

### 同一個原因也決定了傳輸模式

MCP 的 Streamable HTTP 預設在記憶體中保存 session 狀態,並以 `Mcp-Session-Id`
關聯。在同樣沒有 session affinity 的前提下,client 的下一個請求可能打到從未
見過該 session 的容器,連線會以間歇性、難以重現的方式失敗。

因此採用 `stateless_http=True`,讓每個請求自我完備。代價是無法主動推送通知
—— 本服務沒有這個需求。

> 附帶記錄:SDK 的 DNS rebinding 防護預設僅允許 `127.0.0.1`,部署後會讓所有
> 請求得到 421。本專案從 `PUBLIC_BASE_URL` 推導允許的 host,而非關閉防護。

## 5. 檔案內容的處理

**圖片不在伺服器端做任何辨識。** 下載原始位元組,以 base64 包成 MCP image
content block 回傳,由 Claude 自身的多模態能力判讀 —— 實測可讀出圖中文字並
回答提問。相關程式碼只有三行(型別檢查與位元組傳遞)。

自行實作 OCR 會引入額外依賴、增加延遲,且品質必然不如呼叫端原生的能力。
代價是 payload 較大:base64 膨脹 4/3,因此上限從 Claude image API 的限制
往回推算(見[已知限制](#已知限制))。

**PDF 用 `pypdf` 抽取文字並附頁碼標記。** 關鍵分支是掃描檔:沒有文字層時
`extract_text()` 回傳空字串而**不拋出例外**,直接回傳會讓使用者以為「檔案是
空的」,而事實是「需要 OCR」。因此明確偵測並回報,加密的 PDF 另有訊息,
單頁解析失敗不影響其餘頁面。

OCR 經評估後刻意不實作 —— 超出作業範圍,且會顯著增加依賴與延遲。

## 關於存取範圍

`drive.readonly` 是能完成本任務的最小 Google scope,但它仍授予
**整個 Drive** 的讀取權 —— Google 未提供 per-folder 的 scope。

本專案依作業範圍實作:`list_files` 只列出指定資料夾;`read_file` 讀取
呼叫端指定的檔案 ID。

**曾評估在伺服器端強制驗證檔案是否為指定資料夾的後代**,實作並測試後
決定移除。理由是這個落差在本情境下不構成實際風險:每位使用者的 token
只能存取**他自己的** Drive,不存在跨使用者的資料外洩。

若要部署到多人共用或處理敏感資料的情境,這個檢查值得加回 —— 它能阻斷
一類經由檔案內容注入指令來存取範圍外檔案的路徑(因## 關於存取範圍

`drive.readonly` 是能完成本任務的最小 Google scope,但它仍授予**整個 Drive**
的讀取權 —— Google 未提供 per-folder 的 scope。

**OAuth scope 和資料夾授權是兩件事。** scope 只回答「這組憑證可以讀 Drive」,
不回答「這組憑證只能讀指定資料夾」。中間的落差必須由伺服器自己補。

因此 `read_file` 在**抓取任何內容之前**呼叫 `assert_within_folder`,
沿 `parents` 往上驗證目標檔案確實是指定資料夾的後代。那是一個圖走訪而非
單線上溯,因為 Drive 允許一個檔案同時位於多個資料夾;走訪帶有 `seen` 集合
與深度上限,避免異常的 parent 結構造成無限迴圈。

實測:資料夾內的檔案通過,帳號中其他 4 個檔案被拒絕。

### 為什麼這個檢查值得存在

這裡不存在跨使用者的資料外洩 —— 每位使用者的 token 只能存取他自己的 Drive。
真正要防的是**工具的實際能力大於使用者授權時的預期**,具體有兩點:

- 使用者的心智模型是「我把這個資料夾分享給這個工具」
- `read_file` 的參數由**模型填入**,而模型的輸入包含它剛讀到的檔案內容 ——
  檔案內容因此可以注入指令,要求存取範圍外的檔案

第二點是最實際的理由。

開發過程中曾一度移除這個檢查(理由是前述「不存在跨使用者外洩」),
後來評估注入路徑後加回。測試不只涵蓋檢查本身,也涵蓋**它有沒有被呼叫**、
以及**是否在下載之前呼叫** —— 移除呼叫但保留函式的版本,原本能通過整套測試。

## 已知限制

**規模與範圍**

- 圖片 3.5MB 上限 —— 從 Claude image API 約 5MB 的 base64 限制往回推算
  (base64 膨脹 4/3,3.5MB 原始檔約產生 4.7MB)
- 其他檔案 5MB 上限;抽出的文字超過 10 萬字元會截斷並標示
- 不執行 OCR
- 已註冊的 MCP client 不會自動清理(正式環境應加上 TTL)
- Cloud Run 的 service account 使用 `roles/editor`,應收斂為 `roles/datastore.user`

**技術債**

- `googleapiclient` 為同步函式庫,Drive 呼叫透過 `asyncio.to_thread` 執行。
  OAuth 路徑則使用原生非同步的 `httpx`,不受影響。
- `id_token` 驗證會同步取得 Google 公鑰(有快取,僅影響行程首次呼叫)
- `/token` 未支援 `refresh_token` grant。簽發的 token 效期 24 小時,
  到期後重新授權即可 —— 真正的憑證壽命由伺服器保管的 Google refresh token
  決定,並在背景自動輪替。
- callback 中「存憑證」與「簽發授權碼」是兩次獨立寫入,未包在 batch 內。
  現行順序(先存憑證)使失敗模式是安全的:憑證已存但授權碼未發時,
  使用者重新授權即可,不會有無效的授權碼流出。

**若有更多時間**

- 接上 Cloud Build trigger 並於部署前執行測試(目前部署是手動的,
  沒有任何機制阻止部署一個測試失敗的版本)

- 補上自動化測試(目前的驗證以手動端到端為主)
- 快取資料夾列表以減少 Drive API 呼叫
