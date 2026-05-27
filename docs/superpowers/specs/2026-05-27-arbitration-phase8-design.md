# ICC 分層重新詮釋 + 統計嚴謹化 — 設計文件（Phase 8）

**日期：** 2026-05-27
**狀態：** 設計定案，待實作
**依賴：** Phase 1（role）、Phase 7（test-retest / AnnotationSnapshot）。base master。
**上游決策：** [methodology-deep-review §A3 + C2/C3/H1–H5](./2026-05-27-methodology-deep-review.md)、ICC 重新詮釋原始需求。

---

## 1. 問題

現行 `src/stats.py::compute_icc_per_dimension` **排除 Amber、只算 yyslin × Vic（K=2）對門檻 0.7**。在三角架構下這量錯對象：

- yyslin×Vic 分歧 = **專業 vs 大眾**，是商品特性 → dashboard 現在把商品當缺陷報（與 lockable spread 同 bug class）。
- 真正該量的「業界對齊」是 **Amber×yyslin**，現行完全沒算（Amber 被排除）。
- K=2 ICC 在 N≈33 時 point estimate 0.7 的 95% CI 可橫跨 0.3–0.9，gate 在點估計不可靠。

## 2. 分層重新詮釋

| 層 | 量什麼 | 統計工具 | 門檻 | gate? |
|---|---|---|---|---|
| **業界對齊** | creator × industry（Amber×yyslin）| **CCC + Bland–Altman**（非 K=2 ICC）| CCC CI 下界 ≥ 0.7 | 是（品質聲明）|
| **三人整體** | creator+industry+audience | ICC(2,k) 報告值 | 無 | **否（自然低=商品，只報告）** |
| **audience 內部一致性** | Vic intra-rater（同類音檔）| within-category 一致性（test-retest）| ≥ 0.6 | 是（audience 品質）|

🔸 **決策：pairwise 對齊改用 Lin's CCC + Bland–Altman，gate 在 CI 下界、最低 N≥30；ICC 僅在報告三人整體時保留（不 gate）。** 理由：CCC 直接量連續尺度 agreement 並拆 precision×accuracy（對齊 vs 系統偏移），K=2 ICC 不穩。

## 3. 後端

### 3.1 `src/agreement.py`（新，純統計）

```python
def ccc(xs, ys) -> dict:        # {value, ci_low, ci_high, n}（bootstrap CI）
def bland_altman(xs, ys) -> dict # {mean_bias, loa_low, loa_high}
def icc_2k(matrix) -> dict       # 三人報告用，附 CI
```

- 所有函式：N < `AGREEMENT_MIN_N`（=30，`thresholds.py`）→ 回 `{insufficient: True}`，不出 pass/fail。
- 門檻視為慣例；per-dim 報告值分布並陳（H4：floor effect 可見）。

### 3.2 改寫 `compute_icc_per_dimension`（或新 `compute_agreement_layers`）

```python
def compute_agreement_layers(session) -> dict:
    """role-aware：
    - industry_alignment: per-dim CCC(creator, industry) + Bland-Altman + CI（gate CI下界≥0.7）
    - overall_three_way: per-dim ICC(2,k) 報告值（不 gate，標『含 audience，自然偏低=商品』）
    - audience_consistency: per-category Vic intra-rater（≥0.6）
    """
```

- 用 `annotator_id_for_role` 解 role（取代寫死 amber）。
- **保留** legacy `compute_icc_per_dimension` 或標 deprecated（dashboard 改讀新層）。

### 3.3 audience within-category 一致性

🔶 **開放項決議（review H5）：分組鍵採 `source_type`（功能類別）為主**，min **5 檔/組**才算；不足 → 該組「資料不足」。理由：source_type 比 game_name（IP，聲學異質）更接近知覺同質；genre 為次選。**實作前仍應對分組做同質性 sanity check（如組內 acoustic 特徵離散度）**，列為實作首步。
- 一致性用 Phase 7 的 test-retest（同題重標）為主；若無 retest 資料，fallback 用組內 SD（標明是 proxy）。

### 3.4 mimicry 殘差檢定（從 Phase 7 延後而來）

- `residual_independence(creator, industry, third_ref)`：industry 對第三基準的殘差是否與 creator 的殘差相關（相關高 = 疑似模仿）。
- ⚠️ **需第三基準**（非 Amber、非 yyslin 的獨立評分）。目前沒有 → 本檢定**標為「待補第三評分者後啟用」**，先佔位不強制。

## 4. 前端

- `/admin/quality`（Phase 5 頁）或 dashboard ICC 區塊改顯示三層：
  - 業界對齊：per-dim CCC + CI（CI 下界 < 0.7 標紅）+ Bland-Altman bias。
  - 三人整體：ICC 報告值，明示「含 audience，低是預期（商品特性），不作 gate」。
  - audience 一致性：per-category。
- 移除舊「yyslin×Vic ICC 對 0.7」的呈現（誤導）。

## 5. 測試計畫

- `ccc`：完全一致→1；固定偏移（+0.3）→ CCC 明顯 < Pearson（驗 accuracy 拆解）；N<30→insufficient。
- `bland_altman`：mean_bias / LoA 正確。
- `compute_agreement_layers`：industry_alignment 算 creator×industry（**不是** yyslin×Vic，回歸 bug 驗證）；overall 不帶 pass/fail；audience per-category。
- within-category：source_type 分組；組 <5 檔→insufficient。
- 門檻邊界 + CI 下界 gate（point=0.72 但 CI 下界=0.6 → 不過）。

## 6. 明確不在 Phase 8

- 補第三評分者（業務面）→ 殘差 mimicry 檢定才能真正啟用。
- 補受眾人數 ≥30 以宣稱受眾分布。
- acoustic 特徵 clustering 當分組鍵（本 phase 用 source_type + sanity check；clustering 為未來優化）。

## 7. 待 review 的 🔸 預設

1. pairwise 對齊改 **CCC + Bland–Altman**，gate **CI 下界 ≥ 0.7**，最低 **N≥30**；ICC 僅報告三人整體（不 gate）。
2. 三人整體 ICC「低=商品」只報告不 gate（呼應 industry_audience 商品 flag）。
3. audience within-category 分組鍵 = **source_type**（min 5 檔/組），實作首步做同質性 sanity check。
4. mimicry 殘差檢定**佔位**，待補第三評分者才啟用。
5. 門檻集中 `thresholds.py`（`AGREEMENT_MIN_N=30`、CCC/audience 門檻）；舊 yyslin×Vic ICC 呈現移除。
