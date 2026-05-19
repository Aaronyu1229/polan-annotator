# 校準報告擴充（calibration report detailed）設計

- **日期**：2026-05-19
- **狀態**：設計已核可，待寫 implementation plan
- **送審阻塞**：是 — 校準音檔庫是計劃書核心賣點，評審 demo 看到 404 會大扣分

---

## 1. 問題與背景

合伙人要求一個 `GET /api/audio/report?annotator={name}` endpoint，回傳含
overall / per-dim / scatter / top-deviations / recommendations 的完整校準報告。

實地查證程式碼後，合伙人 spec 有 5 處需修正（見 §3），但**核心需求可行**：
不用換 stack、不用改 DB schema、不用新套件，絕大部分是加法。

### 「404」的真正成因（非缺功能）

`src/routes/audio.py:138` 有 `GET /api/audio/{audio_id}`，但**沒有**
`/api/audio/report`。`GET /api/audio/report?annotator=amber` 會被貪婪匹配成
`audio_id="report"` → `get_audio()` 丟 `404 找不到音檔：report`。

真正可用的 endpoint 早已存在：`GET /api/calibration/report?annotator=<id>`
（`src/routes/calibration.py:175`），且有對應頁面
`/calibration/report?annotator=<id>`（serve `calibration-report.html`），
自 Phase 12 未動、測試全綠。本設計**擴充這條既有 endpoint**，不新增會撞路由的路徑。

---

## 2. 目標 / 非目標

### 目標
- 既有 `/api/calibration/report` 從「聚合-only」擴充為合伙人 spec 的完整報告
- Demo 可用 `/calibration/report?annotator=vvgosick` 給評審看完整校準證據
- 後端零新套件、零 DB schema 變更；前端維持 vanilla + Tailwind CDN

### 非目標（YAGNI）
- 不新增 `/api/audio/report` 路徑（撞路由、語意錯位）
- 不引入 chart / 前端框架（散點圖手刻 SVG）
- 不改既有 `build_calibration_report()` 與其另一消費者 `src/routes/stats.py:82`
- 不改「Amber 在 Dashboard 點『✓ 認可』才解鎖」的最終認可 gate —
  本報告只「建議」，不「拍板」

---

## 3. 對合伙人 spec 的修正（已與 Aaron 確認）

1. **路徑**：用既有 `/api/calibration/report`，不做 `/api/audio/report`
   （後者撞 `/api/audio/{audio_id}` ＝ 404 本體）。
2. **demo annotator**：用 `vvgosick` 不用 `amber`。amber 是 reference，
   `build_calibration_report()` 對 amber 回 `{"is_reference": True, ...}` 空殼，
   amber-vs-amber 無 MAE 可算。prod 實測 vvgosick 與 amber overlap = 37。
3. **數字**：spec 內「33 筆」與「37/37」自相矛盾。一律從
   `total_overlap`/`reference_total` 動態取，不 hardcode。
4. **`display_name_zh`**：用 `dimensions_config.json` 既有 `label_zh`
   （唯一資料來源，CLAUDE.md #8 不另造欄位），格式為雙語
   （例「Tension Direction 張力方向」），非純中文。
5. **`scatter_data` / `top_deviations`**：含 Amber 逐題值，系統刻意不揭露
   reference per-item（`calibration_feedback.py:106` docstring + Phase 9
   「保 multi-perspective」）。決議：**只給 admin 視角**回，標註員自看版省略
   這兩個 key（聚合-only），不破壞校準獨立性。

### 額外設計決策：`category` 的 subjective/objective

`dimensions_config.json` 自身的 `category` 是 emotion/function/acoustic，與
合伙人要的 subjective/objective 不同軸，且會把 `world_immersion`
（config 標 acoustic，但其實在 `HUMAN_CONTINUOUS_DIMS` 內、有人工標）誤標。

**權威規則**：`category = "subjective" if dim in HUMAN_CONTINUOUS_DIMS
else "objective"`。沿用 MAE 計算本來就用的 `HUMAN_CONTINUOUS_DIMS`
（valence, arousal, emotional_warmth, tension_direction, temporal_position,
event_significance, world_immersion — 共 7 個）為唯一來源。

→ objective（無人工 vs amber MAE）= `loop_capability`（multi_discrete）、
`tonal_noise_ratio`、`spectral_density`，一律 `mae:null, status:"no_data",
overlap_count:0`。

---

## 4. 架構

採「新增專責函式」取向（非就地改既有函式），符合 CLAUDE.md #3/#10 surgical：

```
build_calibration_report()            ← 既有，不動。MAE/Pearson 單一來源。
        ▲ 內部呼叫
        │
build_calibration_report_detailed(    ← 新增於 src/calibration_feedback.py
    session, annotator_id,
    include_reference_detail: bool)
        ▲ 呼叫
        │
GET /api/calibration/report           ← 改 route：加 Depends(require_auth)，
  (src/routes/calibration.py:175)        以 user["is_admin"] 傳 include_reference_detail
        ▲ 渲染
        │
calibration-report.html / .js         ← 擴充：overall 卡 + 既有 per-dim 表
                                         + 手刻 SVG 散點(admin) + Top-10 清單
```

`src/routes/stats.py:82` 仍呼叫既有 `build_calibration_report()`，零影響。

---

## 5. API 合約

`GET /api/calibration/report?annotator=<id>`（需登入；`require_auth`）

### 5.1 admin 視角（完整）

```jsonc
{
  "annotator": "vvgosick",
  "annotator_name": "Vic",
  "role": "general_audience",              // ← annotators_config.annotator_profile
  "is_reference": false,
  "calibration_progress": "37/37",         // f"{total_overlap}/{reference_total}"
  "report_generated_at": "2026-05-19T15:00:00Z",

  "overall": {
    "mae": 0.220,                          // 各 subjective dim mae 平均(沿用 stats.py:86 算法)
    "threshold": 0.15,                     // GREEN_THRESHOLD 常數
    "warning_dims_count": 4,               // count(dim.mae > threshold)
    "warning_dims_threshold": 2,           // 常數
    "recommendation": "needs_training"     // 見 §6 規則
  },

  "dimensions": [
    {
      "name": "tension_direction",
      "display_name_zh": "Tension Direction 張力方向",  // label_zh 原文
      "category": "subjective",            // in HUMAN_CONTINUOUS_DIMS
      "mae": 0.38,
      "threshold": 0.15,
      "status": "warning",                 // "ok" | "warning" | "no_data"
      "overlap_count": 37                  // = sample_size
    },
    {
      "name": "tonal_noise_ratio",
      "display_name_zh": "...（label_zh）",
      "category": "objective",
      "mae": null,
      "threshold": 0.15,
      "status": "no_data",
      "overlap_count": 0
    }
    // ... 全 10 維（7 subjective + 3 objective no_data）
  ],

  "scatter_data": {                        // admin only
    "tension_direction": [
      { "file": "0_points_F.mp3", "amber": 0.50, "annotator": 0.05 }
      // ... overlap 全筆，每 subjective dim 一個 array
    ]
  },

  "top_deviations": [                      // admin only，Top 10
    {
      "file": "Wealth God_s Blessing_Free Game.wav",
      "game": "Wealth God's Blessing",     // AudioFile.game_name
      "section": "Free Game",              // AudioFile.game_stage
      "audio_url": "/api/audio/<id>/stream", // 既有 stream endpoint
      "worst_dim": "tension_direction",
      "worst_dim_display": "Tension Direction 張力方向",
      "amber_value": 0.85,
      "annotator_value": 0.40,
      "diff": 0.45,
      "all_dims": {
        "tension_direction": { "amber": 0.85, "annotator": 0.40, "diff": 0.45 }
        // ... 各 subjective dim
      }
    }
  ],

  "recommendations": {
    "dims_to_retrain": ["tension_direction", "emotional_warmth"],  // verdict red/yellow
    "dims_approved": ["valence", "arousal"],                       // verdict green
    "dims_no_data": ["tonal_noise_ratio", "spectral_density", "loop_capability"],
    "next_actions": [ /* 繁中固定建議文案，constants */ ]
  }
}
```

### 5.2 非 admin 視角（標註員自看）

同上，但**省略 `scatter_data` 與 `top_deviations` 兩個 key**
（聚合統計仍完整：overall / dimensions / recommendations）。前端偵測 key 不存在
即不渲染散點與 Top-10 區塊。

### 5.3 amber 自己 / 無 overlap

沿用既有行為：`is_reference:true` 或 `total_overlap:0` 時回精簡結構
（`dimensions:[]`、無 scatter/top）。前端顯示「無校準資料」說明，不報錯。

---

## 6. recommendation 判定規則

沿用既有 `GREEN_THRESHOLD=0.15` / `YELLOW_THRESHOLD=0.30` 常數，不新增魔術數字：

- `overall.mae <= 0.15` 且 `warning_dims_count < 2` → `"approved"`
- `overall.mae > 0.30` → `"not_recommended"`
- 其餘 → `"needs_training"`

`warning_dims_count` = subjective dims 中 `mae > GREEN_THRESHOLD` 的數量。
no_data dims 不計入 overall.mae，也不計入 warning。

---

## 7. 後端設計

`src/calibration_feedback.py` 新增：

```
def build_calibration_report_detailed(
    session, annotator_id: str, include_reference_detail: bool
) -> dict[str, Any]:
```

步驟：
1. 呼叫既有 `build_calibration_report(session, annotator_id)` 取 per-dim 核心
   （mae / pearson_r / mean_signed_offset / verdict / sample_size）。
2. is_reference 或 total_overlap=0 → 直接回精簡結構（§5.3）。
3. 重撈 overlap 的 my / ref annotation（既有函式已撈過 ref，可重用查詢邏輯；
   為單一職責，detailed 自行查 overlap 的 my+ref rows 一次）。
4. 組 `dimensions[]`：7 subjective 帶數值 + 3 objective no_data；
   `display_name_zh` ← `dimensions_loader` 讀 `label_zh`；`status` 由
   mae vs GREEN_THRESHOLD（no_data 若 sample_size=0）。
5. 組 `overall`（§6）、`recommendations`（依 verdict 分組 + 固定 next_actions）。
6. `include_reference_detail=True` 才加 `scatter_data`（每 subjective dim 的
   `{file, amber, annotator}` array）與 `top_deviations`（每 overlap 檔算跨
   subjective dim 最大 |delta| → 排序取 10，附 game/section/audio_url/all_dims）。
7. `annotator_name` / `role` ← `annotators_loader.get_annotator()`
   的 `name` / `annotator_profile`。

`src/routes/calibration.py` `/report` route：
- 加 `user: dict[str, Any] = Depends(require_auth)`
- 改呼叫 `build_calibration_report_detailed(session, annotator,
  include_reference_detail=bool(user.get("is_admin")))`
- ⚠️ dev/單機模式 `require_auth` 恆回 `is_admin=True`（middleware.py:67），
  本機 demo 一律看 admin 版；非 admin 分支須靠 pytest 直接測 builder。

不可變性：detailed 函式只組新 dict，不 mutate 既有函式回傳值
（先 `dict(core)` 複製再加 key）。

---

## 8. 前端設計

`static/calibration-report.html` + `static/calibration-report.js`：

- **Overall 卡**：mae / threshold / recommendation 徽章（三色沿用既有
  green/yellow/red 規則），warning dims 數。
- **Per-dim 表**：既有表擴充 category 欄與 no_data 列樣式（灰、標「無資料」）。
- **散點圖（admin only）**：每 subjective dim 一張手刻 vanilla SVG，
  x=Amber、y=annotator、0–1 方框、對角線=完美一致、點為 overlap 各檔。
  約 40 行、零依賴。`scatter_data` key 不存在則整區不渲染。
- **Top-10 偏差（admin only）**：清單列出 file / game·section / worst_dim /
  diff，可展開看 all_dims；每列 inline `<audio controls src=audio_url>`
  （沿用 2026-05-17 upload-audio-preview 的單一實例原生 audio 模式，
  無新 lib，後端零改）。`top_deviations` key 不存在則整區不渲染。
- 文案繁中、sentence case；不加滑桿/chip 以外的動畫（CLAUDE.md UI convention）。

---

## 9. 邊界情況

| 情況 | 行為 |
|---|---|
| `?annotator=amber`（reference） | `is_reference:true`，前端顯示「Amber 為基準，無自我校準報告」 |
| annotator 無任何 overlap | `total_overlap:0`，顯示「尚未開始校準」 |
| 某 subjective dim 該人全未填 | 該 dim `sample_size:0` → `status:"no_data", mae:null` |
| objective 維度 | 一律 no_data（無人工 vs amber） |
| 非 admin 開報告 | 無 scatter/top_deviations，聚合區塊完整 |
| 本機 dev | `is_admin` 恆 True → 看 admin 版（測試另證非 admin 分支） |
| Pearson 常數序列 | 沿用既有 `_pearson` 回 None |

---

## 10. 測試（pytest，維持 80%+ 覆蓋）

擴充 `tests/test_calibration_feedback.py`：
- detailed builder `include_reference_detail=True/False`：前者有
  scatter_data/top_deviations，後者無；聚合區塊兩者一致。
- objective 3 維出現且為 no_data；subjective 7 維數值正確。
- `top_deviations` 排序正確（跨 dim 最大 |delta| 由大到小）、長度 ≤ 10、
  `all_dims` 完整、`audio_url` 格式正確。
- `overall.recommendation` 三檔門檻（approved / needs_training /
  not_recommended）邊界值。
- is_reference / total_overlap=0 走精簡結構不報錯。
- route 層：admin → 含敏感 key；非 admin → 不含（以 monkeypatch/依賴覆寫
  模擬 `require_auth` 回 `is_admin=False`，因 dev 恆 True）。

---

## 11. 不在範圍

- 新 `/api/audio/report` 路徑（撞路由，明確排除）
- 前端 chart 套件、scatter 互動 zoom/tooltip 進階功能
- 改 Amber「✓ 認可」最終 gate 邏輯
- 修本機 `annotations.db` schema drift（本功能只讀 `Annotation` 表，
  不碰 Phase 10 AudioFile 欄位，本機可開發測試）
