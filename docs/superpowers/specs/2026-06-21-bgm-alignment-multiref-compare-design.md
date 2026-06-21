# BGM 對齊 — 多 Ref 校準格 + 四組比對 UI 設計規格

> 狀態：draft（待 Aaron 審）
> 日期：2026-06-21
> 前置必讀：
> - 本 repo `CLAUDE.md`（尤其 #2 不 over-engineer、#5 不加多餘動畫、#6 不引框架、#8 不改 dimensions_config 文字）
> - `docs/superpowers/specs/2026-06-18-bgm-alignment-mode-design.md`（資料模型 + 比對引擎，已實作）
> 目標畫面（mockup，version 為示意）：
> - 輸入端：`~/Downloads/multi-ref-panel-mockup.html`（多 ref 校準格）
> - 輸出端：`~/Downloads/comparison-view-mockup (1).html`（四組比對）

---

## 0. 一頁總覽

把已存在的 BGM 對齊**後端**（`AlignmentReading` / `AlignmentSpec` / 比對引擎 / `/compare/*` API）接上**兩個前端畫面**，並補兩個小後端缺口：

1. **輸入端**：把現有單一首 ref 的標註表單，改寫成 mockup 1 的「**多 ref 校準格**」——維度當列、ref 當欄、共用一條 0–1 軸，右欄即時 Δ；把「比對 3（同關卡多 ref 分歧）」直接畫進輸入畫面。
2. **輸出端**：新建 mockup 2 的「**四組比對頁**」——四個 tab（①④③②），每個有「按住/變動」設定列 + 表格 + 自動判讀。
3. **後端小補**：`level_id` 欄位（關卡）、比對②的收斂專屬路徑、角色規則。

**已經做完、本規格不重做**：資料模型雙表、`compare_pair` / `compute_variance` / `differing_axes`、`/api/alignment/{readings,compare/pair,compare/variance,dimensions,style-options,spec,context,audio/*}`、客戶隔離 token gate、音檔分倉、BGM 四維 view（含 mid_anchor）。

---

## 1. 範圍與隔離（沿用 6/18 spec，不變）

- 既有 annotations.db / 三角校準 / ICC / export 一行不動。
- BGM 對齊資料仍只進 `data/alignment.db`，永不進要賣的資料集。
- 本規格只動：`alignment_db.py`、`routes/alignment.py`、`alignment_compare.py`（可能）、`static/alignment.{html,js}`（改寫）、新增 `static/alignment-compare.{html,js}`。前端維持 vanilla + Tailwind CDN，不引框架、不加多餘動畫。

---

## 2. 決策記錄（Aaron 已拍板 2026-06-21）

| # | 決策 | 理由 |
|---|---|---|
| D1 | **target 走 Option A**：掛每首 ref（perceived + target 各一組），比對②④用「主 ref 選擇器」指定以哪首 ref 的 target 當新曲目標 | per-ref 是錨定式微調（「像這首，但更亮」），對客戶最好填；「2 ref × 2 target 打架」只發生在②，用主 ref 選擇器解即可。改動最小、符合 6/18 已定的「target 掛每首 ref」 |
| D2 | **加 `level_id`**：session = 一個客戶案，level = 案底下一個 BGM 槽位（Base/Free/Main…），各有自己一組 ref + 一首新曲 | mockup 麵包屑兩層俱在；比對③分歧必須鎖在同一關卡內算才正確 |
| D3 | 比對②走**專屬收斂路徑**，不硬套 `compare_pair` | ②（ref-target ↔ deliverable-perceived）天生跨多軸，會被「一次只變一軸」守門標 invalid |
| D4 | MVP **不新增 level→ref 綁定表**；ref 清單走 query string（內部 Amber 試標流程），對外客戶多 ref 綁定**延後** | 立即用途是 Amber 內部自標；符合「成果優先、不堆基建」 |
| D5 | 判讀文案**模板化、資料驅動**，不接 AI、不開自由輸入 | mockup 的人寫文案改成依數值生成（哪維對齊/落差/分歧、點名最熱維度） |

---

## 3. 資料模型變更（最小）

### 3.1 `level_id` 欄位（唯一 schema 變更）
在 `AlignmentReading` 與 `AlignmentSpec` 各加一欄：

```
level_id : str   # 關卡 id（同一 session 下可有多關卡）；NOT NULL，server default ""
```

- 向後相容：既有 row 經 ALTER 後 `level_id = ""`。新流程一律帶真實 `level_id`。
- **比對③ variance、`/readings`、`/spec`、②收斂**全部以 `(session_id, level_id)` 為範圍鍵。

### 3.2 migration（純 SQLAlchemy，獨立於 `src/migrations.py`）
`src/migrations.py` 的 `apply_pending_migrations` 用 SQLModel `session.exec`，alignment.db 是純 SQLAlchemy（`.execute`），**不可共用**。在 `src/alignment_db.py` 寫一個平行的 idempotent ALTER：

```python
def _column_exists(conn, table, column) -> bool:
    rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").all()
    return any(r[1] == column for r in rows)

def apply_alignment_migrations(eng: Engine = engine) -> list[str]:
    applied = []
    with eng.begin() as conn:
        for table in ("alignment_reading", "alignment_spec"):
            if not _column_exists(conn, table, "level_id"):
                conn.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN level_id VARCHAR NOT NULL DEFAULT ''")
                applied.append(f"{table}.level_id")
    return applied
```

在 `main.py` 的 `create_alignment_db()`（line 78）之後呼叫 `apply_alignment_migrations()`。

### 3.3 不新增的表（明確界線，守 D4）
- **不**新增 `AlignmentLevel` / level→ref 綁定表。
- 「本關有哪些 ref」「關卡顯示名」「deliverable 是哪支」由前端從 query string / context 取得（見 §4.4）。
- 對外客戶多 ref 綁定（`ClientLink` 綁一個 level 的多支 ref）→ **延後**，列入 §9 開放項。

---

## 4. P1 — 後端補強

### 4.1 `level_id` 串進現有端點
- `POST /readings`、`POST /spec`：payload 加 `level_id: str = ""`，寫入 row。client 鎖定邏輯不變。
- `GET /readings`、`GET /spec`：加 query `level_id`；有給就 `where level_id == level_id` 再篩。
- `POST /compare/variance`：`VarianceRequest` 加 `level_id`，`_load_set` 的 Identity 帶上 `level_id`。
  - ⚠️ `_load_set` 目前的 where 子句要加 `level_id` 條件；`Identity` schema 加 `level_id` 欄。

### 4.2 `Identity` schema + `_load_set` 加 `level_id`
所有 reading 定位都多一個 `level_id` 維度。`Reading` / `SetIdentity`（`alignment_compare.py`）也加 `level_id`，並在 `differing_axes` 視 `level_id` 差異為一個軸（跨關卡比對應 invalid）。

### 4.3 比對②收斂專屬路徑（D3）
新端點：

```
POST /api/alignment/compare/convergence
body: {
  session_id, level_id, annotator_id, annotator_role,   # 誰標的（通常 client）
  goal_audio_id,            # 主 ref 選擇器選的那首 ref（取其 reading_type=target）
  deliverable_audio_id,     # 新曲
  versions: [1, 2, ...]     # 要看的版本
}
return: {
  "goal": {dim: value, ...},                  # 主 ref 的 target
  "versions": [
    {"version": 1, "values": {dim: v}, "diffs": {dim: |v-goal|}},
    {"version": 2, ...}
  ]
}
```

實作：載入 goal set（`audio_id=goal_audio_id, audio_role="ref", reading_type="target", version=0`）+ 每個 version 的 deliverable perceived set（`audio_id=deliverable_audio_id, audio_role="deliverable", reading_type="perceived", version=N`），每維算 `|v - goal|`。差距邏輯重用 `compare_pair` 的 per-dim abs diff，但**不套**一次只變一軸守門。收斂判讀（vN Δ 是否較 v(N-1) 縮小）在前端算。

### 4.4 角色規則（D1 的落地 + 修改清單 P1）
| 標註者 × 音源 | 該標什麼 | 落地點 |
|---|---|---|
| client × ref | perceived + target（兩值） | 前端：客戶模式顯示 target ring |
| engineer × ref | 只 perceived | 前端：音效師模式隱藏所有 target ring，submit 只送 perceived |
| 任何人 × deliverable（新曲） | 只 perceived | 新曲只標「聽到」，target 在 ref 上 |

- **前端強制**為主（依角色變形表單）。後端維持「存收到的」+ 既有範圍/型別驗證；不加硬性角色×reading_type 擋制（避免 over-engineer，且 engineer fallback 走 query string 的彈性要留）。

### 4.5 GET `/level`（輕量、給輸入頁與比對頁載入上下文）
不靠新表，回傳前端拼好的上下文（內部流程從 query string，client 流程從 token）：

```
GET /api/alignment/level?session_id=&level_id=&audio_ids=A,B&deliverable_id=D&label=...
return: { session_id, level_id, label, role, refs:[{audio_id, label?}], deliverable_id }
```

MVP 可先**省掉這個端點**，由前端直接讀自己的 query string 組上下文；若 client（token）流程也要多 ref 再實作。**標記為 P1 可選**。

---

## 5. P2 — 輸入頁：多 ref 校準格（改寫 `static/alignment.{html,js}`）

目標畫面 = mockup 1。逐區對照：

### 5.1 版面結構
1. **頂列**：`關卡 <label> · session <id> · 標註者 <role>` 麵包屑 + 角色切換鈕（客戶／音效師）。
2. **本關 ref 列**：每首 ref 一個 chip（色點 A/B/… + 名稱 + ▶試聽 + 時長）。⚠️ mockup 的「＋加 ref」按鈕 MVP **隱藏**——ref 清單來自 query string，加 ref＝改 URL；要做成可互動的「加 ref」需 §9.1 的 level→ref 綁定，延後。
3. **圖例**：實心點＝perceived、空心 ring＝target（音效師隱藏）、右欄 Δ 說明。
4. **校準格**（核心）：4 列維度 × 3 欄
   - 欄1 維度：`display_name` + `client_question` + 低/高錨（讀 `/dimensions`）。
   - 欄2 共用 0–1 軌：每首 ref 一個彩色 handle（perceived）；客戶模式每首 ref 多一個同色 ring（target）。可拖动。0.25/0.5/0.75 格線。值標籤（`.90`）。
   - 欄3 Δ：跨所有 ref 該維的 **spread = max−min**（= `compute_variance` 語意）+ badge。
5. **per-ref footer 卡**：每首 ref 一張，含「想額外加的元素」風格 chips（白名單 `/style-options`）+ 規格（loop 單選 / loop_length 單選）。
6. **新曲目標提示**（informational note）：說明 target 掛每首 ref、比對②會用主 ref 選擇器；**不**放獨立目標卡（守 D1）。
7. **儲存列**（sticky）：本關進度（你 X/N 首已標 · 對方 role Y/N）+「儲存」+「查看四組比對 →」（連到 P3，帶 session_id/level_id）。

### 5.2 Δ 欄門檻與配色（與比對頁共用，§7）
| spread | badge | 色 |
|---|---|---|
| < 0.10 | 鎖定 · 保留 | 綠 |
| 0.10–0.20 | 偏鎖定 | 黃 |
| ≥ 0.20 | 需確認 | 紅（該列 highlight） |

### 5.3 state 與儲存
- `state[audio_id][dim] = { perceived: 0.5, target: 0.5 }`；每首 ref 一組。
- 儲存：對每首 ref → `POST /readings`（perceived）；客戶再 `POST /readings`（target）；`POST /spec`（loop/length/style_tags）。全部帶 `level_id`。
- 音效師模式：略過 target 那次 POST。
- localStorage 草稿沿用 CLAUDE.md 慣例（key `draft:{annotator_id}:{level_id}`，3 秒 idle 才存）；**MVP 可先不做草稿**，列為 nice-to-have。

### 5.4 多 ref 顏色
mockup 寫死 2 色（A 藍 `#3f6f8f` / B 橙 `#dc7a18`）。MVP 支援到 ~4 ref，給固定 4 色盤；超過先不處理（log 提示）。

### 5.5 互動限制（守 CLAUDE.md #5）
滑桿/ring 拖動、chip 選中**不加 transition 動畫**。

---

## 6. P3 — 輸出頁：四組比對（新建 `static/alignment-compare.{html,js}`）

目標畫面 = mockup 2。載入時 `GET /readings?session_id=&level_id=` 取全關 reading，前端依 tab 呼叫對應比對 API。

### 6.1 共用結構（每個 tab）
- **設定列**：「按住：<鎖定的 pill>」「變動：<A pill> ↔ <B pill>」+ 一句 hint。
- **表格**：維度 | 位置（mini track 落點）| 各值 | Δ/分歧 | 判讀 badge。
- **判讀框**：模板化一段話（D5），點名最熱的維度 + 給操作含義。

### 6.2 四個 tab 對應 API
| Tab | 比對 | API | 變動軸 | 渲染重點 |
|---|---|---|---|---|
| ① | 音效師 vs 客戶 | `POST /compare/pair`（同 ref、perceived，變 annotator_role） | who | 並排兩值 + Δ；badge 對齊／認知落差 |
| ④ | 聽到 vs 預期 | `POST /compare/pair`（客戶、同 ref，變 reading_type） | reading_type | 方向 ↑↓＝ + `±.NN` + 文字（更正向/更弱化…）；輸出製作指令 |
| ③ | 同關卡多 ref | `POST /compare/variance`（客戶、perceived、多 audio_ids） | audio | 看**分歧**不看差距；鎖定·保留／偏鎖定／分歧·需確認 |
| ② | v1 vs v2 | `POST /compare/convergence`（§4.3） | version（對目標） | 目標｜v1｜v1Δ｜v2｜v2Δ｜收斂（`.20→.05 ✓`） |

### 6.3 主 ref 選擇器（②④ 用）
- ④：選「看哪首 ref」的 perceived↔target。
- ②：選「以哪首 ref 的 target 當新曲目標」（goal_audio_id）。
- 一個下拉/chip 列，列出本關 refs。

### 6.4 Δ 門檻配色（§7 共用）；③ 用「分歧」語意，文案與配色和①②④的「差距」區隔（穩＝鎖定綠、飄＝需確認紅）。

### 6.5 判讀模板（資料驅動，舉例）
- ①：找 Δ 最大維 → 「<維>落差 <Δ>：音效師 .XX、客戶 .YY；其餘 N 維一致可信任。」
- ③：分歧 ≥0.20 的維 → 「<維>兩首給相反方向（A .XX／B .YY）＝客戶還沒定，開案要問的一題。」鎖定維列「必做保留」。
- ④：逐維 ↑/↓/= → 組「製作指令」清單。
- ②：找仍未收斂（vN Δ ≥0.10 或未縮小）的維 → 「v(N+1) 唯一指令＝把 <維> 做<方向>；其餘已達標別再動。」

---

## 7. Δ 門檻與配色（全站單一來源）
共用一個 JS util（`deltaBadge(delta) -> {label, klass}`）：

| Δ / spread | 差距語意（①②④） | 分歧語意（③） | 色 |
|---|---|---|---|
| < 0.10 | 對齊 / 已收斂 | 鎖定 · 保留 | 綠 `--ok` |
| 0.10–0.20 | 接近 | 偏鎖定 | 黃 `--mid` |
| ≥ 0.20 | 落差 / 未收斂 | 分歧 · 需確認 | 紅 `--hot`（列 highlight） |

---

## 8. 比對↔資料定位 對照（除錯參考）
一筆 reading 身分 = `session_id · level_id · annotator_role+annotator_id · audio_id+audio_role · version · reading_type`。
- ①：按住 `level_id, audio_id, audio_role=ref, reading_type=perceived, version=0`；變 `annotator_role/id`。
- ④：按住 `level_id, annotator(client), audio_id, audio_role=ref, version=0`；變 `reading_type`。
- ③：按住 `level_id, annotator(client), audio_role=ref, reading_type=perceived, version=0`；變 `audio_id`（多首）。
- ②：goal = `audio_id=主ref, audio_role=ref, reading_type=target, version=0`；對比 `audio_id=deliverable, audio_role=deliverable, reading_type=perceived, version=1..N`。

---

## 9. 開放項 / 延後（明確不做進 MVP）
1. **對外客戶多 ref 綁定**：`ClientLink` 目前綁單一 audio。客戶要在校準格看多 ref，需讓 token 綁一個 level 的 ref 集合。延後到 Amber 內部流程驗證 OK 後再做。
2. **關卡顯示名的權威儲存**：MVP 由 query string / label 帶入；若要持久化需小表或塞 ClientLink.label。
3. **>4 ref 的顏色盤**。
4. **localStorage 草稿**（nice-to-have）。

## 10. 先不要做（修改清單第四節，照搬）
AI 預標、品質 gate、CCC／ICC、JSON 匯出、仲裁、統計指標、自由輸入標籤、後台維度編輯器。

---

## 11. 驗收（Amber / Aaron 自測）
用 6/18 spec 附錄的兩首 ref 落點建測試資料（過年喜慶 / 舞龍舞獅），驗：
1. 輸入校準格能標兩首 ref 的 perceived（+ 客戶 target），右欄 Δ：valence .05 鎖定、tension .15 偏鎖定、柔烈度 .55 需確認、immersion .00 鎖定。
2. 比對頁 ③ 顯示同樣分歧結論（柔烈度需確認）。
3. ① 切音效師/客戶看認知落差；④ 看方向；② 用主 ref 選擇器 + 假 deliverable v1/v2 看收斂。
4. 音效師模式整頁無 target ring。
5. annotations.db 完全未被觸碰（隔離未破）。
6. 既有測試 `pytest tests/test_alignment_*.py` 全綠（加 level_id 後可能要更新少量測試 fixture）。
