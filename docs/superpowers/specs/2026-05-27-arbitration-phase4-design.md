# 仲裁 UI（fast-path 快速確認 + 完整仲裁）— 設計文件（Phase 4）

**日期：** 2026-05-27
**狀態：** 設計定案，待 Phase 1–3（PR #19）merge 後實作
**依賴：** Phase 1–3（`Arbitration` 表、`role_gaps`、新 status、`src/arbitration.py`）。本分支 `feat/arbitration-phase4` stacked 於 `feat/arbitration-triangular-lockable`；#19 merge 後 rebase 到 master。
**上游決策：** [methodology-deep-review §D](./2026-05-27-methodology-deep-review.md)、[Phase 1–3 spec §10](./2026-05-27-arbitration-triangular-lockable-design.md)。

---

## 1. 目標

讓 `creator_ready` 真正可達 —— Phase 3 把 status 算出來，但**沒有任何路徑寫 `Arbitration` 紀錄**。Phase 4 補上兩條寫入路徑：

- **fast path**：creator-industry 已對齊（`fast_confirmable`）→ Amber 批次「快速確認」一鍵把多檔的所有欄位以 creator raw value 寫成 `Arbitration`(path=fast)，Notes 可省略。
- **full path**：creator-industry gap > GATE（`needs_arbitration`）→ Amber 在 reconcile 頁逐維設定最終值，寫 `Arbitration`(path=full)，**Notes 強制**。

仲裁完成 → 該檔所有 `ARBITRATED_FIELDS` 有 active 紀錄 → status 變 `creator_ready`。

## 2. 後端

### 2.1 寫入 helper（`src/arbitration.py` 擴充）

```python
def write_arbitration(
    session, *, audio_id, fields_values: dict[str, Any],
    path: str, notes: str | None, arbitrated_by: str,
) -> list[Arbitration]:
    """為一個 audio 的多個 field 各寫一筆 Arbitration（append；不刪歷史）。
    fields_values: {field: raw_value}。value 經 serialize_value 轉 (json, value_type)。"""
```

- fast-confirm：`fields_values` = creator annotation 的 13 欄現值；`path="fast"`；`notes` 預設 `None`（或前端帶「無實質分歧，以 creator 初標為準」）。
- full：`fields_values` = Amber 在 reconcile 設定的值；`path="full"`；`notes` 必填（API 驗）。

### 2.2 fast-path 批次快速確認

`POST /api/admin/arbitrate/fast-confirm`（admin only）

```
body: { "audio_ids": [str, ...], "notes": str | null }
```

- 對每個 audio_id：驗 status == `fast_confirmable`（重算，防 race）；且**非 blind-audit 抽中**（見 §2.4）。不符 → 該筆 skip，回報 skipped 清單。
- 取 creator annotation（`annotator_id_for_role("creator")`）13 欄值 → `write_arbitration(path="fast")`。
- 回 `{ confirmed: [...], skipped: [{audio_id, reason}] }`。

### 2.3 完整仲裁（reconcile 寫入路徑改造）

現行 `/admin/reconcile/{id}` 儲存走 `POST /api/annotations(annotator_id=amber)` **覆寫 amber annotation** — Phase 4 改為寫 `Arbitration`：

新 `POST /api/admin/arbitrate/{audio_id}/full`（admin only）

```
body: { "values": {field: value, ...（13 欄）}, "notes": str }
```

- 驗 creator+industry 皆 is_complete（否則 409「尚不可仲裁」）。
- 🔸 **決策：Notes 對 `needs_arbitration` 檔強制**（任一連續維 creator-industry gap > GATE）；對其餘檔（Amber 主動想覆寫的 fast 檔）Notes 選填。空 Notes + needs_arbitration → 400。
- `write_arbitration(path="full", notes=...)`。
- 🔸 **決策：不覆寫 creator raw annotation。** raw annotation 保留供 gap 歷史；最終值只存 `Arbitration`。export（Phase 6）Creator Edition 取 active arbitration 值。
- reconcile detail GET（`/api/admin/reconcile/{id}`）增加回傳：per-dim creator-industry gap + 是否 needs_full（讓前端標紅 + 決定 Notes 是否必填）。

### 2.4 fast-path 10% 盲審（A5）

`src/arbitration.py`：

```python
def is_blind_audit(audio_id: str) -> bool:
    """確定性抽樣 ~10%：sha1(audio_id) 末位 hex < 2（16 取 2 = 12.5%）。
    抽中的 fast_confirmable 檔不可批次快速確認，必須走 full（強制 Notes），
    讓獨立判斷紀律不只在校準失敗時才有。"""
```

- fast-confirm endpoint 對 `is_blind_audit(audio_id)` 為真者 skip（reason="blind_audit"）。
- reconcile list（needs_arbitration）**額外納入** blind-audit 抽中的 fast_confirmable 檔（標 `audit=true`），讓它們出現在 Amber 的完整仲裁佇列。
- full-arbitrate 對 audit 檔 Notes 同樣強制。
- 不動 Phase 3 status 計算（audit 是 routing 概念，非 status）。

## 3. 前端

🔸 **決策：複用現有頁面，不開新頁。**

- **`/admin/lockable`（待快速確認清單）**：每列加勾選框 + 頂部「✓ 全部快速確認」/「✓ 確認選取」按鈕 → `POST .../fast-confirm`。blind-audit 抽中的列 disable 勾選並標「🔍 需完整仲裁」連到 reconcile。成功後該列移除（已 creator_ready）。
- **`/admin/reconcile/{id}`（完整仲裁頁）**：現有滑桿 + 其他人 markers 保留；儲存改打 `.../full`。`needs_arbitration` 或 audit 檔：Notes textarea 標「必填」，空值前端擋 + 後端 400。per-dim creator-industry gap > GATE 的維度標紅。
- dashboard `fast_confirmable` 卡（待快速確認）已連 `/admin/lockable`；`needs_arbitration`（待仲裁）已連 `/admin/reconcile`（Phase 3 已接）。

## 4. 測試計畫

- `write_arbitration`：13 欄 round-trip（float/list 混）、append 不刪歷史、path/notes 正確。
- `is_blind_audit`：確定性（同 id 同結果）、抽樣比例落在 ~10–15%（對一批 uuid 統計）。
- fast-confirm：`fast_confirmable` → confirmed + 變 creator_ready；非 fast 檔 skip；blind-audit 檔 skip(reason)；batch 部分成功回報。
- full-arbitrate：寫 path=full + creator_ready；`needs_arbitration` 空 Notes → 400；缺 industry → 409；不覆寫 raw annotation（驗 annotation 未變）。
- reconcile GET 回傳含 per-dim gap + needs_full flag。
- 既有 reconcile 測試（仍 POST /api/annotations 的）→ 更新或標記為舊路徑退役。

## 5. 明確不在 Phase 4

- Phase 5 flags（業界內部分歧 / 專業vs大眾）+ industry 校準觸發。
- Phase 6 雙版本匯出（Creator Edition 取 arbitration 值）。
- Phase 7 per-role 校準（self-MAE 寫 AnnotationSnapshot）。
- Phase 8 ICC 分層。

## 6. 待 review 的 🔸 預設

1. UI 複用 `/admin/lockable` + `/admin/reconcile`（不開新頁）。
2. full 仲裁**不覆寫** creator raw annotation（最終值只存 Arbitration）。
3. Notes 強制範圍 = `needs_arbitration` + blind-audit 檔；其餘選填。
4. blind-audit ~10%：`sha1(audio_id)` 末 hex < 2（≈12.5%），確定性。
