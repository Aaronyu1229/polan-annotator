# Alignment 客戶隔離設計

**日期：** 2026-06-18
**狀態：** 已核可，待寫實作計畫

## 問題

要把 alignment（聲音對齊標註）的 ref 音檔丟給外部客戶標註，但需要：

1. **權限不同** — 客戶用同一個入口（`annotate.dolcenforte.com`），但內部工具（dashboard / upload / admin / 資料集 export / stats / calibration）客戶看不到、進不去。
2. **音檔分倉** — 客戶標註時碰不到版權非我方的內部音檔；ref 音檔要搬出主庫，跟內部音檔實體分開。

## 現況（已查證）

- **資料層已分開**：客戶標的值寫在獨立 `data/alignment.db`（`alignment_reading` / `alignment_spec` 表），永不進 `/api/export/*` 資料集 pipeline。
- **音檔共用**：alignment 的 ref 音檔走 `/api/audio/{id}/stream`，查 `annotations.db` 的 `AudioFile` 表、讀磁碟 `data/audio/`。與內部標註音檔同一張表、同一個目錄。
- **存取全靠 Cloudflare Access**：整站在 CF Access OTP 白名單後面。**關鍵發現** — 大多數內部路由（含 `/api/export/dataset.json` 資料集本體、`/dashboard`、`/upload`、`/api/stats/*`、`/calibration`）**沒有 app 層 auth**，唯一的鎖就是 CF Access。只有 `/admin/*` 和少數 API 有 `Depends(require_auth)`。

### 既有 auth 機制（`src/middleware.py`）
`require_auth` 回傳統一 user dict：`{annotator_id, email, is_admin, name}`。三模式優先序：CF Access JWT/header → session OAuth → dev query string。權限模型是 binary（authenticated/not）+ `is_admin` 旗標，無角色階層。

## 核心洞察

讓 bypass 的表面積 = alignment 命名空間，而那個命名空間實體上只摸得到 alignment 的 DB 和音檔目錄。

- 對 `/alignment` + `/api/alignment/*` 開 CF Access **Bypass**，改用 app 層 token 把關。
- 其他路徑**一律不 bypass** → 內部全部維持在 CF Access OTP 後面，客戶永遠進不去。
- 幫 alignment 開**自己的** streaming 端點（只讀 `data/alignment_audio/`），「音檔分倉」與「只 bypass alignment 命名空間」變成同一個動作。

## 設計

### A. 存取隔離：token gate + CF bypass

**新表 `ClientLink`（alignment.db）**
| 欄位 | 說明 |
|---|---|
| `id` | uuid 主鍵 |
| `token_hash` | token 的 SHA-256（**不存明文**） |
| `session_id` | 綁定的 alignment session |
| `alignment_audio_id` | 綁定的 ref 音檔（FK → AlignmentAudio.id） |
| `label` | 客戶名/批次標籤 |
| `created_at` | |
| `expires_at` | nullable，到期失效 |
| `revoked` | bool，可手動撤銷 |

**新依賴 `require_client_link`**（新檔 `src/client_auth.py`）
- 掛在 `GET /alignment` 和**全部** `/api/alignment/*`。
- token 來源：URL `?token=xxx`（首次）→ 後端驗證後種 cookie → 後續 API/音檔請求自動帶 cookie（含 `<audio src>` 請求）。
- cookie：`HttpOnly` + `Secure` + `SameSite=Lax`。
- 驗證：SHA-256 後查 `ClientLink`，檢查未 `revoked`、未過 `expires_at`，**constant-time 比對**。
- 回傳該 `ClientLink`（含 `session_id` + `alignment_audio_id`）。
- **強制注入 session_id**：所有 alignment API handler 忽略客戶傳入的 `session_id` query，改用 token 綁定的值 → 一條 token = 一個 session，客戶之間互相看不到資料。

**token 規格**：≥32 bytes urlsafe 隨機；明文只在發佈當下回傳一次，之後只存 hash。

**Cloudflare Access（基礎設施步驟，非程式碼）**
- 對 `/alignment` 和 `/api/alignment/*` 加一條 **Bypass** policy。
- 內部路徑（`/dashboard`、`/upload`、`/admin/*`、`/api/export/*`、`/api/stats/*`、`/calibration`、`/api/audio/*` 等）**一律不在 bypass 清單**，維持 OTP 白名單保護。
- 確切設定值另寫操作文件，由專案擁有者在 CF 後台套用。

### B. 音檔分倉

- **新目錄** `data/alignment_audio/`。
- **新表 `AlignmentAudio`（alignment.db）**：`id`(uuid) / `filename` / `orig_audio_id`(留出處，nullable) / `created_at`。
- **新端點 `GET /api/alignment/audio/{alignment_audio_id}/stream`**：
  - 只讀 `data/alignment_audio/`，以 `AlignmentAudio.filename` 解析磁碟路徑（沿用既有「不暴露目錄、經 id 查 filename」模式）。
  - 掛 `require_client_link`，且驗證該 `alignment_audio_id` == token 綁定的那支（客戶連別的 session 音檔都串不到）。
- 客戶端**完全碰不到** `/api/audio/{id}/stream`（內部端點，仍在 CF Access 後）。

### C. 前端（`static/alignment.js`）

- 不再從 URL 讀 `audio_id`。
- 改打新的 **`GET /api/alignment/context`**（token-gated）拿回 `{session_id, alignment_audio_id}`。
- player src 指向 `/api/alignment/audio/{alignment_audio_id}/stream`。
- 客戶 URL 只剩 `…/alignment?token=xxx`。

### D. 發佈動作（Dashboard 按鈕）

- **`POST /api/admin/alignment/publish`**（`Depends(require_auth)` + admin 檢查）
  - body：`{audio_id, label, expires_at?}`。
  - 動作：複製 `data/audio/{filename}` → `data/alignment_audio/{filename}`；建 `AlignmentAudio`；產生新 `session_id`；產生高熵 token、存 hash、建 `ClientLink`。
  - 回傳：一次性 `{client_url, token}`（明文 token 只此一次）。
- **`GET /api/admin/alignment/links`**：列出所有 link（含 label / 狀態 / 到期），供管理。
- **`POST /api/admin/alignment/links/{id}/revoke`**：撤銷。
- **Dashboard UI**：音檔列旁加「發佈給客戶」按鈕 → 呼叫 publish → 顯示可複製連結；另一處列出/撤銷既有連結。

## 動到的檔案

| 檔案 | 變更 |
|---|---|
| `src/alignment_db.py` | 新增 `AlignmentAudio`、`ClientLink` 兩表 |
| `src/client_auth.py`（新） | `require_client_link` 依賴 + token 雜湊/驗證工具 |
| `src/routes/alignment.py` | 掛 gate、強制注入 session_id、新增 `/context` 與 `/audio/{id}/stream` |
| `src/routes/admin.py` | `publish` / `links` / `revoke` 端點 |
| `static/alignment.js` | 改用 `/context` + 新 stream 端點 |
| dashboard 前端 | 「發佈給客戶」按鈕 + 連結列表/撤銷 |
| `docs/`（新操作文件） | Cloudflare Access Bypass policy 確切設定 |

## 安全考量

- 開 CF bypass 後 alignment 表面為公開網路可達，**唯一的鎖是 app token** → token gate 必須高熵 + 雜湊存 + constant-time 比對 + 可撤銷 + 可到期（已含）。
- publish / links / revoke 端點要求 admin（縱深防禦，即使已在 CF Access 後）。
- 內部資料集裸端點因**完全不在 bypass 清單**，照舊受 CF Access 保護，不受本變更影響。
- token 明文不落地、不入 log；發佈當下回傳一次。

## YAGNI（刻意排除）

- 不碰內部任何路由現有的 auth（維持現狀）。
- 不做客戶自助登入 / 帳號系統。
- 一條 link 對一支 ref 音檔（多支留待之後）。

## 成功標準

1. 帶 valid token 的 `…/alignment?token=xxx` 能進頁、能串到該 session 的 ref 音檔、能存讀標註。
2. 同一個瀏覽器（帶 alignment cookie）直打 `/dashboard`、`/api/export/dataset.json` → 被 CF Access OTP 擋下（客戶不在白名單）。
3. 客戶改 `session_id` query 或 `alignment_audio_id` → 只能存取自己 token 綁定的那一份，碰不到別人的。
4. `/api/alignment/audio/{id}/stream` 解析的磁碟路徑只在 `data/alignment_audio/` 內，永不指向 `data/audio/`。
5. revoke 後該 token 立即失效。
