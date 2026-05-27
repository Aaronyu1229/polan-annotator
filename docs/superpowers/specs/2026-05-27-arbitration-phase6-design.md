# 雙版本匯出（Creator Edition + Dual-View Edition）— 設計文件（Phase 6）

**日期：** 2026-05-27
**狀態：** 設計定案，待實作
**依賴：** Phase 4（`Arbitration` + `src/arbitration.py`）、Phase 5（quality flags）。base master。
**上游決策：** [methodology-deep-review §A1/§A4 + B10](./2026-05-27-methodology-deep-review.md)、[Phase 1–3 spec §10.4](./2026-05-27-arbitration-triangular-lockable-design.md)。

---

## 1. 目標

把資料集匯出從「單一 mean consensus」拆成兩個誠實命名的版本：

- **Creator Edition**：以 **creator 仲裁值**（active `Arbitration`）為權威標籤的資料集。誠實定位 = **單一專家策展（single-expert curated）**，**不宣稱 ground truth**（A1）。
- **Dual-View Edition**：industry 與 audience 並陳 + Phase 5 flags，呈現「專業 vs 大眾」差異。因 audience N=1，**降級為 single end-user reference annotations**（A4），不宣稱受眾分布。

現行 `mean consensus` 的 `dataset.json`（schema 0.4.0）保留為 legacy 向後相容。

## 2. Schema 版本與端點

🔸 **決策：major bump → `1.0.0`；新增兩個端點，不改既有 `dataset.json` 形狀。**

| 端點 | 內容 | schema |
|---|---|---|
| `GET /api/export/dataset.json` | 既有 mean consensus（legacy 不動）| 0.4.0 |
| `GET /api/export/creator_edition.json`（新）| creator 仲裁值 | 1.0.0 |
| `GET /api/export/dual_view.json`（新）| industry/audience 並陳 + flags | 1.0.0 |

每份頂層加 `"edition": "creator" | "dual_view"` 欄，買方 parser 一眼分辨。

## 3. Creator Edition

### 3.1 收錄範圍
只收 `creator_ready` 檔（所有 `ARBITRATED_FIELDS` 有 active 仲裁）。其餘 status 不進。

### 3.2 每筆 item
- `dimensions`：7 連續維取 active arbitration 值；acoustic 2 維取 `audio.*_auto`（librosa，沿用現行）。
- 多選欄位（loop / source_type / function_roles / genre / worldview / style）：取 active arbitration 值。
- `dimension_sources`：🔸 **擴充詞彙**——仲裁維度標 `"creator_arbitrated"`、acoustic 標 `"librosa_v1"`（取代現行 `human_consensus`）。
- `arbitration_meta`：per-field `{path: fast|full, arbitrated_at, notes}`，讓買方知道哪些是快速確認、哪些經完整仲裁（可審計）。
- `audio_metadata`：沿用（filename / game / duration…）。

### 3.3 實作
- `src/export.py` 加 `_build_creator_item(audio, arbitrations, ...)`：用 `latest_by_audio_field` 取 active 紀錄，`deserialize_value` 還原值。
- 不重用 `_aggregate_consensus`（那是 mean）；Creator Edition 是「取仲裁值」非聚合。

## 4. Dual-View Edition

### 4.1 收錄範圍
industry + audience 皆 is_complete 的檔（要有兩個 view 才能並陳）。

### 4.2 每筆 item
- `industry_view`: yyslin 的 7 連續維 + tags。
- `audience_view`: Vic 的 7 連續維 + tags。
- `creator_view`（選附）: Amber 仲裁值（若 creator_ready）作對照。
- `flags`: Phase 5 的 per-dim `product_divergence`（industry_audience_gap > 0.40）+ `creator_industry_gaps`。
- `audience_quality`: Phase 5 straight-lining 守門結果（suspect 時標記，買方知道這筆受眾資料品質）。

### 4.3 誠實標示
頂層 `meta` 註明：`"audience_n": 1`、`"disclaimer": "single end-user reference, not an audience distribution"`。不出現「audience consensus / distribution」字眼。

## 5. min_status / 過濾

- Creator Edition 隱含 `min_status=creator_ready`（不另開參數）。
- Dual-View 不用 status 過濾（用「industry+audience 皆完成」為條件）。
- 既有 `dataset.json` 的 `min_status` 行為不動。

## 6. 測試計畫

- creator_edition：只含 creator_ready 檔；dimensions 來自 arbitration（非 mean，刻意讓 raw annotation ≠ 仲裁值驗證取對來源）；dimension_sources=creator_arbitrated；arbitration_meta path/notes 正確；acoustic 仍 librosa。
- dual_view：只含 industry+audience 皆完成檔；industry_view/audience_view 值正確；product_divergence flag（gap>0.40）出現；audience_quality suspect 標記；meta.audience_n==1 + disclaimer。
- schema：兩端點 `schema_version==1.0.0` + `edition` 欄正確；legacy `dataset.json` 仍 0.4.0 不破。
- 空資料 / 無 creator_ready → items=[] 不爆。

## 7. 明確不在 Phase 6

- Phase 7：per-role 校準（self-MAE / industry 重新校準流程 / audience 嚴謹 intra-rater）。
- Phase 8：把 Dual-View 的「分歧」用 CCC + Bland–Altman + CI 量化（Phase 6 只並陳原始值 + Phase 5 慣例 flags）。
- 真正的「audience distribution」需先補受眾人數（≥30），非本 phase。

## 8. 待 review 的 🔸 預設

1. major bump 1.0.0；新增 `creator_edition.json` / `dual_view.json` 兩端點；legacy `dataset.json` 不動。
2. 頂層 `edition` 欄 + Dual-View `meta.audience_n=1` + disclaimer（誠實定位）。
3. Creator Edition 只收 `creator_ready`，dimensions 取 active arbitration，`dimension_sources` 加 `creator_arbitrated`，附 `arbitration_meta`。
4. Dual-View 收 industry+audience 皆完成檔，附 Phase 5 flags + audience straight-lining 標記。
