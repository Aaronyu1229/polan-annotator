# BGM 對齊 P2（輸入頁 · 多 ref 校準格）Implementation Plan

> **For agentic workers:** 這是前端（vanilla JS + Tailwind CDN，repo 無 JS 單元測試框架），驗收用 agent-browser 對照 mockup + 真實資料，不是 pytest。逐 Task 做、各自 commit。

**Goal:** 把現有單一首 ref 的 `static/alignment.{html,js}` 改寫成 mockup 1 的「多 ref 校準格」：維度當列、ref 當欄、共用一條 0–1 軸、右欄即時 Δ，並依角色（客戶/音效師）變形。

**Architecture:** 以 mockup `~/Downloads/multi-ref-panel-mockup.html` 為**骨架直接移植**（HTML 結構 + CSS 已是目標樣貌），把寫死的 ref/數值換成**資料驅動渲染**，加上拖曳 handle/ring、即時 Δ、儲存、進度。ref 清單 MVP 走 query string（內部 Amber 流程）。

**Tech Stack:** vanilla ES modules、無分號、fetch API、Tailwind CDN。**不引任何框架/套件**。

## Global Constraints
- 權威 spec：`docs/superpowers/specs/2026-06-21-bgm-alignment-multiref-compare-design.md`（§5）。
- 目標畫面：`~/Downloads/multi-ref-panel-mockup.html`（version 數值為示意，照版面/互動，不照數字）。
- 沿用既有 P1 後端端點，**不改後端**（若發現需要後端微調，停下來在 PR 註明，不自行加端點）。
- CLAUDE.md：使用者可見文字繁中、identifier 英文、**不加多餘 transition 動畫**（滑桿/chip 值變化不要動畫——標註員長時間操作）、JS 2-space、無分號、const 為主。
- 不碰 annotations.db、不碰既有標註頁 `static/index.html` 等。
- 在分支 `feat/bgm-alignment-multiref-compare` 上做（接續 P1），**不動 master、不 merge**。

## P1 已提供的資料契約（消費，不可改）
- `GET /api/alignment/context` → `{role, annotator_id, session_id, alignment_audio_id}`
- `GET /api/alignment/dimensions` → `{dimensions:[{key, display_name, low_anchor, mid_anchor, high_anchor, client_question}]}`（順序即顯示序）
- `GET /api/alignment/style-options` → `{style_tags:[...]}`
- `GET /api/alignment/readings?session_id=&level_id=` → `{sets:[{session_id, level_id, annotator_id, annotator_role, audio_id, audio_role, version, reading_type, values:{dim:float}}]}`
- `POST /api/alignment/readings` body：`{session_id, level_id, annotator_id, annotator_role, audio_id, audio_role, version, reading_type, values:{dim:float}, note?}`
- `POST /api/alignment/spec` body：`{session_id, level_id, annotator_id, annotator_role, audio_id, audio_role, version, loop, loop_length, style_tags:[...]}`
- `GET /api/alignment/audio/{alignment_audio_id}/stream`

## 頁面上下文來源（D4：query string 驅動）
內部（engineer/Amber）流程，輸入頁讀 query：
```
?session_id=s1&level_id=L1&level_label=Main%20Game%20·%20福龍迎春&annotator_id=amber&annotator_role=client&audio_ids=refA,refB&deliverable_id=song
```
- `audio_ids`：本關 ref 的 alignment_audio_id 清單（逗號分隔），決定校準格的「欄」。
- client（token）流程目前只綁單一 audio（`/context` 的 alignment_audio_id）→ 退化成單欄校準格；**多 ref client 綁定本期不做**（spec §9.1）。

---

### Task 1: 移植 mockup 骨架 + 資料驅動渲染（靜態落點）

**Files:**
- Rewrite: `static/alignment.html`（以 mockup 1 的 `<head><style>` + `<body>` 結構為基底）
- Rewrite: `static/alignment.js`
- 參考（唯讀，照抄結構/CSS）：`~/Downloads/multi-ref-panel-mockup.html`

**做什麼：**
1. 把 mockup 的 CSS 原樣搬進 `alignment.html`（`:root` 變數、grid、track、handle、ring、delta、foot、savebar、`body.engineer .targetonly{display:none}` 等）。
2. 把寫死的 HTML 換成容器骨架：頂列 crumbs（填 query 的 level_label/session/role）+ 角色切換鈕；空的 `#refbar`、`#grid`、`#foot`、`#savebar`。
3. `alignment.js`：
   - 讀 query string 組 `CTX = {session_id, level_id, level_label, annotator_id, annotator_role, audio_ids:[], deliverable_id}`；若 `/context` 回 client，覆蓋成綁定值、`audio_ids = [alignment_audio_id]`。
   - `fetchJson` 工具沿用既有寫法。
   - `Promise.all` 載 `/dimensions` + `/style-options`。
   - 渲染 refbar：每個 audio_id 一個 refchip（色點 A/B/…用固定 4 色盤 `['#3f6f8f','#dc7a18','#5b8f3f','#8f3f6f']`、名稱先用 audio_id、▶ 接 `/audio/{id}/stream`）。
   - 渲染 grid：thead（維度 | 0/0.5/1 軸 | 兩首 Δ）+ 每維一列；每列 track 內每個 ref 一個 handle（perceived，預設 left:50%）、client 模式每個 ref 一個 ring（target，預設 left:50%）+ value tag + 0.25/0.5/0.75 gridline。
   - `state[audio_id] = {}`；`state[audio_id][dim] = {perceived:0.5, target:0.5}`。

**驗收（agent-browser）：**
- 本機起 server（`.venv/bin/uvicorn src.main:app --port 8000` 或既有方式），開
  `http://localhost:8000/static/alignment.html?session_id=s1&level_id=L1&level_label=Main+Game&annotator_id=amber&annotator_role=client&audio_ids=refA,refB`
- 看到：兩欄 ref、4 維列、每列兩個彩色 handle、版面接近 mockup。
- **Commit**：`git commit -m "[Phase 6] alignment 輸入頁: 移植校準格骨架 + 資料驅動渲染"`

---

### Task 2: 拖曳 handle/ring + 即時 Δ + badge 配色

**Files:** Modify `static/alignment.js`（+ 必要的小 CSS 在 alignment.html）

**做什麼：**
1. 寫 `makeDraggable(el, track, onChange)`：pointer events（pointerdown/move/up），由游標 x 相對 track 寬算 `v = clamp((x-left)/width, 0, 1)`，設 `el.style.left = v*100+'%'`、更新對應 value tag、呼叫 `onChange(v)`。**不加 transition**。
2. handle 拖動 → `state[ref][dim].perceived = v`；ring 拖動 → `state[ref][dim].target = v`。
3. 每維即時算 Δ = 該維所有 ref 的 **perceived 的 spread = max−min**；寫進該列 delta cell。
4. `deltaBadge(d)` util（**與 P3 共用語意**，spec §7）：`<0.10 → {label:'鎖定 · 保留', klass:'lock'}`；`0.10–0.20 → {label:'偏鎖定', klass:'lock'}`（用 lock 的淡色變體即可）；`≥0.20 → {label:'需確認', klass:'check'}` 並給該列加 `.hot` highlight。沿用 mockup 的 `.lock`/`.check` 配色變數。

**驗收（agent-browser）：**
- 拖 refA 柔烈度到 ~.25、refB 到 ~.80 → Δ 顯示 ~0.55、badge「需確認」紅、該列 highlight。
- 兩首 valence 都拖到 ~.9 → Δ ~0.0、badge「鎖定 · 保留」綠。
- **Commit**：`git commit -m "[Phase 6] alignment 輸入頁: 拖曳 + 即時 Δ + badge 配色"`

---

### Task 3: per-ref footer 卡（風格 + 規格）+ 角色切換

**Files:** Modify `static/alignment.{html,js}`

**做什麼：**
1. footer：每個 ref 一張 fcard：標題（色點 + 名稱）；「想額外加的元素」風格 chips（白名單 `/style-options`，多選 toggle）；「規格」loop 單選（無縫循環/一次性）+ loop_length 單選（~15s/~30s/~60s）。state 存 `spec[ref] = {style_tags:[], loop:null, loop_length:null}`。
2. 角色切換鈕：點「音效師」→ `document.body.className='engineer'`（CSS 已隱藏 `.targetonly`）→ 所有 target ring + 圖例 target 列消失；點「客戶」還原。crumbs 的 role 文字同步更新。
3. 確保 ring 元素掛 class `targetonly`（engineer 模式整頁無 target）。

**驗收（agent-browser）：**
- 客戶模式：每列有 handle + ring；footer 兩張卡可選 style/loop/length。
- 切音效師：ring 全消失、圖例只剩 perceived。
- **Commit**：`git commit -m "[Phase 6] alignment 輸入頁: footer 風格/規格卡 + 角色切換隱 target"`

---

### Task 4: 儲存 + 進度列 + 連到比對頁

**Files:** Modify `static/alignment.js`

**做什麼：**
1. 儲存：對每個 ref：
   - `POST /readings`（reading_type `perceived`、audio_role `ref`、version 0、values=該 ref 各維 perceived、帶 level_id）。
   - client 再 `POST /readings`（`target`）；engineer 略過。
   - `POST /spec`（loop/loop_length/style_tags、帶 level_id）。
   - 全部成功顯示 banner「已儲存」，任何失敗顯示具體錯誤（沿用既有具體 error 文案要求）。
2. 進度列：載入時 + 儲存後 `GET /readings?session_id=&level_id=`，算「目前角色已標的 ref 數 / audio_ids 總數」、以及對方角色已標數，顯示「本關進度 X/N（你 · role）· 對方 Y/N」。
3. 「查看四組比對 →」按鈕：連到 `static/alignment-compare.html?session_id=&level_id=`（P3，本期可先連、頁面 P3 才生）。
4. localStorage 草稿：**本期不做**（spec §9 nice-to-have）。

**驗收（agent-browser）：**
- 標完兩首 ref 按儲存 → banner 成功；重整後 `GET /readings` 有資料、進度顯示 2/2。
- 用 spec §11 的過年/舞龍落點建資料，確認 Δ 結論：valence .05 鎖定、tension .15 偏鎖定、柔烈度 .55 需確認、immersion .00 鎖定。
- **Commit**：`git commit -m "[Phase 6] alignment 輸入頁: 儲存 + 進度列 + 連比對頁"`

---

## P2 完成定義
1. agent-browser 跑過上述四個驗收、版面與 mockup 1 一致、互動正常。
2. 既有 `pytest -k alignment` 仍綠（前端改寫不應影響後端；若動到後端就是超範圍）。
3. annotations.db 未被碰。
4. push 分支（PR #28 自動納入這些 commit）；**不 merge**。

## Self-Review 結果
- **Spec §5 覆蓋**：§5.1 版面→T1/T3；§5.2 Δ 門檻→T2；§5.3 state/儲存→T4；§5.4 多色盤→T1；§5.5 無動畫→全程約束。§5.1 第6點「新曲目標提示 note」→ 補進 T3 footer 區下方（informational，照 mockup `.newgoal`）。
- **Placeholder**：deltaBadge 的中段 klass 待 T2 落實時對齊 mockup CSS class（已標明用既有 lock 淡色/mid），非 TODO。
- **型別一致**：`state[ref][dim]={perceived,target}`、`spec[ref]={style_tags,loop,loop_length}` 全程一致；POST body 欄位對齊 P1 契約。
