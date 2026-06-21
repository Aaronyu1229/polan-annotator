# BGM 對齊 P3（輸出頁 · 四組比對）Implementation Plan

> **For agentic workers:** 前端（vanilla JS + Tailwind CDN，無 JS 單元測試框架），驗收用 agent-browser，不是 pytest。逐 Task 各自 commit。

**Goal:** 新建 `static/alignment-compare.{html,js}`，做成 mockup 2 的「四組比對頁」：四個 tab（①④③②），各有「按住/變動」設定列 + 表格 + 資料驅動判讀，對接 P1 的 `/compare/*` 端點。

**Architecture:** 以 mockup `~/Downloads/comparison-view-mockup (1).html` 為骨架移植（HTML+CSS 已是目標樣貌）。載入時 `GET /readings` 取全關 reading，前端據此發現 refs / 角色 / 版本 / deliverable，再依 tab 呼叫對應比對 API 渲染。

**Tech Stack:** vanilla ES（無分號、2-space、const 為主）、fetch API、Tailwind CDN。**不引框架/套件**。

## Global Constraints
- 權威 spec：`docs/superpowers/specs/2026-06-21-bgm-alignment-multiref-compare-design.md`（§6、§7、§8）。
- 目標畫面：`~/Downloads/comparison-view-mockup (1).html`（照版面/邏輯，數值示意）。
- **不改後端**（端點 P1 已齊；若真缺東西，停下來在報告列出，不自行加）。
- **不改 P2 的 `static/alignment.{html,js}`**（除了它已有的「查看四組比對 →」連結已指向本頁）。
- CLAUDE.md：使用者文字繁中、identifier 英文、不加多餘動畫、JS 2-space 無分號。
- 在分支 `feat/bgm-alignment-multiref-compare` 上做，**不動 master、不 merge、不開新 PR**（PR #28 自動更新）。

## 消費的 P1 端點契約
- `GET /api/alignment/dimensions` → `{dimensions:[{key, display_name, low_anchor, high_anchor, ...}]}`（顯示序）
- `GET /api/alignment/readings?session_id=&level_id=` → `{sets:[{annotator_role, audio_id, audio_role, version, reading_type, values:{dim:float}}]}`（用來發現本關有哪些 ref / 角色 / deliverable 版本）
- `POST /api/alignment/compare/pair` body `{a:Identity, b:Identity}` → `{diffs:{dim:float}, differing_axes:[...], valid:bool}`
  - `Identity = {session_id, level_id, annotator_id, annotator_role, audio_id, audio_role, version, reading_type}`
- `POST /api/alignment/compare/variance` body `{session_id, level_id, annotator_id, annotator_role, audio_role, version, reading_type, audio_ids:[...]}` → `{spread:{dim:float}, n:int}`
- `POST /api/alignment/compare/convergence` body `{session_id, level_id, annotator_id, annotator_role, goal_audio_id, deliverable_audio_id, versions:[int]}` → `{goal:{dim:float}, versions:[{version, values:{dim:float}, diffs:{dim:float}}]}`

## 頁面上下文
讀 query：`?session_id=&level_id=`（從 P2 的「查看四組比對 →」帶來）。`annotator_id` 預設用 readings 裡出現的 client id（或 query `annotator_id`）。

## 共用 Δ 配色（spec §7）
本頁自帶 `deltaBadge(d)`（與 P2 同門檻，刻意小幅重複 3 行、加註解指向 spec §7 與 alignment.js；不為此抽共用模組以免回頭動到已驗收的 P2）：
- `<0.10` → 差距語意「對齊/已收斂」、分歧語意「鎖定 · 保留」、class `ok`（綠）
- `0.10–0.20` → 「接近」/「偏鎖定」、class `mid`（黃）
- `≥0.20` → 「落差/未收斂」/「分歧 · 需確認」、class `hot`（紅，列 highlight）

---

### Task 1: scaffold + 載入資料 + tab 骨架

**Files:**
- Create: `static/alignment-compare.html`（移植 mockup 2 的 `<style>` + top/tabs/panel 結構）
- Create: `static/alignment-compare.js`
- 參考（唯讀）：`~/Downloads/comparison-view-mockup (1).html`

**做什麼：**
1. `alignment-compare.html`：搬 mockup 2 CSS（`:root` 變數、tabs、setup、table、mtrack/dot、badge、read 等）；body 放 top（crumbs：session/關卡/維度數）+ 4 個 tab 按鈕（①音效師vs客戶 ④聽到vs預期 ③同關卡多ref ②v1vsv2）+ 4 個空 panel 容器 `#p1 #p4 #p3 #p2`。
2. `alignment-compare.js`：
   - 讀 query `session_id` / `level_id`。
   - `Promise.all` 載 `/dimensions` + `/readings?session_id=&level_id=`。
   - 從 sets 推導：`refs`（distinct audio_id where audio_role=ref，依出現序）、`roles`（有哪些 annotator_role）、`clientId`、`deliverable`（audio_id where audio_role=deliverable）、`versions`（deliverable 的 distinct version 升序）。
   - tab 切換邏輯（show(n)）：套用 mockup 的 panel on/off。
   - 預設顯示 tab ①、其餘 lazy render（切到才算）。

**驗收（agent-browser，先用 curl 種資料見下方「驗收資料種法」）：**
- 開 `http://localhost:8000/static/alignment-compare.html?session_id=s1&level_id=L1` → 看到 4 個 tab、crumbs 正確、預設 ① panel。
- **Commit**：`git commit -m "[Phase 6] alignment 比對頁: scaffold + 載入資料 + tab 骨架"`

---

### Task 2: tab ①（音效師vs客戶）+ ④（聽到vs預期）

**Files:** Modify `static/alignment-compare.{html,js}`

**做什麼：**
1. **① 音效師 vs 客戶**：設定列「按住：ref <主ref> · perceived」「變動：音效師 ↔ 客戶」。對選定主 ref 呼叫 `/compare/pair`：
   - `a = {…, annotator_role:'engineer', annotator_id:<engineer id 或同 clientId 視資料>, audio_id:主ref, audio_role:'ref', version:0, reading_type:'perceived'}`
   - `b = {…, annotator_role:'client', annotator_id:clientId, audio_id:主ref, audio_role:'ref', version:0, reading_type:'perceived'}`
   - 表格每維：display_name | mtrack(兩 dot) | 音效師值 | 客戶值 | Δ | badge（`deltaBadge`，<.10「對齊」/≥.20「認知落差」）。
   - 值從 readings 撈（前端已有 sets）或從 pair 回傳推（pair 只回 diffs，值仍需自 sets 取——用 sets 建 `valueOf(role,audioId,readingType,version,dim)` helper）。
   - 判讀框：找 Δ 最大維 → 「<維>落差 <Δ>：音效師 .XX、客戶 .YY；其餘 N 維一致、可信任。」若該 ref 缺某角色資料，顯示「<角色>尚未標此 ref」。
2. **④ 聽到 vs 預期**：主 ref 選擇器（下拉/chip 列出 refs）。設定列「按住：客戶 · ref <主ref>」「變動：perceived ↔ target」。`/compare/pair`：a=client perceived、b=client target（同 ref）。
   - 表格每維：display_name | mtrack(perceived dot + target 空心 dot) | 聽到 | 預期 | 方向（↑/↓/= + `±.NN` + 文字「更正向/更弱化/更烈一點/保持」）。方向文字依 sign(target−perceived) 與維度語意，簡單規則：>0「↑ +.NN」、<0「↓ −.NN」、=0「= 保持」。
   - 判讀框：彙整成「新曲製作指令（這首 ref 版）」清單。

**驗收（agent-browser）：**
- ① 顯示音效師/客戶兩值 + Δ；柔烈度（種資料時 eng .50 / cli .25）→ Δ .25 紅「認知落差」。
- ④ 切主 ref，顯示 perceived→target 方向箭頭。
- **Commit**：`git commit -m "[Phase 6] alignment 比對頁: tab ① 音效師vs客戶 + ④ 聽到vs預期"`

---

### Task 3: tab ③（同關卡多 ref）+ ②（v1 vs v2 收斂）

**Files:** Modify `static/alignment-compare.{html,js}`

**做什麼：**
1. **③ 同關卡多 ref**：設定列「按住：客戶 · perceived」「變動：A ↔ B（多 ref）」。呼叫 `/compare/variance`：
   - body `{session_id, level_id, annotator_id:clientId, annotator_role:'client', audio_role:'ref', version:0, reading_type:'perceived', audio_ids:refs}`。
   - 表格每維：display_name | mtrack(各 ref dot) | 各 ref 值 | 分歧(spread) | badge（**分歧語意**：<.10「鎖定·保留」/.10–.20「偏鎖定」/≥.20「分歧·需確認」）。
   - 判讀框（分歧讀法，非差距）：分歧 ≥.20 的維 → 「<維>兩首給相反方向（A .XX／B .YY）＝客戶還沒定，開案要問的一題」；鎖定維 → 「必做保留」。
2. **② v1 vs v2 收斂**：主 ref 選擇器（goal）。呼叫 `/compare/convergence`：
   - body `{session_id, level_id, annotator_id:clientId, annotator_role:'client', goal_audio_id:主ref, deliverable_audio_id:deliverable, versions:versions}`。
   - 表格每維：display_name | 目標 | v1 | v1Δ | v2 | v2Δ | 收斂（`.20→.05 ✓`，依末版 Δ<.10 給 ✓、否則 ✗，class ok/hot）。
   - 若無 deliverable / 無 versions：panel 顯示「尚無新曲版本，開案後客戶標 deliverable 才有資料」。
   - 判讀框：找末版仍未收斂（Δ≥.10）的維 → 「v(N+1) 唯一指令＝把 <維> 做<方向>；其餘已達標別再動」。

**驗收（agent-browser）：**
- ③ 柔烈度分歧 .55 紅「分歧·需確認」、valence .05 綠「鎖定·保留」。
- ② 種 goal(refA target valence .90) + deliverable v1 .80 / v2 .88 → v1Δ .10、v2Δ .02、收斂 ✓。
- **Commit**：`git commit -m "[Phase 6] alignment 比對頁: tab ③ 多ref分歧 + ② 版本收斂"`

---

## 驗收資料種法（reviewer 用，curl 直灌獨立庫）
起 server 後，對 session s1 / level L1 灌：
- 客戶 perceived（兩 ref）：refA {valence .90, tension_direction .45, emotional_warmth .25, world_immersion .90}、refB {.85,.60,.80,.90}
- 客戶 target（refA）：{valence .95, tension_direction .30, emotional_warmth .40, world_immersion .90}
- 音效師 perceived（refA）：{valence .85, tension_direction .55, emotional_warmth .50, world_immersion .80}
- deliverable（song）v1 perceived {valence .80...}、v2 perceived {valence .88...}
（POST /api/alignment/readings，欄位照契約。）

## P3 完成定義
1. agent-browser 跑過四個 tab 驗收、版面與 mockup 2 一致。
2. `pytest -k alignment` 仍綠（前端不應動到後端）。
3. annotations.db 未被碰。
4. push 分支（PR #28 自動納入），**不 merge**。

## Self-Review 結果
- **Spec §6 覆蓋**：§6.1 共用結構→各 Task；§6.2 四 tab→API 對應 T2/T3；§6.3 主 ref 選擇器→T2(④)/T3(②)；§6.4 Δ 配色→共用 deltaBadge；§6.5 判讀模板→各 tab 判讀框。§8 定位對照＝API body 構造依據。
- **Placeholder**：方向文字規則、收斂 ✓/✗ 規則已給具體條件，非 TODO。
- **型別一致**：`Identity` 八欄與 P1 一致；convergence/variance body 欄位與 P1 schema 對齊；`valueOf(...)` helper 在 T2 定義、T3 沿用。
