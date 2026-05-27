# 三角架構方法論 — 深度審查與待決議

**日期：** 2026-05-27
**性質：** 對 [arbitration-triangular-lockable spec](./2026-05-27-arbitration-triangular-lockable-design.md) 的跨 phase 深度審查（統計效度 / 紅隊 / 資料架構三視角綜合）。
**用途：** 區分「我已替你決議的工程項」與「只有你 / Amber 能拍板的方法論項」。後者請逐項裁示。

---

## A. 最關鍵的策略發現（方法論層，須 Amber 拍板）

### A1. 整個三角缺乏「外部效度錨點」——目前 ground truth = 一個人的意見 〔CRITICAL〕

Amber 同時是：方法論設計者 + 最終仲裁者（`arbitrated_value`）+ 校準基準（`REFERENCE_ANNOTATOR`）+ 自我一致性標準（self-MAE）。**所有人都對 Amber 量，Amber 對自己量。** 沒有任何 Amber 以外的真值來源——沒有 held-out gold set、沒有第三方裁決、沒有行為驗證。

加上 A2（industry 被訓練成趨近 Amber），三角實際上**塌縮成「Amber ×2」**：yyslin 同意 Amber 不能證明品質，因為她正是被訓練/篩選成同意 Amber 的。買方資料科學團隊問一句「validated against what?」，誠實答案是「一個人的標註偏好，由她自己確認」。

**建議（二選一，須你裁示）：**
- **(a) 誠實重定位 + 重新命名**：產品就是「單一專家策展標註（single-expert curated）+ 一位終端使用者參照」，定價與話術照此。把「Expert Edition」改名「**Creator Edition**」（與 role 命名一致，且不宣稱 = ground truth）。
- **(b) 引入真正的外部錨點**：一批 held-out item 由未參與的第三方專家評分當驗證集；或行為驗證（標為 high-valence 的 BGM 是否真的對應玩家自陳情緒）。成本高但能支撐「validated」話術。

> 沒有 (a) 或 (b)，資料集是「內部自洽但外部無法證偽」。這題不解，後面所有 phase 都是在精修一個定位不明的產品。

### A2. yyslin 的「對齊帶 0.10–0.20」自相矛盾且無法執行 〔CRITICAL〕

- **下界 0.10「太近=模仿」是範疇錯誤**：MAE 量的是「同意幅度」，模仿是「過程/獨立性」問題。兩位真正獨立的專家本來就可能 MAE 很低——罰他們在統計上是反的。低 MAE 同時相容於 (i) 真共識 (ii) 抄襲 (iii) 回歸均值/floor effect，MAE 分不出來。
- **與現行 code 直接打架**：`calibration_feedback.py` 現在 `≤0.15 = 🟢 綠 = pass`，NEXT_ACTIONS 寫「目標降至 ≤0.15」——**現行系統獎勵越近越好，與 0.10 下界完全相反**。兩條規則會同時出貨互相矛盾。
- **誘因有害**：若 yyslin「太近」，這規則等於要她**故意加雜訊**去湊過 0.10，製造假分歧。

**建議：** 拿掉下界。要防模仿就用針對「獨立性」的手段：盲標（yyslin 不可看到 Amber 的值——須驗證 UI 沒預載參照值）、時間獨立（時間戳證明她沒開 Amber 的檔）、**殘差相關檢定**（模仿者會複製 Amber 對第三基準的偏移；真共識 = 低 MAE 但殘差獨立，模仿 = 低 MAE 且殘差與 Amber 相關）。industry target 只留單邊上界（對齊度）或改成殘差獨立性檢查。**須你確認是否拿掉下界。**

### A3. 統計工具選錯：K=2 的 ICC 不該當主指標 〔CRITICAL/HIGH〕

- K=2（甚至 pairwise）的 ICC 極不穩定，N 一小 CI 巨大；point estimate 0.7 在 N≈20 時 95% CI 可能橫跨 0.3–0.9。**現行所有門檻都 gate 在 point estimate 上**（`pass = icc >= threshold`），沒有任何 CI。
- 樣本量不足：33 seed 是上限，intersection design（只取所有人都標過的）會更小。穩定 ICC 需 **N≥30–50**；現行 guard 只擋 `n<2`，n=3 也照報。

**建議：**
- pairwise 對齊（Amber×yyslin）改用 **Lin's CCC + Bland–Altman**（mean bias + 95% LoA）——CCC 直接量連續尺度的 agreement，且拆成 precision×accuracy，正好回答「對齊 vs 系統性偏移」。
- 保留 ICC 的話必須報 **CI（F 分布或 bootstrap）並 gate 在 CI 下界**，不是 point estimate。
- 設並文件化**最低 N（建議 ≥30）**，不足就報「資料不足」而非 pass/fail。
- 所有門檻（0.10/0.15/0.20/0.30/0.40）視為**慣例非驗證過的 cutoff**；最好用 per-dimension SD 正規化（0.15 MAE 在 SD=0.1 的維度是災難，在 SD=0.4 是優秀），或由實測專家對分布反推。**須你認可改用 CCC + CI 路線。**

### A4. 「分歧=商品」對 audience（Vic）沒有任何品質底線 〔HIGH〕

audience 偏離「永不影響」任何 gate，Phase 7 又說 audience 校準「不 gating、不顯示 🔴、不擋 pending」。結果：**沒有任何機制能標記 Vic 的資料壞掉。** 亂拉、疲勞、straight-lining、誤解「valence」、爆走離場，全都產生「分歧」——與「有價值的受眾視角」無法區分，而方法論還把它當商品特性慶祝。

而且 **N=1 的 audience 不是一個「view」**：無法算分布、變異、人口切分。買方問「Dual-View 底下幾個受眾？」答案是一個人。

**建議（須你裁示）：**
- audience 仍要一條**與「對 Amber 對齊」無關**的品質底線：隱藏重複題的 intra-rater 一致性（Vic 對自己一致）、straight-lining 偵測、反應時間 outlier、attention check。**這條底線目前被推到 Phase 8 且「同類音檔」定義未定**——等於唯一的受眾品質守門員尚未規格化。
- N=1 要不要補受眾人數（≥30 才能宣稱分布）？否則「Dual-View Edition」降級為「single end-user reference annotations」，不單獨定價。

### A5. fast-path 橡皮圖章是「設計上的預設」而非邊角 〔MEDIUM〕

yyslin 一旦校準到 ≤0.20，**依定義**幾乎每筆都 `fast_confirmable` → Amber 批次確認採 creator raw value → Expert Edition = Amber 原始滑桿值原封不動。強制寫 Notes 的 full path 只在校準失敗時觸發——**品質儀式只在系統運作最差時才啟動。**

**建議：** fast-path 隨機抽一定比例（如 10%）走 full arbitration 盲審，讓 Notes/獨立判斷紀律不是只在失敗時才有。**須你決定抽審比例（或不做）。**

---

## B. 工程層發現 — 我已決議並寫進 Phase 1–3 spec（可推翻）

| # | 發現 | 決議 |
|---|---|---|
| B1 | **test-retest 在現行 UPSERT 模型下結構性不可能**（`Annotation` 有 `uq_audio_annotator` 唯一鍵，re-標會覆蓋）。且這是 Phase 1 決策非 Phase 7（晚做要二次 migration + 改寫 upsert，且 stats/export/calibration 都假設一檔一人一列）。 | **不動 `Annotation` upsert**（太多 reader 依賴一列）。Phase 1 就建一張 append-only `AnnotationSnapshot`/retest 表（空的、欄位凍結、Phase 7 才寫入），self-MAE 只讀它。零後續 migration。 |
| B2 | **status 邏輯散在 3 處**，spec 漏了 `src/routes/audio.py:132` 的 `_compute_status_inline`（自行 inline 重算 spread）。兩處改、漏第三處 → list 頁顯示舊 badge、dashboard 顯示新分類。 | Phase 3 刪除 `_compute_status_inline`，全部走單一 `compute_status_from_preload`（加 arbitration 預載）。 |
| B3 | **「active = 最新 arbitrated_at」是 N+1 / window-function 陷阱**（1300 檔 × 13 欄）。 | 加複合索引 `(audio_file_id, field, arbitrated_at DESC)`；寫一個共用 `latest_by_audio_field(rows)` reducer，per-file 與 bulk 路徑共用同一邏輯（避免 B2 那種雙路徑漂移）。 |
| B4 | **role 只在非快取 JSON config**（`annotators_loader` 每次重讀磁碟），在 ICC/status 迴圈內解 role→id = N 次磁碟讀。且 `REFERENCE_ANNOTATOR="amber"` 寫死（與 role≠profile 解耦原則矛盾）。 | bulk 操作頂端解一次 role→id map 往下傳，不在 per-row 迴圈解；以 `annotator_id_for_role("creator")` 取代寫死常數。 |
| B5 | **single-rater-per-role 寫死在 gap 引擎簽名**（`dict[str, Annotation]`），但 role≠profile 保證未來一 role 多人。 | gap 引擎簽名現在就處理/守備多人情形（`dict[str, list[Annotation]]` 或一 role 多人時 raise，把假設變大聲）。 |
| B6 | **`is_gold_locked` 退役會留下孤兒**：`lock_gold`/`unlock_gold` 寫端點（`admin.py:212-290`）、`lockable-list`/`reconcile-list` UI、`export` 的 `gold_locked` metadata 與 `min_status` 過濾。 | Phase 3 一併處理：停用 lock/unlock 端點（回 410 或標 deprecated）、UI 同步、`_STATUS_ORDER` 保留 `gold`/`lockable` key 讓舊 `min_status` 請求不 400 並做 old→new 映射。 |
| B7 | **reconcile 現在用 `POST /api/annotations` 覆寫 amber annotation**；新模型下仲裁要寫 Arbitration 表。若 Phase 3 出 taxonomy 但 reconcile 仍覆寫 annotation，status 永遠推不進。 | 明示：Phase 3 出的 taxonomy 在 Phase 4（寫 Arbitration 的 UI）之前**不會產生任何 `creator_ready`**（這是預期，不是 regression）。reconcile→Arbitration 寫端點屬 Phase 4。 |
| B8 | **`creator_ready` 邊角態**：(a) Amber 仲裁後又改 raw annotation → 仲裁變 stale 但仍 creator_ready；(b) industry 在 creator_ready 後才標/改且分歧 → 無「需重新仲裁」逆轉態；(c) 部分維度仲裁 = 永久 limbo（tag 無 gap 概念時 rollup 未定義）；(d) `draft` 把 creator-only 與 industry-only 混為一態（前者是校準集、後者無用）。 | Phase 3 明訂規則：stale = `creator.updated_at > arbitrated_at` 時失效並標記；late/changed industry 分歧會 demote；定義 mixed 完成度的 audio-level rollup；`draft` 拆成 `creator_draft` vs `industry_only`。 |
| B9 | **`arbitrated_value: str` JSON 無型別契約**（float / list[str] / list[float] 混存），Phase 5/6 各自 decode 易漂移。 | 存 `value_type` 判別欄（或 decode 時查 `dimensions_config`），decode 集中在 `src/arbitration.py`。 |
| B10 | **Phase 6 雙版本無法用現行單一 `consensus` block 表達**，`schema_version` 是無 edition 概念的平字串。 | 凍結匯出形狀（即使 Phase 6 才實作）：major bump（→ `1.0.0`）、加 top-level `edition`、拆 `/export/creator_edition.json` 與 `/export/dual_view.json`、`dimension_sources` 詞彙擴充 `creator_arbitrated`。 |
| B11 | **門檻散落多檔**（`GOLD_MAX_SPREAD`、`GREEN/YELLOW_THRESHOLD` 已是「兩處同改」的註解漂移源）。 | Phase 2 起所有門檻（0.20 仲裁 gate / 0.30 industry 校準 / 0.40 商品 / CCC/ICC 門檻）集中一個 constants module。 |

---

## C. 跨層一致性註記

- **C1（同一 bug class）**：「把預期內視角分歧當缺陷」出現在 (i) lockable spread 含 Vic、(ii) 現行 ICC 量 yyslin×Vic 目標 0.7、(iii) 任何全域統一門檻套到 audience。修法一致：**audience 偏離只觀察不 gate；alignment 只在 creator×industry 間要求。** 每個 phase 實作都用這把尺複檢。
- **C2（門檻不一致）**：仲裁 gate `≤0.20 = fast`，校準 code `≤0.15 = 綠 / ≤0.30 = 黃`。同一個 0.18 在仲裁是「自動確認」、在校準是「🟡 該注意」。需統一或文件化「為何仲裁容忍度 > 校準容忍度」。

---

## D. 待你裁示清單（A 區，方法論）

1. **A1 外部效度錨點**：選 (a) 誠實重定位為單一專家策展（+ Expert→Creator Edition 改名），還是 (b) 引入第三方/行為驗證錨點？
2. **A2 yyslin 對齊帶**：是否拿掉 0.10 下界、改用盲標 + 殘差獨立性檢查防模仿？
3. **A3 統計路線**：是否改用 CCC + Bland–Altman、gate 在 CI 下界、設最低 N≥30？
4. **A4 audience 品質底線 + N=1**：是否補受眾人數（≥30）或把 Dual-View 降級為「single end-user reference」？audience 的 garbage filter（隱藏重複/straight-lining/attention）要不要提前到核心而非 Phase 8？
5. **A5 fast-path 盲審**：抽審比例（建議 10%）或不做？

> B 區工程決議若有要推翻的也一併講；否則我據此更新 Phase 1–3 spec 後進 writing-plans。
