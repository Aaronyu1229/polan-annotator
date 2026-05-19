# 標註員詳細頁（annotator detail view）— 設計

- 日期：2026-05-19
- 分支：`feat/annotator-detail-view`
- 狀態：設計已獲使用者口頭核可，待 spec 書面確認

## 問題與目標

Dashboard 的「各標註員進度」區塊（`dashboard.js` `loadProgressForAll()`，render 進
`#progress-list`）已能看到每位標註員的完成進度條，但**名字不能點進去**，看不到「ta
到底標了哪些檔案、每筆標了什麼值」。

需求來源：Amber 想直接檢視每位標註員的標註明細與整體品質。

**目標：** 從 dashboard 進度列點標註員名字 → 進入該標註員詳細頁，看到 ta 的所有完成
標註（可排序）、點任一筆就地展開完整標註結果，頁面上方顯示彙整統計（含與 Amber 的
校準對齊摘要）。

**用途定位（使用者確認）：** 內部品質檢視，不是對外報表。沿用既有 admin 頁慣例，
不引入新框架／套件。

## 範圍

### 會動的檔案

- `src/routes/stats.py` — 新增 `GET /api/stats/annotator/{annotator_id}/detail`
- `src/annotation_serialization.py` — **新檔**，`annotation_to_dict(ann) -> dict`
  （~30 行，可單測），只給新端點用
- `src/main.py` — 新增頁面路由 `GET /annotator/{annotator_id}` → serve HTML
- `static/annotator-detail.html` — **新檔**
- `static/annotator-detail.js` — **新檔**
- `static/dashboard.js` — `loadProgressForAll()` 把名字依權限 render 成連結
- `tests/` — 新增 API／頁面路由／序列化單測

### 明確不動

- 既有 3 處重複的 annotation 序列化（`routes/audio._annotation_to_dict`、
  `calibration._annotation_to_dict`、`reconcile_detail` inline）**不重構**
  — 守 CLAUDE.md 外科原則。本 spec 僅記錄此 duplication smell，不順手改。
- `compute_progress()`、`build_calibration_report()` 邏輯不改，只 compose 重用。
- `dashboard.html`（進度列由 JS 生成，HTML 不需改）。
- 任何 DB schema / migration（所需欄位皆已存在）。
- 不新增前端套件，維持 vanilla + 無框架 + Tailwind CDN。

## 設計

### 1. 後端 API — `GET /api/stats/annotator/{annotator_id}/detail`

放 `src/routes/stats.py`（與 `/progress` 同 router，語意相近）。

**Query 參數：** `tz`（IANA 時區，傳給 `compute_progress` 算連續天數，沿用 `/progress`）。

**權限 gate（admin-or-self）：** 直接複用 `/progress` 既有寫法：

```python
target = annotator_id.strip()
if target != user["annotator_id"] and not user.get("is_admin"):
    raise HTTPException(status_code=403, detail="僅 admin 或本人可檢視此標註員明細")
```

**未知 annotator 處理：** 不 404，比照 `/progress`（`compute_progress` 對無資料者
回 `has_data=False`），`files` 回空陣列。

**回傳結構：**

```jsonc
{
  "annotator_id": "yyslin1024",
  "annotator_name": "yyslin1024",          // annotators_loader 取 name，無則 = id
  "progress": { /* ProgressStats.to_dict()：completed_count / completion_rate /
                   avg_duration_sec / total_audio_files / current_streak_days /
                   estimated_remaining_sec / has_data */ },
  "calibration": null,                       // amber(is_reference) 或無重疊 → null
  // 否則：
  // "calibration": {
  //   "total_overlap": 25,
  //   "reference_total": 211,
  //   "overall_mae": 0.182,                 // 各 dim mae(非 null)的算術平均
  //   "worst_dim": "emotional_warmth",
  //   "worst_mae": 0.41,
  //   "report_url": "/calibration/report?annotator=yyslin1024"
  // }
  "files": [
    {
      "audio_id": "...", "filename": "...", "game_name": "...",
      "game_stage": "...", "duration_sec": 9.2,
      "created_at": "2026-05-12T03:11:00+00:00",
      "updated_at": "2026-05-12T03:14:00+00:00",
      "valence": 0.7, "arousal": 0.5, "emotional_warmth": 0.6,
      "tension_direction": 0.4, "temporal_position": 0.5,
      "event_significance": 0.3, "world_immersion": 0.55,
      "tonal_noise_ratio": 0.8, "spectral_density": 0.6,
      "loop_capability": [1.0], "source_type": ["..."],
      "function_roles": ["..."], "genre_tag": ["..."],
      "worldview_tag": "...", "style_tag": ["..."], "notes": "..."
    }
  ]
}
```

- `files` 只收 `is_complete == True`（與 progress / overlap / calibration 全站一致）。
- 後端回傳的 `files` 已按 `updated_at` desc 排好（前端仍可重排，後端給穩定預設）。
- `calibration` 聚合邏輯：呼叫 `build_calibration_report(session, annotator_id)`：
  - `is_reference == True`（annotator 是 amber）→ `calibration = null`
  - `total_overlap == 0` → `calibration = null`
  - 否則 `overall_mae` = 所有 `dimensions[dim].mae` 非 `None` 值的算術平均
    （全 None → `overall_mae = null`），`worst_dim`/`worst_mae` 取 mae 最大者。

**序列化：** 新增 `src/annotation_serialization.py`：

```python
def annotation_to_dict(ann: Annotation) -> dict: ...
```

回傳上述 `files[]` 內單筆的標註欄位部分（JSON 字串欄位 `loop_capability` /
`source_type` / `function_roles` / `genre_tag` / `style_tag` decode 成 list，
decode 失敗回 `[]`）。audio metadata 由端點 join `AudioFile` 後合併。為避免 N+1，
一次 `select(Annotation, AudioFile).join(...)` 撈齊。

### 2. 頁面路由 — `GET /annotator/{annotator_id}`

`src/main.py` 加：

```python
@app.get("/annotator/{annotator_id}", include_in_schema=False)
def annotator_detail_page(annotator_id: str) -> FileResponse:  # noqa: ARG001 — JS 從 path 取
    return FileResponse(STATIC_DIR / "annotator-detail.html")
```

比照 `/calibration/report` 頁本身不 gate（純 `FileResponse`），真正權限由
`/api/stats/annotator/{id}/detail` 把關。**不放 `/admin/` 前綴**，因為這頁是
admin-or-self，不是 admin-only，沒有「不洩露 admin route」的考量。

### 3. 前端 — `static/annotator-detail.html` + `annotator-detail.js`

慣例：vanilla + Tailwind CDN、2-space indent、無分號、ES module、`escapeHtml`
helper 比照 `reconcile-list.js` / `dashboard.js`。`<script type="module"
src="/static/auth.js">` 比照其他頁掛上。

**版面（由上到下）：**

1. **Header**：`← Dashboard` 連結 + 標題「標註員明細 — {name}」
2. **統計卡列**（grid）：
   - 總完成筆數（`completed_count` / `total_audio_files`，附完成率 %）
   - 平均單筆耗時（`avg_duration_sec` → `mm:ss`，`null` → 「—」）
   - 連續標註天數（`current_streak_days`，`null` → 「—」）
   - **校準摘要卡**：
     - `calibration != null`：overall MAE + 最差維度（label）+ 重疊筆數 +
       「看完整報告 ↗」連結（`report_url`，`target="_blank"`）
     - `calibration == null` 且該人 = amber：顯示「此為 reference 標註員，無校準比對」
     - `calibration == null` 且非 amber（無重疊）：顯示「與 Amber 無重疊檔案，無法比對」
3. **控制列**：排序選單 — `標註時間 ↓`（預設）／`檔名 A→Z`。純前端對已載入
   `files` 重排，不重打 API。
4. **檔案表格**：欄位 `檔名` | `遊戲・段落` | `標註時間`。點任一列 → **inline
   展開**該列下方面板（再點收合；同時只展開一筆）：
   - 10 維：label（走現有 `GET /api/dimensions`，比照 `reconcile.js` 取 label）
     + 值；對 4 個 `amber_confirmed:false` 維度顯示 ⚠️。
     `loop_capability` 為多選，顯示選中的值集合。
   - 音源類型（`source_type`）、功能角色（`function_roles`）：list → chip 列。
   - `genre_tag` / `worldview_tag` / `style_tag` / `notes`。
5. **錯誤狀態**：API 403 → 整頁顯示「無權限檢視此標註員」（不空白）；其他錯誤顯示
   具體訊息（CLAUDE.md：error 要具體）。

**無動畫**（CLAUDE.md：標註員長時間操作，展開／排序不加 transition）。

### 4. Dashboard 進度列改動 — `static/dashboard.js`

`loadProgressForAll()` 目前把 annotator 名字 render 成純文字 `<div>`。改為：

- `loadAll()` 先 fetch `/api/me` 取 `{ is_admin, annotator_id }`（dashboard.js
  已有 `showAdminLinks()` 在打 `/api/me`，可共用一次結果，避免重複請求）。
- 每列名字：當 `is_admin || name === me.annotator_id` →
  `<a href="/annotator/{encodeURIComponent(name)}" class="hover:underline">{name}</a>`；
  否則維持純文字。
- `dashboard.html` 不動（該列純 JS 生成）。

### 5. 測試（pytest，沿用 `tests/`）

- API：admin 看他人成功 / 本人看自己成功 / 非本人非 admin → 403 /
  amber → `calibration == null` 且 progress 正常 / 無重疊 → `calibration == null` /
  `files` 只含 is_complete / 後端預設 `updated_at` desc 排序正確 /
  JSON 欄位正確 decode 成 list。
- 頁面路由：`GET /annotator/{id}` 回 200 + HTML。
- `annotation_to_dict` 單測：含壞 JSON → `[]`、None 欄位處理。
- 覆蓋率沿用專案 `pytest --cov=src` 標準。前端**無測試框架**（與專案現況一致，
  本 spec 不引入 JS test infra）。

## 已知決策與待 Aaron 確認

1. **`files` 只收 `is_complete=True`** — 與全站（progress/overlap/calibration）一致。
   若 Amber 之後想看草稿（is_complete=False），另案處理，本次不做。
2. **Commit 訊息慣例落差**：CLAUDE.md 規定 `[Phase N] 描述`，但近 10 個 commit
   皆已改用 conventional（`feat:` / `fix:`），Phase 已收尾於 13。本 spec 一系列
   commit **沿用近期實際慣例 `feat:`**。CLAUDE.md 文件是否更新成現況，留待 Aaron 決定
   （與既有 memory 記錄之「commit 訊息慣例不一致」同一議題，非本次新增）。
3. **未知 annotator 不 404**，比照 `/progress` 回 `has_data:false` + 空 `files`。

## 不做（YAGNI）

- 不做每列 lazy fetch（≤300 筆一次回傳，payload 小）。
- 不做 drawer/modal 版（破壞既有「清單頁→詳細」慣例、無可分享 URL）。
- 不做標註員 CRUD（沿用 Phase 8 設計理由：Amber 改 JSON 即生效）。
- 不做匯出按鈕（已有 Phase 4 export pipeline，不在本頁重造）。
- 不重構既有重複序列化（外科原則）。
