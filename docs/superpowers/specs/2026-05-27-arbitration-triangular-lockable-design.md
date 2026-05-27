# 三角架構仲裁 + lockable 重寫 — 設計文件（Phase 1–3）

**日期：** 2026-05-27
**狀態：** 設計定案，待 Phase 1–3 實作
**範圍：** 本 spec 只涵蓋 Phase 1–3（資料基礎 + gap 引擎 + status 重寫）。Phase 4–6（仲裁 UI、flags/校準觸發、雙版本匯出）另開 spec。

---

## 1. 問題

現行 lockable 邏輯（`src/audiofile_status.py`）：

```
spread = max(三人各維值) - min(三人各維值)
lockable = 每維 spread ≤ 0.20
```

**Bug：** 假設三位標註員應該趨同。但三角架構中，audience（Vic）本來就該跟 creator（Amber）有視角差異——把他的偏離算進 spread 會讓本來合格的檔案卡在 cross_annotated，永遠 lockable 不了。

## 2. 三角架構（方法論角色）

| role | 目前是誰 | 方法論身份 | 對 creator 的期望 |
|---|---|---|---|
| `creator` | Amber | 方法論設計者 + 最終仲裁者（Creator View） | — |
| `industry` | yyslin1024 | 業界同行（Industry View） | 應對齊，gap < 0.20 |
| `audience` | Vic (vvgosick) | 目標受眾 / 終端使用者（Audience View） | 可大可小（視角差異，允許不同） |

**role ≠ profile。** `role` 是方法論架構角色（內部分配，creator/industry/audience），`profile` 是標註員身份特徵（對外 metadata，如 music_professional / general_audience）。目前 1:1 映射，但未來會解耦（例：請 music_professional 來扮演 audience role 測「被要求用直覺反應時的判斷」），故兩者必須獨立欄位，**不可由 profile 推論 role**。

三個 pairwise gap（per 連續維度）：

- `creator_industry_gap = |creator − industry|` — 業界對齊指標。**這是仲裁路徑的唯一闘門。**
- `creator_audience_gap = |creator − audience|` — 觀察用，視角差異，不影響任何判定。
- `industry_audience_gap = |industry − audience|` — 觀察用（Phase 5 才用：>0.40 = 專業 vs 大眾分歧 = 商品本身）。

## 3. 仲裁模型

仲裁（arbitration）是 creator 對「某音檔某欄位」確認最終值的**獨立事件**，不是音檔屬性。

- **粒度：** per (audio × field)。一個音檔的每個可標欄位各自獨立仲裁。「Amber 仲裁了 valence 但還沒仲裁 emotional_warmth」是正常狀態。
- **覆蓋欄位：** 7 個 human 連續維 + `loop_capability` + `source_type` + `function_roles` + `genre_tag` + `worldview_tag` + `style_tag`。acoustic 2 維（librosa deterministic）不仲裁。
- **歷史：** 同 (audio, field) 可有多筆（方法論升級重新仲裁），舊紀錄保留。
- **最低參與：** creator + industry 都 `is_complete` 標過才能算 gap / 進仲裁；audience 可選。industry 缺 → 「等待 industry」，不能仲裁。

### 仲裁路徑（fast / full）

路徑由**連續維的 `creator_industry_gap`** 決定（audience 偏離永不影響）：

- `creator_industry_gap ≤ 0.20` → **fast**：採 creator raw value，Notes 可省略（批次「快速確認」）
- `creator_industry_gap > 0.20` → **full**：走完整仲裁流程（/reconcile），Notes 不可省略

> 🔸 **決策：路徑 per-維度。** 與 per-dim 仲裁一致——每個連續維各自依自己的 creator_industry_gap 判 fast/full。非連續欄位（loop/tags）無 gap 概念：在 fast 場景隨批次快速確認（path=fast）；若該檔有任一連續維走 full、Amber 進 reconcile 時一併設定（path=full）。

## 4. Phase 1 — 資料基礎

### 4.1 Config role 欄位

`data/annotators_config.json` 每人加 `role`：

```json
"amber":      { ..., "role": "creator" },
"yyslin1024": { ..., "role": "industry" },
"vvgosick":   { ..., "role": "audience" },
"guest":      { ..., "role": null }
```

`src/annotators_loader.py`：
- `_REQUIRED_FIELDS` 加 `role`（或設為 optional 並預設 null，避免舊 config 載入失敗 — 採 optional + default null）。
- 新增 `_VALID_ROLES = {"creator", "industry", "audience"}`；非法值（且非 null）raise。
- 新增 helper：`get_role(annotator_id) -> str | None`、`annotator_id_for_role(role) -> str | None`（反查，給 gap 引擎解析誰是 creator/industry/audience）。

### 4.2 `Arbitration` 表（`src/models.py`）

```python
class Arbitration(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    audio_file_id: str = Field(index=True, foreign_key="audiofile.id")
    field: str = Field(index=True)          # valence … style_tag
    arbitrated_value: str                   # JSON-serialized（float 或 list）
    path: str                               # "fast" | "full"
    notes: Optional[str] = None             # full path 時 API 強制要求
    arbitrated_by: str                      # = creator annotator_id
    arbitrated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
```

- 建表走既有 `apply_pending_migrations`（idempotent `CREATE TABLE IF NOT EXISTS` 或 SQLModel metadata create）。
- 🔸 **決策：active = 最新 `arbitrated_at` per (audio, field)；不加 `is_active` 欄，查詢時取最新。** 歷史筆數全保留。
- `ARBITRATED_FIELDS` 常數（列出上述 13 個欄位名）放 `src/audiofile_status.py` 或新 `src/arbitration.py`，作單一資料來源。

## 5. Phase 2 — Gap 引擎（`src/role_gaps.py`，pure module）

純函式、無 DB、無副作用，可獨立單元測試。

```python
def pairwise_gaps(
    by_role: dict[str, Annotation | None],   # {"creator": ann|None, "industry": ..., "audience": ...}
) -> dict[str, dict[str, float | None]]:
    """每個 human 連續維 → {creator_industry, creator_audience, industry_audience}。
    任一側缺 → 該 pair gap = None。"""
```

- 輸入由呼叫端用 `annotator_id_for_role` 解析 role → annotation。
- 只算 `HUMAN_CONTINUOUS_DIMS`（7 維）。
- 提供便利判定：`needs_full_arbitration(gaps) -> set[str]`（回 creator_industry_gap > 0.20 的維度集合）。

## 6. Phase 3 — Status 重寫（`src/audiofile_status.py`）

移除 spread≤0.20 的 lockable 判定，改為 arbitration + gap 驅動。

### 6.1 新 status 分類（audio-level）

🔸 **決策：以下取代舊 5 態的 lockable/gold。**

| status | 條件 |
|---|---|
| `untouched` | 0 筆 is_complete |
| `draft` | 只有 creator **或** industry 其一 is_complete（未湊齊可比對的兩人） |
| `cross_annotated` | creator + industry 都 is_complete，但尚未全部仲裁 |
| `needs_arbitration` | 上述 + 至少一個連續維 creator_industry_gap > 0.20（待走 full reconcile） |
| `fast_confirmable` | 上述 + 所有連續維 creator_industry_gap ≤ 0.20（待 Amber 批次快速確認） |
| `creator_ready` | 所有 `ARBITRATED_FIELDS` 都有 active arbitration 紀錄 → Creator Edition 可出貨（取代 `gold`） |

> 註：`needs_arbitration` 與 `fast_confirmable` 互斥，皆為 cross_annotated 的細分。audience 是否標過不影響 status（只影響 Phase 5 的觀察指標）。

### 6.2 `is_gold_locked` 退役

🔸 **決策：arbitration 表成為唯一真實來源，`creator_ready` 由「所有欄位皆有 active 仲裁」衍生。** 保留 `is_gold_locked` column 向後相容但不再參與 status 計算。Production `gold=0`（見專案記憶），無歷史資料遷移負擔。

### 6.3 受影響的既有函式

- `compute_audiofile_status` / `compute_status_from_preload`：改用新分類。需要一併預載 arbitration 紀錄（避免 N+1，比照 `bulk_load_annotations_by_audio` 加 `bulk_load_arbitrations_by_audio`）。
- `per_dim_spread`：保留供參考或移除（若無其他呼叫端則移除——實作時 grep 確認）。
- `gold_lock_prerequisites`：語意改為 arbitration eligibility（creator+industry 齊、列出 needs-full 維度）。Phase 4 的 UI 會用，但 Phase 3 先把純邏輯就位。
- export `min_status` 過濾（`_STATUS_ORDER`）：更新排序含新狀態。

## 7. 測試計畫

- **Phase 1**：config role 載入 + 驗證（合法/非法/null/缺欄）；`annotator_id_for_role` 反查；Arbitration 表 CRUD + active=最新時間戳;JSON value round-trip（float 與 list）。
- **Phase 2**：pairwise_gaps 各情境（三人齊、industry 缺、audience 缺、值相同 gap=0）；`needs_full_arbitration` 門檻邊界（=0.20 不算超、>0.20 算）。
- **Phase 3**：六個 status 各自的 fixture；creator+industry 齊但 audience 缺仍能 fast_confirmable；audience 大幅偏離不影響 status（回歸舊 bug）；creator_ready 需全欄位仲裁。
- 維持既有測試全綠（status 既有測試會因語意改變需更新，spec review 後於實作處理）。

## 8. 明確延後（Phase 4–7，不在本 spec）

- Phase 4：fast-path 批次「快速確認」admin UI + full 仲裁 Notes 強制（/reconcile 改造）。
- Phase 5：flags（creator_industry > 0.30 → 業界內部分歧 → 觸發 industry 校準；industry_audience > 0.40 → 專業 vs 大眾分歧 = 商品）surfacing + 校準觸發機制。
- Phase 6：雙版本匯出 — Creator/Expert Edition（仲裁值）+ Dual-View Edition（industry/audience 並陳 + flags），export schema bump。
- **Phase 7：per-role 校準/訓練策略（另開 spec）。** 依賴 Phase 1 的 `role` 欄位；與 Phase 5 的 industry 校準觸發重疊。現行校準對所有人套同一組門檻（🟢≤0.15 / 🟡≤0.30 / 🔴>0.30，`src/calibration_feedback.py`），需改為 per-role：
  - **creator（Amber）：自我一致性訓練，self-MAE < 0.10。** 新能力 — 需 test-retest 儲存（同一 item 標 ≥2 次；現行 annotation 是 upsert 覆寫，無重複儲存），計算她的 test-retest MAE 並 surface。
  - **industry（yyslin）：中度方法論對齊，vs-Amber MAE 目標帶 0.10–0.20。** 注意有下界 0.10（過度貼近 = 模仿，非獨立判斷）。取代此 role 的全域門檻。
  - **audience（Vic）：只訓練定義理解，不訓練分數對齊。** audience role 的校準**不**以 vs-Amber MAE gating（不顯示 🔴、不擋 pending）；改為定義理解確認。⚠️ Vic 目前 `pending_calibration` 走全域 gating，此為對 live 流程的改動，上線前需確認切換時機。
- **Phase 8：ICC 分層重新詮釋（另開 spec）。** 集合層級品質指標，非 per-file。**現行 `src/stats.py::compute_icc_per_dimension` 排除 Amber、只算 yyslin × Vic（K=2）對 0.7** —— 在新方法論下量錯對象（yyslin×Vic 分歧 = 商品特性，dashboard 現在等於把商品當缺陷報，與 lockable spread 同一 bug class）。重新分層：
  - **業界對齊度：Amber × yyslin（creator×industry）ICC ≥ 0.7。** 現行 ICC 完全沒算這對（Amber 被排除）。
  - **整體三人 ICC（含 Vic）：自然會低，是商品特性 → 只報告不 gate**（呼應 industry_audience_gap > 0.40 = 商品）。
  - **Vic 內部一致性（intra-rater，同類音檔變異）≥ 0.6。** 量 Vic 對同類音檔評分是否穩定，非與他人一致。
  - 🔶 **開放項：「同類音檔」的定義未定** — 依 genre？source_type？game_name？或標籤組合？此為 Phase 8 前置，須先決定分組鍵才能算 within-category 一致性。

## 9. 待 review 的 🔸 預設（可改）

1. 仲裁路徑 **per-維度**（非 per-音檔）。
2. active 仲裁 = **最新 arbitrated_at**（無 is_active 欄）。
3. `is_gold_locked` **退役**，status 全由 arbitration 衍生。
4. status 分類含 `needs_arbitration` / `fast_confirmable` / `creator_ready` 三個新名。

---

## 10. 深度審查後的實作決議（2026-05-27，全部 accepted）

見 [methodology-deep-review.md](./2026-05-27-methodology-deep-review.md)。以下為**影響 Phase 1–3 的已定案項**，writing-plans 以此為準。

### 10.1 改寫 Phase 1（資料基礎）

- **命名**：`creator_ready` 對應 **Creator Edition**（非 Expert）。產品定位 = 單一專家策展 + 受眾參照，不宣稱 ground truth。
- **新增 `AnnotationSnapshot` 表（append-only，本 phase 建空表、欄位凍結，Phase 7 / audience-floor 才寫入）：**
  `(id, audio_file_id, annotator_id, pass_no, created_at, valence, arousal, emotional_warmth, tension_direction, temporal_position, event_significance, world_immersion)`。
  **不動 `Annotation` 的 upsert / 唯一鍵**（stats/export/calibration 都假設一檔一人一列）。self-MAE（creator）與 intra-rater 一致性（audience）皆只讀此表。理由：test-retest 在現行 UPSERT 下結構性不可能，晚做要二次 migration。
- **`Arbitration` 表補**：複合索引 `(audio_file_id, field, arbitrated_at DESC)`；加 `value_type` 欄（`float` / `list_str` / `list_float`）。
- **新 `src/thresholds.py`** 集中所有門檻：`ARBITRATION_GATE = 0.20`、`INDUSTRY_RECAL = 0.30`、`PRODUCT_DIVERGENCE = 0.40`（門檻視為慣例，非驗證 cutoff）。取代散落的 `GOLD_MAX_SPREAD` / `GREEN_THRESHOLD` 等。

### 10.2 改寫 Phase 2（gap 引擎）

- role 解析：呼叫端在 bulk 操作**頂端解一次** role→annotator_id map 往下傳，**不在 per-row 迴圈**解（config 無快取，會 N 次磁碟讀）。以 `annotator_id_for_role("creator")` 取代寫死 `REFERENCE_ANNOTATOR="amber"`。
- gap 引擎簽名守備「一 role 多人」（`role≠profile` 保證未來會發生）：一 role 解析到多位 is_complete 時 raise，把假設變大聲。
- 共用 reducer `latest_by_audio_field(rows)`，per-file 與 bulk 路徑同一邏輯。

### 10.3 改寫 Phase 3（status 重寫）

- **收斂三處 status 邏輯為一**：刪除 `src/routes/audio.py::_compute_status_inline`，全走 `compute_status_from_preload`（加 arbitration 預載 `bulk_load_arbitrations_by_audio`）。
- **`is_gold_locked` 退役連帶**：停用 `lock_gold` / `unlock_gold`（回 410 或標 deprecated）、`lockable-list` / `reconcile-list` UI 同步；`_STATUS_ORDER` 保留 `gold` / `lockable` key 並做 old→new 映射，避免舊 `min_status` 請求 400。
- **status 邊角規則**：`draft` 拆 `creator_draft` / `industry_only`（後者無用、前者是校準集）；stale 仲裁（`creator.updated_at > arbitrated_at`）失效並標記；late / changed industry 分歧會 demote `creator_ready`；mixed 完成度（部分欄位已仲裁）的 audio-level rollup 明訂。
- **reconcile 寫端點屬 Phase 4**：Phase 3 出的 taxonomy 在 Phase 4（寫 Arbitration 的 UI）之前**不產生任何 `creator_ready`** —— 這是預期，非 regression。

### 10.4 凍結但延後（影響後續 phase，現在定案）

- **A3 統計（Phase 8）**：pairwise 對齊用 **CCC + Bland–Altman**、gate 在 **CI 下界**、最低 **N≥30**。
- **A2 industry（Phase 7）**：取消 0.10 下界；防模仿改盲標 + 殘差獨立性檢定。
- **A4 audience（提前為核心）**：garbage filter（隱藏重複 intra-rater / straight-lining / attention / 反應時間）重用 `AnnotationSnapshot`；**Dual-View 降級為 single end-user reference**，不單獨定價。
- **A5 fast-path（Phase 4）**：隨機 ~10% 走 full arbitration 盲審。
- **匯出（Phase 6）**：major bump → `1.0.0`、top-level `edition` 欄、拆 `/export/creator_edition.json` 與 `/export/dual_view.json`、`dimension_sources` 擴充 `creator_arbitrated`。
