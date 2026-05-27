# Per-role 校準策略（self-MAE / 對齊 / audience 守門）— 設計文件（Phase 7）

**日期：** 2026-05-27
**狀態：** 設計定案，待實作
**依賴：** Phase 1（`AnnotationSnapshot` 空表已備）、Phase 5（audience straight-lining 啟發式→本 phase 升級為嚴謹版）。base master。
**上游決策：** [methodology-deep-review §A2/§A4 + C1/H1/H2](./2026-05-27-methodology-deep-review.md)、訓練策略原始需求。

---

## 1. 目標

現行校準對**所有人套同一組門檻**（🟢≤0.15 / 🟡≤0.30 / 🔴>0.30，`src/calibration_feedback.py`），且 audience 也被 vs-Amber MAE gating —— 這對 audience role 是錯的（把預期分歧當缺陷）。Phase 7 改為 **per-role 校準**：

| role | 訓練目標 | 機制 |
|---|---|---|
| **creator**（Amber）| 自我一致性 **self-MAE < 0.10** | test-retest（盲、wash-out），寫 `AnnotationSnapshot` |
| **industry**（yyslin）| 方法論對齊 **vs-creator MAE ≤ 0.20（只上界）** | 盲標 + 對齊報告（**拿掉 0.10 下界**）|
| **audience**（Vic）| 只訓練定義理解、**不訓練分數對齊** | 無 MAE gating；改 comprehension + **intra-rater 一致性 ≥ 0.6**（test-retest）|

## 2. 關鍵方法論修正（deep-review）

- **A2：拿掉 industry 的 0.10 下界。** 「太近=模仿」是範疇錯誤（低 MAE 同時相容真共識/抄襲/回歸均值）。改：**只留上界 0.20** + **盲標**（UI 不得預載 creator 參照值）防模仿。殘差相關檢定需第三基準，本 phase 無 → 列 Phase 8。
- **A4：audience 不以 vs-Amber 分數 gating。** 移除 audience 的 pending_calibration MAE 門檻；改以「定義理解確認 + intra-rater 一致性」決定校準通過。
- **C1/H3：報 CI、設最低 N。** self-MAE / intra-rater 需 **N ≥ 20** 才出數字，且報區間而非點估計；不足 → 顯示「資料不足」。

## 3. test-retest 基礎建設（7A）

🔸 **決策：新增「retest」校準模式**，把已完成的題目盲重發。

- **觸發**：admin（或排程）對某 annotator 指定 retest batch（從其已 is_complete 的 audio 抽 ≥20）。
- **wash-out**：只重發**距原標註 ≥ 14 天**的題（psychometric 慣例，避免記憶污染）。不足 14 天的不納入 self-MAE。
- **盲**：retest 介面**不預載**該人上次的值（也不顯示任何參照）。
- **寫入**：retest 提交寫 `AnnotationSnapshot(annotator_id, audio_id, pass_no=2+, 7 連續維)`；**不**動正式 `Annotation`（upsert 不破）。
- **計算**：`self_mae(annotator)` = 同 (audio, annotator) 跨 pass 的 per-dim |Δ| 平均（原始 `Annotation` 值 vs snapshot 值，或多 snapshot 互比）。

## 4. per-role 校準判定（7B）

`src/calibration_feedback.py` 改為 role-aware（用 `annotator_id_for_role` 取代寫死 `REFERENCE_ANNOTATOR="amber"`）：

```python
def calibration_status(annotator_id, session) -> dict:
    role = get_role(annotator_id)
    if role == "creator":   # 自我一致性
        return {"metric": "self_mae", "value": ..., "pass": self_mae < 0.10, "n": ..., "ci": ...}
    if role == "industry":  # 對齊（只上界）
        return {"metric": "vs_creator_mae", "value": ..., "pass": mae <= 0.20, ...}
    if role == "audience":  # 不 gate 分數
        return {"metric": "intra_rater", "value": ..., "pass": intra >= 0.6 and comprehension_ok, ...}
```

- 移除全域 `GREEN/YELLOW_THRESHOLD` 對 pending gating 的耦合；門檻集中到 `src/thresholds.py`（`SELF_MAE_MAX=0.10`、`INDUSTRY_ALIGN_MAX=0.20`、`AUDIENCE_INTRA_MIN=0.6`、`CALIB_MIN_N=20`）。
- 三色徽章保留作**即時 per-item 視覺回饋**（不洩具體 amber 值），但**不再是 pending 解鎖條件**；解鎖改看上面 role-aware `pass`。

## 5. 盲標強制（7C）

- 稽核 calibration / annotate 前端：industry（與 retest）流程**不得預載參照/前次值**。修任何違反處（deep-review 指 `calibration_feedback` 的正常流程會預載 draft/DB 值）。
- 後端 reference endpoint（`get_reference_annotation`）對 industry 校準流程不回 amber 值（或只在「比對報告頁」事後揭露，不在標註當下）。

## 6. audience 嚴謹守門（7D，取代 Phase 5 啟發式）

- `audience_intra_rater(annotator)`：用 retest snapshot 算 Vic 對同題的 test-retest 一致性（per-dim |Δ| → 1 - mean|Δ| 或 ICC(intra)）。≥ 0.6 視為穩定。
- 取代 Phase 5 的 `audience_straight_lining` 啟發式（保留啟發式當 N<20 時的 fallback）。
- 「divergence = product」只在 audience intra-rater 過關後才採信（否則 Dual-View 標「品質待驗證」）。

## 7. 測試計畫

- test-retest 寫入：retest 提交 → `AnnotationSnapshot` pass_no 遞增；正式 `Annotation` 不變；< 14 天題不納入。
- `self_mae`：同題兩 pass |Δ| 平均正確；N<20 → 回「資料不足」不 gate。
- industry 判定：MAE 0.18 → pass（只上界）；0.25 → fail；**0.05 不再 fail**（拿掉下界回歸驗證）。
- audience 判定：高 MAE-vs-amber **不**影響 pass；intra-rater ≥0.6 + comprehension → pass；straight-lining（全同值）→ intra 低 → fail。
- 盲標：industry 校準 API 回應不含 amber 參照值。
- role-aware：creator/industry/audience 各走對的 metric。

## 8. 明確不在 Phase 7

- Phase 8：殘差相關 mimicry 檢定（需第三基準）、ICC 改 CCC+Bland–Altman+CI、within-category 一致性的分組鍵定義。
- 自動排程 retest（本 phase admin 手動觸發 batch；排程化未來再說）。
- 補受眾人數 ≥30（業務面，非工程）。

## 9. 待 review 的 🔸 預設

1. 新增「retest」校準模式：盲、wash-out ≥14 天、寫 `AnnotationSnapshot`、不動正式 `Annotation`。
2. industry 校準**拿掉 0.10 下界**，只留上界 0.20 + 盲標；殘差檢定留 Phase 8。
3. audience **移除分數 gating**，改 comprehension + intra-rater ≥0.6；保留三色徽章僅作視覺回饋。
4. self-MAE / intra-rater 需 **N≥20** 才出數字 + 報 CI；不足顯示「資料不足」。
5. 門檻集中 `thresholds.py`（SELF_MAE_MAX / INDUSTRY_ALIGN_MAX / AUDIENCE_INTRA_MIN / CALIB_MIN_N）。
