# 珀瀾聲音標註工具 polan-annotator

珀瀾聲音 Pōlán Sound 內部音效情緒標註工具。給創辦人 Amber 自己標 33 個 BGM 種子作品用，標完後成為招募外部標註員的「黃金校準範例」與賣給 AI 新創的訓練資料集雛形。

> Phase 1：專案骨架 + 資料模型  ✅ 完成
> Phase 2：標註介面核心  ✅ 完成
> Phase 3：校準模式 + ICC（第一輪不做）
> Phase 4：資料匯出 + validation

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

## 後續 Phase

Phase 3 預期加：
- 第二位標註員加入後的 ICC / consensus aggregation
- 標註員之間的歧見熱點 UI

Phase 4 預期加：
- `scripts/export_dataset.py` — 匯出 `is_complete=True` 的 annotations
- training set / validation split 邏輯
