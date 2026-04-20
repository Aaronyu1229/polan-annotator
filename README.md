# 珀瀾聲音標註工具 polan-annotator

珀瀾聲音 Pōlán Sound 內部音效情緒標註工具。給創辦人 Amber 自己標 33 個 BGM 種子作品用，標完後成為招募外部標註員的「黃金校準範例」與賣給 AI 新創的訓練資料集雛形。

> Phase 1：專案骨架 + 資料模型  ✅ 完成
> Phase 2：標註介面核心  🚧 待實作
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

目前 35 tests，分布：
- `test_filename_parser.py` — 7（兩段式 / 三段式品牌主題 / fallback）
- `test_dimensions_loader.py` — 14（happy path + fail-fast）
- `test_audio_scanner.py` — 7（idempotent + parse 整合）
- `test_smoke.py` — 7（API + 靜態頁路由）

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
│   └── routes/
│       ├── dimensions.py       # GET /api/dimensions
│       └── audio.py            # GET /api/audio
├── static/
│   ├── index.html              # 音檔清單頁
│   ├── annotate.html           # Phase 2 placeholder
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

## 後續 Phase

Phase 2 預期改/加：
- `src/routes/annotations.py` — POST/GET annotations
- `src/audio_analysis.py` — librosa 跑 duration/bpm/sample_rate + auto-compute 兩個 acoustic 維度
- `static/annotate.html` — 滑桿、波形（WaveSurfer）、autocomplete
- `static/draft-save.js` — localStorage 草稿（3 秒 debounce）

Phase 4 預期加：
- `scripts/export_dataset.py` — 匯出 is_complete=True 的 annotations
- consensus aggregation（要等第二位標註員加入）
