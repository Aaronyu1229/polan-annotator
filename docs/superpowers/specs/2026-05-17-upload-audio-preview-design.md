# 音源管理「已上傳音檔」清單內嵌試聽 — 設計

- 日期：2026-05-17
- 分支：`feat/upload-audio-preview`
- 狀態：設計已獲使用者口頭核可，待 spec 書面確認

## 問題與目標

`/upload`（「音源管理 — 上傳新檔」，admin 限定）頁面底部的「已上傳音檔」清單，每列只顯示
檔名、時長、遊戲資訊與「刪除」鈕，**沒有任何播放方式**。目前唯一能聽音檔的路徑是回首頁
→ 點清單列 → 進標註頁用 WaveSurfer 播放器，對「上傳後快速抽查檔案有沒有上錯／壞掉」
的情境太繞。

**目標：** 在「已上傳音檔」清單的每一列就地加「試聽」，讓 admin 上傳後能立即確認檔案正確。

**用途定位（使用者確認）：** 上傳後快速抽查，不是音檔庫瀏覽，不需要波形或進階控制。
因此範圍鎖定此頁此清單，不擴及首頁、不做導覽、不加任何套件。

## 範圍

### 會動的檔案
- `static/upload.js` — `renderExistingRow()` 加按鈕與播放槽；新增播放器掛載／拆除邏輯
- `static/upload.html` — `upload.js?v=20260501c` 版本字串改一碼（此頁有 cache-busting query，
  不改新 JS 不會被瀏覽器抓取）

### 明確不動
- 後端：不改。直接用既有 `GET /api/audio/{id}/stream`（已實測回正確 MIME + 完整位元組）
- `static/auth.js`、首頁（`index.html`/`list.js`）、標註頁、任何後端路由
- 不新增前端套件，維持本專案 vanilla + 無框架 + Tailwind CDN 的約束

## 設計

### 每列結構（restructure `renderExistingRow`）

`<li>` 由單行 flex 改為兩段式（直向）：

1. **上排**：檔名／時長／遊戲資訊／`✓` 維持原樣；在「刪除」鈕**前面**插入一顆琥珀色
   `▶ 試聽` 鈕（`data-action="preview" data-id="{a.id}"`，沿用頁面既有琥珀色按鈕樣式）
2. **下方**：一個播放槽容器 `data-player-slot="{a.id}"`，預設 `hidden`，點開才出現

### 行為：單一實例播放器

模組層維護單一狀態：

```
activePreview = { audioId, slotEl, btnEl } | null
```

**點 `▶ 試聽`：**
1. 先 `teardownPreview()`：停止並移除目前 active 的 `<audio>`、該列槽位清空並 `hidden`、
   該列按鈕文字還原為 `▶ 試聽` → **保證同時只有一個音檔在播**
2. 在本列槽位掛入：
   `<audio controls autoplay preload="auto" src="/api/audio/{encodeURIComponent(id)}/stream">`
3. 本列按鈕文字改為 `✕ 收起`，槽位解除 `hidden`
4. 更新 `activePreview`

**再點同一列的 `✕ 收起`：** 對該列呼叫 `teardownPreview()`（toggle 關閉）。

每列只有一顆按鈕，其文字／行為由狀態決定。點擊 handler 分支：
- 若 `activePreview?.audioId === a.id`（本列正在播）→ `teardownPreview()`（收起，等同 `✕ 收起`）
- 否則 → 先 `teardownPreview()` 再掛載本列（等同 `▶ 試聽`）

原生 `<audio controls>` 免費提供 播放／暫停／拖曳進度／時間／音量，足夠抽查
「是不是這段、有沒有壞」。

### 邊界與錯誤處理

- **清單重繪前必先拆播放器**：`refreshExistingList()` 會整段 `innerHTML` 重寫；若不先停，
  會留下 DOM 已被取代卻仍在播放的孤兒 `<audio>`。在 `list.innerHTML = ...` 之前呼叫
  `teardownPreview()`。刪除某列、上傳完刷新都會走 `refreshExistingList()`，一併涵蓋。
- **串流失敗**（404／網路／CF Access session 過期回非音訊內容）：`<audio>` 觸發 `error`
  事件 → 槽位顯示紅字「試聽失敗（檔案讀取錯誤）」，按鈕還原為 `▶ 試聽` 可重試。
  採內嵌訊息而非 `window.alert()`（較不打斷，與佇列列既有錯誤呈現方式一致）。
- **播放結束**（`ended`）：不自動拆除，保留播放器讓使用者可直接重播。

## 驗證

後端未改，無需新後端測試；`/api/audio/{id}/stream` 已由既有 Phase 6 測試覆蓋。
前端為 vanilla JS，本專案無 JS 測試框架（`tests/` 為 pytest），**不為此功能另架
JS 測試基建**（符合使用者「不堆基建、成果優先」原則）。

手動煙霧測試清單（admin 登入 `/upload`）：

1. 點某列 `▶ 試聽` → 出現播放器並出聲
2. 拖曳進度條可跳轉
3. 點另一列 `▶ 試聽` → 前一列自動停止並收起，只剩新列在播
4. 點 `✕ 收起` → 播放停止、槽位收起
5. 播放中對該列按「刪除」並確認 → 播放乾淨停止、清單刷新無孤兒音
6. 對檔案不存在／壞檔的列試聽 → 顯示內嵌「試聽失敗」紅字、按鈕可重試

可選（低成本回歸保護，不建 JS 基建）：一條 pytest 斷言 `GET /upload` 回 200，
且其載入的 `upload.js` 內容含 `data-action="preview"`，確保資產有接上。

## 不做（YAGNI）

- 波形視覺化（WaveSurfer）— 抽查不需要
- 每列常駐播放器 — 上千列 DOM 過重
- 首頁清單／標註流程的試聽 — 超出本次範圍
- 播放速度、音量記憶、鍵盤快捷 — 抽查不需要
