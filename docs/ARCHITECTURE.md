# 架構說明

技術選擇與取捨的紀錄。安裝與使用方式見 [README](../README.md)。

---

## 目錄

- [整體形狀](#整體形狀)
- [1. 為什麼是 Cloud Run](#1-為什麼是-cloud-run)
- [2. 為什麼是兩段 OAuth Handshake](#2-為什麼是兩段-oauth-handshake)
- [3. 為什麼實作 SDK 的 provider 介面](#3-為什麼實作-sdk-的-provider-介面)
- [4. 為什麼是 opaque token 而非 JWT](#4-為什麼是-opaque-token-而非-jwt)
- [5. 為什麼是 Firestore](#5-為什麼是-firestore)
- [6. 為什麼是 stateless 傳輸](#6-為什麼是-stateless-傳輸)
- [7. 圖片為什麼不做辨識](#7-圖片為什麼不做辨識)
- [8. PDF 的處理界線](#8-pdf-的處理界線)
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

## 3. 為什麼實作 SDK 的 provider 介面

MCP Python SDK(`mcp.server.auth`)已內建完整的 OAuth 2.1 授權伺服器:
discovery 文件、動態註冊、`/authorize`、`/token`、PKCE 驗證、bearer middleware。

原本規劃自行實作全部端點,發現 SDK 已涵蓋後改為實作
`OAuthAuthorizationServerProvider` 介面 —— SDK 負責**協定**,本專案負責**政策**
(使用者去哪裡登入、狀態存在哪裡、token 如何簽發)。

**取捨**:自行實作能完全掌握每一行,但會重複 SDK 已驗證過的邏輯,
且「為何不用官方實作」在審查時難以自圓其說。實作 provider 介面仍需理解
完整流程才能正確填入,因此並未犧牲對設計的掌握度。

SDK 的 PKCE 實作也處理了容易忽略的細節,例如 base64url 編碼**不帶補位**
(`.rstrip("=")`)—— 這是自行實作時常見的互通性問題。

---

## 4. 為什麼是 opaque token 而非 JWT

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

## 5. 為什麼是 Firestore

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

## 6. 為什麼是 stateless 傳輸

MCP 的 Streamable HTTP 預設為 stateful,在記憶體中保存 session 狀態並以
`Mcp-Session-Id` 關聯。在 Cloud Run 的多實例環境下,client 的下一個請求
可能打到從未見過該 session 的容器,連線會以間歇性、難以重現的方式失敗。

因此採用 `stateless_http=True`,讓每個請求自我完備。
代價是無法主動推送通知 —— 本服務沒有這個需求。

> 附帶記錄:SDK 的 DNS rebinding 防護預設僅允許 `127.0.0.1`,部署後會讓所有
> 請求得到 421。本專案從 `PUBLIC_BASE_URL` 推導允許的 host,而非關閉防護。

---

## 7. 圖片為什麼不做辨識

作業說明「即使只是回報無法解析也算合理處理」。本專案的做法是**下載原始
位元組,以 base64 包成 MCP image content block 回傳**,由 Claude 自身的
多模態能力判讀。

伺服器端**不執行任何 OCR 或影像辨識**。

實測結果:Claude 能讀出圖片中的文字內容(標題、案號、委託單位)並回答
相關提問。

這個選擇的價值在於**做得更少而不是更多** —— 相關程式碼只有三行(型別檢查
與位元組傳遞)。自行實作 OCR 會引入額外依賴、增加延遲,且品質必然不如
呼叫端原生的多模態能力。

Base64 編碼刻意放在 MCP 邊界(`tools.py`)而非抽取層,讓 extractor 保持
處理原始位元組,便於測試。

---

## 8. PDF 的處理界線

使用 `pypdf` 抽取文字,並以頁碼標記分隔,讓引用時能指出位置。

**關鍵分支是掃描檔的處理。** 沒有文字層的 PDF 會讓 `extract_text()` 回傳
空字串而**不拋出例外**。若直接回傳,使用者看到的是「這個檔案是空的」——
與事實(「這個檔案需要 OCR」)完全不同。

因此明確偵測並回報:

> This PDF has N page(s) but no text layer — it appears to be scanned images.
> Reading it would need OCR, which this server does not perform.

加密的 PDF 有獨立的訊息。單一頁面解析失敗會被容忍(通常是字型問題),
不讓一頁毀掉整份文件。

**OCR 經評估後刻意不實作** —— 超出作業範圍,且會顯著增加依賴與延遲。

---

## 關於存取範圍

`drive.readonly` 是能完成本任務的最小 Google scope,但它仍授予
**整個 Drive** 的讀取權 —— Google 未提供 per-folder 的 scope。

本專案依作業範圍實作:`list_files` 只列出指定資料夾;`read_file` 讀取
呼叫端指定的檔案 ID。

**曾評估在伺服器端強制驗證檔案是否為指定資料夾的後代**,實作並測試後
決定移除。理由是這個落差在本情境下不構成實際風險:每位使用者的 token
只能存取**他自己的** Drive,不存在跨使用者的資料外洩。

若要部署到多人共用或處理敏感資料的情境,這個檢查值得加回 —— 它能阻斷
一類經由檔案內容注入指令來存取範圍外檔案的路徑(因為 `read_file` 的參數
由模型填入,而模型的輸入包含它剛讀到的檔案內容)。相關實作保留在
commit 歷史中。

---

## 已知限制

**規模與範圍**

- 單一檔案 5MB 上限;文字超過 10 萬字元會截斷並標示
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

- 補上自動化測試(目前的驗證以手動端到端為主)
- 快取資料夾列表以減少 Drive API 呼叫
