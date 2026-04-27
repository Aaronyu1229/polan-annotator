# 珀瀾聲音標註工具 polan-annotator

珀瀾聲音 Pōlán Sound 內部音效情緒標註工具。給創辦人 Amber 自己標 33 個 BGM 種子作品用，標完後成為招募外部標註員的「黃金校準範例」與賣給 AI 新創的訓練資料集雛形。

> Phase 1：專案骨架 + 資料模型  ✅ 完成
> Phase 2：標註介面核心  ✅ 完成
> Phase 3：校準模式 + ICC + Dashboard  ✅ 完成
> Phase 4：資料匯出 + validation  ✅ 完成
> Phase 5：一鍵啟動 / 進度儀表板 / 維度反饋 / 多選改造  ✅ 完成

---

## 環境需求

- Python 3.11+（測試於 3.12）
- macOS 或 Linux（Windows 未驗證）

## 安裝

### 用 uv（推薦，更快）

```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"
```

### 用 pip + venv

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## 啟動

```bash
source .venv/bin/activate
uvicorn src.main:app --reload --port 8000
```

開啟 <http://localhost:8000>，可看到音檔清單。

啟動 lifespan 會：
1. 建表（idempotent）
2. 掃描 `data/audio/` 並 upsert 新 `.wav` 到 DB
3. 把掃描摘要 log 出來

## 給 Amber 的一鍵啟動

做了一個雙擊即可啟動的腳本，免打指令。安裝方式（Aaron 做一次即可）：

```bash
bash scripts/install_desktop_shortcut.sh
```

跑完後桌面會出現「啟動珀瀾標註工具」檔案（實際是 symlink 指向 `scripts/start_annotator.command`），雙擊就會：

1. 終端機視窗跳出 + 顯示啟動訊息
2. 3 秒後瀏覽器自動打開 <http://localhost:8000/?annotator=amber>
3. 結束工作時 Ctrl+C 停 server、再關視窗

**第一次雙擊**會被 macOS 安全機制擋下，需到 `系統設定 → 隱私權與安全性` 最下方點「強制打開」。之後不會再擋。

**搬 project 位置後**（例如 iCloud Drive、不同硬碟）重跑一次 `install_desktop_shortcut.sh` 即可更新桌面 symlink。腳本自己會用 `python3 os.path.realpath` 解 symlink 定位真正 project dir，不會因為路徑不同壞掉。

## 手動 rescan

新增音檔後不想重啟 server：

```bash
python scripts/rescan_audio.py
```

## 測試

```bash
pytest                         # 全部
pytest tests/test_smoke.py -v  # 只跑 smoke
```

目前 50 tests，分布：
- `test_filename_parser.py` — 7（兩段式 / 三段式品牌主題 / fallback）
- `test_dimensions_loader.py` — 14（happy path + fail-fast）
- `test_audio_scanner.py` — 7（idempotent + parse 整合）
- `test_smoke.py` — 7（API + 靜態頁路由）
- `test_annotations_api.py` — 12（完整度驗證 / upsert / next_audio_id / annotators 列表）
- `test_audio_analysis.py` — 3（librosa 分析 + cache，需 data/audio 有檔）

## 目錄結構

```
polan-annotator/
├── src/
│   ├── main.py                 # FastAPI app + lifespan
│   ├── db.py                   # SQLite engine + session
│   ├── models.py               # SQLModel: AudioFile / Annotation / TagSuggestion
│   ├── constants.py            # SOURCE_TYPES, FUNCTION_ROLES, parse_audio_filename
│   ├── dimensions_config.json  # 10 維度的單一資料來源（Amber 可直接編輯）
│   ├── dimensions_loader.py    # JSON loader + 驗證
│   ├── audio_scanner.py        # data/audio/ 掃描器
│   ├── audio_analysis.py       # librosa 分析 + cache 到 AudioFile
│   └── routes/
│       ├── dimensions.py       # GET /api/dimensions
│       ├── audio.py            # GET /api/audio (列表 / 單筆 / stream)
│       ├── annotations.py      # POST /api/annotations (upsert) + /annotators
│       └── tag_suggestions.py  # GET /api/tag-suggestions?field=
├── static/
│   ├── index.html              # 音檔清單頁（進度 / ✓✗）
│   ├── annotate.html           # Phase 2 標註主介面
│   ├── annotate.js             # 標註頁邏輯（滑桿 / 波形 / draft）
│   └── list.js                 # 清單 fetch + render
├── scripts/
│   └── rescan_audio.py         # CLI：手動重掃
├── tests/                      # pytest
├── data/
│   ├── audio/                  # 33 個 .wav 種子
│   └── annotations.db          # SQLite（gitignored）
└── pyproject.toml
```

## 維度定義 — `src/dimensions_config.json`

10 個維度的所有定義文字、錨定範例、auto-compute 設定都在這個 JSON。**Amber 試標發現定義需要調整時，只需要改這個 JSON 檔、重啟 server 就生效**，不用改 Python code。

目前 4 個維度標記為 `amber_confirmed: false`：
- `emotional_warmth`
- `tension_direction`
- `event_significance`
- `world_immersion`

這些是 Aaron 依設計脈絡推敲的，每個都附 `todo_amber` 註記等 Amber 試標時驗收。**Phase 2 UI 必須在這些維度顯示 ⚠️ icon**。

## 音檔命名規則

Parser 在 `src/constants.py` 的 `parse_audio_filename()`，處理三種：

1. **兩段式**：`{Game Name}_{Stage}.wav` — 例：`Volcano Goddess_Base Game.wav`
2. **三段式品牌主題曲**：`Game Brand Theme Music_{品牌}_AI Virtual Voice.wav` — 例：`Game Brand Theme Music_金鑫_AI Virtual Voice.wav`
3. **Fallback**：不符合上面兩種時 split 第一個底線（game_name 上加 TODO 註記）

合法 stage 集合在 `KNOWN_STAGES`：`Base Game / Free Game / Bonus Game / Main Game / Winning Panel`。

## POST `/api/annotations` validation 層級

後端驗證分為**硬性錯誤（400）**和**軟性不完整（接受但 `is_complete=False`）**兩層：

| 情境 | 回應 | `is_complete` |
|------|------|---------------|
| 任一維度值超出 `[0, 1]`（例如 `valence=1.5`） | **400** — `"維度 valence 值 1.5 超出範圍 [0, 1]"` | — |
| `source_type` 不在 `SOURCE_TYPES` 白名單 | **400** — `"source_type 'xxx' 不在合法清單"` | — |
| `function_roles=[]`（空陣列） | **400** — `"function_roles 必須至少選一項"` | — |
| `function_roles` 含不合法 key | **400** — `"function_roles 包含非法值：xxx"` | — |
| 任一維度值為 `null` / `source_type=null` | **200** — 儲存並回 `next_audio_id` | **False** |
| 上述全部通過 + 10 維度齊全 + `source_type` 已填 + `function_roles` ≥ 1 | **200** | **True** |

**設計意圖：**
- 硬性錯誤 = 資料髒（不可能是合理狀態）→ 前端要把錯訊顯給使用者，不要寫 DB
- 軟性不完整 = 使用者還沒標完，但想先存 → 允許，但 Phase 4 `export_dataset.py` 只撈 `is_complete=True`
- Draft（自動暫存）完全在前端 `localStorage`，**不會** POST 到後端
- 同一 `(audio_id, annotator_id)` 對重複 POST 會 upsert（保留 `created_at`、更新 `updated_at`）

## Phase 4 — 資料集匯出

Phase 4 負責把 DB 內 `is_complete=True` 的 annotation 轉成可交付 AI 買方的 JSON。

### 端點

| Endpoint | 用途 |
|---|---|
| `GET /api/export/dataset.json` | 主要交付物。多人標註時合併為共識值 |
| `GET /api/export/calibration_set.json` | 只含 amber 的標註（校準新標註員） |
| `GET /api/export/individual.json?annotator=<id>` | 特定標註員全部標註；未知 or 無完成標註 → 404 |

所有端點一次回整份 JSON，無分頁（MVP dataset < 1000 筆）。

### Aggregation 規則

只從 DB 取 `is_complete=1` 的 annotation。若一檔所有 annotation 都 incomplete，整檔從 `items` 排除，但仍計入 `total_audio_files`。

| 欄位類型 | 規則 |
|---|---|
| 9 個連續維度 | mean，round 到 **3 位小數**（滑桿 step=0.05，更多位是假精確；浮點 `0.700` 在 JSON 顯示為 `0.7` 是 Python float 正常行為）|
| `loop_capability`（離散 0/0.5/1） | mode；三方各一票 fallback **0.5** |
| `source_type`（單選 enum） | mode；平手 → `null` + `warnings: ["source_type_conflict"]` |
| `function_roles` / `style_tag`（多選）| **union**，dedupe 保留首現順序 |
| `genre_tag` / `worldview_tag`（單選）| mode；平手 → `null`（不加 warning）|
| `notes` | 不合併，只留在 `individual_annotations[].notes` |

### `consensus_method` 的語意

- `"single_annotator"`：只有 1 位標註員，consensus 就是該人的值
- `"mixed"`：≥ 2 位標註員，混合用上述規則（**不**叫 `"mean"`，因為 loop_capability 是 mode、多選是 union — `mixed` 比較誠實）

### `annotated_at` 的語意

值來自 `Annotation.updated_at`，代表「最近一次確認這筆標註的時間」。若 Amber 之後重新調整舊檔的標記，買方看到的是最新時間戳。

### `schema_version` 政策

當前 `"0.1.0"`。0.x 代表 MVP 試水溫；任何 breaking change（欄位改名、刪除、enum 變動）→ bump major。買方應該 pin major version。

### Schema 驗證

```bash
uv run python scripts/validate_export.py /path/to/dataset.json
# exit 0 = valid，exit 1 = 有錯誤（逐條列在 stdout）
```

Validator 刻意**獨立於 FastAPI / SQLModel**，enum 硬編碼在檔案內。買方拿到 JSON 後能自己跑這支 script 驗，不需要我們的 repo。若 `src/constants.py` 的 enum 變動，必須同步更新 `scripts/validate_export.py` 的 `EXPECTED_*` 常數。

### 預熱 librosa cache

`audio_metadata` 的 `duration_sec` / `bpm` / `sample_rate` / `auto_computed.*` 是 librosa 算出來的，原本只在使用者開啟標註頁時 lazy 計算。匯出前跑一次這個 script 讓 33 個檔案都有 metadata：

```bash
uv run python scripts/warm_audio_cache.py
```

預計 20 秒~幾分鐘（依 CPU）。單檔失敗不會中斷整批，結尾印 summary。

### 完整驗證流程（copy-paste）

```bash
# 1. warm cache（可選，讓 audio_metadata 齊全）
uv run python scripts/warm_audio_cache.py

# 2. 啟 server
uv run uvicorn src.main:app --reload &

# 3. export + validate
curl -s http://localhost:8000/api/export/dataset.json > /tmp/dataset.json
uv run python scripts/validate_export.py /tmp/dataset.json

# 4. 跑測試
uv run pytest tests/test_export.py -v
```

## Phase 3 — 校準模式 + ICC + Dashboard

Phase 3 在 Phase 1+2+4 之上加「品質保證層」：第二位標註員加入時能用 amber 已標的檔案
做訓練、立即看到分數比對；Aaron 端有 dashboard 看跨人 ICC 紅綠燈。

### 端點

| Endpoint | 用途 |
|---|---|
| `GET /api/calibration/queue?annotator=` | reference (amber) 已標、self 未標的 audio 清單 |
| `GET /api/calibration/reference/{audio_id}` | amber 對該 audio 的 is_complete annotation |
| `GET /api/stats/icc?include_fixture=` | 跨標註員 ICC(2,1) per dimension |
| `GET /api/stats/overlap?include_fixture=` | 被 ≥ 2 位標註者標過的 audio 清單 |
| `GET /calibration` | 校準首頁 HTML |
| `GET /calibration/{audio_id}` | 校準標註頁（重用 annotate.html） |
| `GET /calibration/compare/{audio_id}` | 比對結果頁（雷達圖 + 差距表） |
| `GET /dashboard` | Dashboard HTML |

### ICC 演算法

`src/statistics.py` 的 `icc_2_1()` — Two-way random effects, single rater, absolute agreement
（Shrout & Fleiss 1979）。純 numpy 實作，不用 scipy ANOVA helper。

**只算連續維度。** `loop_capability` 是 `multi_discrete`（list[float]），不在 ICC 計算內 —
列在 dashboard 的 `skipped_dimensions` 區塊。未來如要量化 multi_discrete 一致性，建議用
per-option Cohen's Kappa（不在 Phase 3 範圍）。

**設計：intersection design** — 只計算「全部 K 個 annotator 都 is_complete-標過」的 audio
子集。若 K=2 且兩人重疊 N 個檔，dashboard 顯示「基於 N 筆共同標註的檔案」。

**門檻：**
- emotion + function 類維度：0.7（主觀，較寬鬆）
- acoustic 類維度：0.85（客觀，需嚴格一致）

### Calibration 流程

1. Bob 開 `/calibration?annotator=bob` → 看 amber 已 is_complete 標、bob 還沒標的清單
2. 點任一檔案進 `/calibration/{audio_id}?annotator=bob` — 介面跟標註頁相同，多一條紅色
   banner「校準模式 — 提交後會顯示跟 amber 的分數比對」
3. 標完按「提交並比對」→ 跳到 `/calibration/compare/{audio_id}` 顯示雷達圖（Bob vs Amber）+ MAE + 維度差距表（按 abs 差距由大到小排序）
4. 點「繼續下一個」自動跳下一個未校準音檔

### Dashboard 預覽工具

DB 還沒有第二位真實標註員時，dashboard 會顯示「尚無跨標註員資料」。要 preview UI 可跑：

```bash
uv run python scripts/seed_fixture.py            # 寫 fixture_bob + fixture_alice
uv run python scripts/seed_fixture.py --remove   # 清除
```

Fixture annotator 永遠以 `fixture_` 前綴；dashboard 預設**排除**它們，要看必須勾右上
「含 fixture（preview 用）」checkbox。

### Phase 3 測試

- `tests/test_statistics.py` — 8 cases（perfect / no agreement / 邊界 / 零變異）
- `tests/test_stats_icc.py` — 12 cases（intersection / fixture filter / threshold / multi_discrete skip）
- `tests/test_calibration_api.py` — 7 cases（queue 邏輯 / reference 404）

跑 `pytest -q` 應 111+ 全綠。
