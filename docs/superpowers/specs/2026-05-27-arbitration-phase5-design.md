# 品質 flags + industry 校準觸發信號 — 設計文件（Phase 5）

**日期：** 2026-05-27
**狀態：** 設計定案，待實作
**依賴：** Phase 1–4（`role_gaps`、`thresholds`、`Arbitration`、bulk loaders、dashboard）。base master（Phase 1–4 已 merge）。
**上游決策：** [methodology-deep-review §A4/§C1](./2026-05-27-methodology-deep-review.md)、原始需求（lockable v2 訊息的 flags 段）。

---

## 1. 目標

把三向 gap 從「per-file 仲裁闘門」升級為**集合層級的品質信號**，回答三個方法論問題：

1. **業界對齊出問題了嗎？** `creator_industry_gap > 0.30`（`INDUSTRY_RECAL`）系統性出現 → industry(yyslin) 該重新校準（哪個維度）。
2. **哪些檔是「商品」？** `industry_audience_gap > 0.40`（`PRODUCT_DIVERGENCE`）→「專業 vs 大眾分歧」，**這是賣點不是缺陷**，蒐集成證據。
3. **audience 資料可信嗎？** 在把 audience 分歧當商品前，先擋掉 straight-lining / 亂標（A4：audience 品質底線提前為核心）。

> **同一 bug class 的延伸（C1）**：audience 偏離永不觸發「修正」；只有 creator-industry 失準才觸發校準。Phase 5 把這條規則做成可見的信號。

## 2. Flag 模型（per-dim 分類）

對 creator+industry 皆完成的檔，每個 human 連續維分類：

| flag | 條件 | 語意 / 動作 |
|---|---|---|
| `industry_divergence` | `creator_industry_gap > INDUSTRY_RECAL (0.30)` | 業界內部分歧 → 累積成 yyslin 校準信號 |
| `product_divergence` | `industry_audience_gap > PRODUCT_DIVERGENCE (0.40)` | 專業 vs 大眾分歧 = 商品證據（不修正）|
| `aligned` | 皆未超標 | 正常 |

（兩者可同時成立；`creator_audience_gap` 純觀察，不分類。）

🔸 **決策：flag 即時算（純函式，無新表）**，集合聚合 on-demand（比照 status_summary 的 bulk 模式）。

## 3. 後端

### 3.1 `src/role_gaps.py` 擴充

```python
def classify_dim_flags(gaps: GapsByDim) -> dict[str, set[str]]:
    """每個連續維 → {"industry_divergence"?, "product_divergence"?}。
    None gap（缺角色）不分類。"""
```

### 3.2 `src/quality_flags.py`（新，純聚合）

```python
def aggregate_quality(
    audios, anns_by_audio, role_map,
) -> dict:
    """跑全部 cross-annotated 檔，回：
    - industry_divergence_by_dim: {dim: {count, audio_ids[]}}  ← 校準信號
    - product_divergence_files: [{audio_id, filename, dims[]}]  ← 商品證據
    - audience_quality: straight-lining 守門（見 3.3）
    - recalibration_recommended_dims: [dim]  ← count ≥ RECAL_MIN_FILES 的維度
    """
```

- `RECAL_MIN_FILES`（新增 `thresholds.py`，預設 **3**）：單一維度需 ≥3 檔超 0.30 才建議重新校準，避免單一 outlier 觸發。

### 3.3 audience 品質守門（A4 輕量版）

🔸 **決策：Phase 5 先做 straight-lining 啟發式（可用現有資料算），嚴謹版（隱藏重複題 intra-rater）留 Phase 7。**

```python
def audience_straight_lining(audience_anns: list[Annotation]) -> dict:
    """audience(Vic) 完成標註的「分數多樣性」啟發式：
    - per-dim distinct value 數 / 標準差；若多數維度 distinct ≤ 2 或 SD < 0.05
      → flag suspect=True（疑似亂拉同一值）。
    回 {"suspect": bool, "n_complete": int, "low_variance_dims": [dim]}。"""
```

- `product_divergence` 在報告中**附帶 audience_quality 標記**：suspect 時標「⚠️ audience 資料品質待驗證（Phase 7 嚴謹檢查）」，避免把垃圾當商品。

### 3.4 端點

`GET /api/admin/quality`（admin only）→ 回 `aggregate_quality(...)` 結果。沿用 `bulk_load_annotations_by_audio` + `resolve_role_map` 一次解析，避免 N+1。

dashboard summary（`/api/admin/audio_status_summary` 旁）可加精簡計數，或前端各自打 `/api/admin/quality`。🔸 **決策：獨立 `/api/admin/quality` 端點 + 新 `/admin/quality` 頁，不塞進 status_summary。**

## 4. industry 校準觸發

🔸 **決策：Phase 5 只「建議」，不自動改狀態。** `recalibration_recommended_dims` 在頁面顯示「建議請 yyslin 重新校準維度 X（N 筆 > 0.30）」。實際把 yyslin 設回 pending_calibration / 派校準題 = Phase 7（per-role 校準）。理由：自動改 live 標註員狀態風險高，且 deep-review 指出校準方法本身要改（CCC/盲標），不該在 Phase 5 半套觸發。

## 5. 前端

新 `/admin/quality` 頁（admin gate，比照 /admin/lockable 結構）：

- **區塊 A — 業界對齊（校準信號）**：每個連續維一列，顯示 `creator_industry_gap > 0.30` 的檔數；超 `RECAL_MIN_FILES` 的維度標紅 +「建議重新校準」。
- **區塊 B — 商品證據（專業 vs 大眾）**：`product_divergence` 檔清單（filename + 哪些維度），附 audience_quality 標記。這是賣資料集時的「Dual-View 價值」佐證。
- **區塊 C — audience 品質守門**：straight-lining 啟發式結果；suspect 時紅字提示。
- dashboard 加一個連到 `/admin/quality` 的入口。

## 6. 測試計畫

- `classify_dim_flags`：0.30 / 0.40 邊界（=門檻不算超）；缺角色 → 不分類；同維可雙 flag。
- `aggregate_quality`：industry_divergence 計數 + audio_ids 正確；product_divergence 清單；recalibration_recommended_dims 受 `RECAL_MIN_FILES` 控制（2 筆不建議、3 筆建議）。
- `audience_straight_lining`：全同值 → suspect；正常多樣 → 不 suspect；不足量 → 不誤判。
- 端點：admin gate（非 admin 403）；空資料不爆。
- audience 偏離大但 creator-industry 對齊 → **不**進 industry_divergence（回歸 bug class 驗證）。

## 7. 明確不在 Phase 5

- Phase 6：雙版本匯出（product_divergence 會餵 Dual-View Edition 的價值敘事）。
- Phase 7：實際 industry 重新校準流程 + audience 嚴謹 intra-rater（隱藏重複題，寫 `AnnotationSnapshot`）+ per-role 門檻。
- Phase 8：把 gap 門檻升級為 CCC + Bland–Altman + CI 下界（Phase 5 的 0.30/0.40 是慣例信號，非統計 gate）。

## 8. 待 review 的 🔸 預設

1. flag 即時算、聚合 on-demand，無新 DB 表。
2. industry 校準只「建議」不自動改狀態（實際校準 = Phase 7）。
3. audience 守門 Phase 5 先做 straight-lining 啟發式，嚴謹 intra-rater 留 Phase 7。
4. 獨立 `/api/admin/quality` + `/admin/quality` 頁，不塞 status_summary。
5. `RECAL_MIN_FILES = 3`（單維建議重新校準的最低檔數）。
