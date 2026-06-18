# BGM 對齊模式 — 設計規格

> 狀態：confirmed（負責人 + Aaron 對齊完成 2026-06-18）
> 作者：負責人（UX / 維度設計）＋ Aaron（工程修正）
> 前置必讀：本 repo `CLAUDE.md`（尤其規則 #6 不引入框架、#8 不改 Amber 的 dimensions_config 定義文字）

---

## 0. 一頁總覽

這是一套**客戶端對齊工具**：客戶聽我們給的參考曲（ref），照維度標數值，我們據此做新曲並逐版收斂。

跟現有「賣給 AI 新創的資料集標註」是**兩件不同的事**，**獨立儲存、互不污染**。

| 決策 | 結論 | 來源 |
|---|---|---|
| 客戶每維標兩個值（perceived + target） | ✅ 要 | 負責人確認 #1 |
| BGM 對齊 vs 內部資料集 | 不同事件 | 負責人確認 #2 |
| BGM 資料進不進要賣的資料集 | **不進**，且**獨立 .db 檔** | 負責人確認 #3 + 「字面也分開」 |
| BGM 錨點 vs Amber 的 SFX 錨點 | 附加新版，**不覆寫** | 負責人確認 #4 |

---

## 1. 範圍與隔離原則（最重要）

- 既有系統（三角校準 creator/industry/audience、ICC/CCC、`Annotation` 表、export / consensus / agreement / quality_flags）**一行都不動**。
- BGM 對齊資料存在**獨立資料庫檔** `data/alignment.db`，與 `data/annotations.db` 檔案層級分離。
- BGM 對齊的數值**永不**進入任何要賣的資料集 pipeline。
- emotional_warmth 這格在 SFX 模式 = 情緒溫度（冷暖），BGM 模式重新詮釋為「柔烈度」。**正因兩種語意衝突，才必須實體隔離**——否則資料集語意污染、賣相打折。

---

## 2. 資料模型（工程核心 — 這是新表，不是 config 切換）

> ⚠️ 負責人原稿寫「不新增維度欄位、沿用既有欄位」。這對「不新增*維度*」為真，對「不新增*欄位/表*」為**假**。現有 `Annotation` 一個 `(audio_file_id, annotator_id)` 只有一 row、每維一個 float、且有 `UniqueConstraint`，無法存 perceived + target 雙值。故實際做法是新增獨立表。

### 2.1 新表 `AlignmentReading`

一筆 reading = 某標註者對某音源在某時點、針對某維度的**單一值**。雙值靠**多 row**（perceived 一筆、target 一筆），不在同一 row 塞兩欄。

```
AlignmentReading
  id              : int PK
  session_id      : str     # 哪次合作 / 哪個關卡（新）
  annotator_role  : str     # "engineer" | "client"（新；與既有 creator/industry/audience 無關）
  audio_id        : str     # 可重用既有 AudioFile.id
  audio_role      : str     # "ref" | "deliverable"（新）
  version         : int     # ref=0；新曲 v1/v2/v3...（新）
  dimension       : str     # valence | tension_direction | emotional_warmth | world_immersion
  value           : float   # 0.00–1.00
  reading_type    : str     # "perceived" | "target"
  note            : str | None
  created_at      : datetime
```

命名警告：
- **不要叫 `value_type`** — 既有 `Arbitration.value_type` 是序列化型別（`"float"|"list_str"|"list_float"`），同名不同義必撞。用 `reading_type`。
- `annotator_role` 用 `engineer|client`，不要複用既有 `role`（creator/industry/audience 是內部校準角色）。

### 2.2 獨立 .db 檔（連檔案都分開）

SQLModel 用**單一全域 `metadata`**。若只是把 `AlignmentReading` 宣告成 `table=True`，`db.py` 的 `create_all(engine)` 會把它一起建進 `annotations.db` → 隔離失敗。

正確做法：**獨立 engine + 獨立 metadata**。

- 新檔 `src/alignment_db.py`：自己的 `MetaData()`（或獨立 SQLModel registry）、自己的 `create_engine("sqlite:///data/alignment.db")`、自己的 `create_alignment_db()` / `get_alignment_session()`。
- `AlignmentReading` 綁這個獨立 metadata，**不註冊**到 `SQLModel.metadata`，確保 `annotations.db` 永遠沒有這張表。
- 啟動時兩個 `create_*_db()` 各自跑，互不影響。

### 2.3 風格標籤另存
`world_immersion` 的**滑桿數值**存進 `AlignmentReading`（進數值比對）；其下的**風格標籤**另存（多選、當補充，**不進數值比對**）。BGM 模式可重用既有 style 標籤庫，但寫入 alignment 側，不寫 `Annotation.style_tag`。

---

## 3. 維度與錨點（依音檔類型切換，附加不覆寫）

### 3.1 `AudioFile.audio_type`（新欄位）
`AudioFile` 加 `audio_type: "bgm" | "sfx"`（預設 sfx，向後相容）。BGM 對齊模式只對 `audio_type == "bgm"` 的音源套用本規格的顯示。

### 3.2 dimensions_config 附加 bgm 變體（守 CLAUDE.md #8）
- Amber 的 SFX 版定義 / low_anchor / high_anchor **完全不動**。
- 新增 BGM 變體 block（依 audio_type 讀不同顯示），且要補 **mid_anchor**（負責人要低/中/高三段，現況只有 low/high）。

### 3.3 BGM 版四條感受滑桿（連續值 0.00–1.00，雙值）
客戶版 UI 只顯示提示文字，不顯示音色術語。

| # | 維度（底層欄位） | BGM 顯示名 | 低錨 (0.0–0.2) | 中 (~0.5) | 高錨 (0.85–1.0) | 客戶版問句 |
|---|---|---|---|---|---|---|
| 1 | valence | 情緒正負向 | 陰暗、壓迫、緊繃、憤怒 | 中性平穩 | 明亮、歡樂、狂喜 | 「底色是明亮歡樂，還是陰暗緊繃？」 |
| 2 | tension_direction | 張力方向 | **穩定維持、有律動但不堆疊**（main game 床最常見） | 略有起伏 | 明顯起伏推進、越來越緊（觸發前 build-up） | 「穩穩鋪著，還是一直往上推？」 |
| 3 | emotional_warmth | **柔烈度** | 柔：暖呼呼、圓潤、溫柔的喜慶 | 柔中帶亮 | 烈：又亮又衝、鏗鏘炸裂的熱鬧 | 「偏柔暖，還是偏明亮衝勁？」 |
| 4 | world_immersion | 世界沉浸感 | 通用、無國界（generic casino） | 有主題色彩、混搭為主 | 濃烈鮮明的特定世界觀 | 「主題濃度多強？一聽就知道是哪個世界嗎？」 |

- 維度 2 重點：SFX 版低錨是「放鬆釋放」，BGM 版改「**穩定維持**」——博弈 BGM 的 dynamic 是維持，一進遊戲體感就到位。main game 床標到高端（一直推）會讓玩家累，標高時 UI 可提醒客戶確認。
- 維度 3：取代 SFX 的「情緒溫度」，改判斷感受結果（柔 vs 衝），不要客戶判斷音色冷暖。

### 3.4 BGM 模式隱藏 arousal / event_significance
靠 `audio_type` **條件隱藏**，不是刪維度（博弈 BGM dynamic 維持不需 arousal；event_significance 偏 SFX/結算）。

---

## 4. 世界觀 / 風格附加標籤
接在世界沉浸感滑桿下方。**只能點選、不可自由輸入**（自由填會收到無法執行的詞）。可多選。

建議選項：electronic · celtic · orchestral · modern_pop · trap · jazz · lofi · chinese_traditional · epic · ambient · kpop · world · fantasy · cute · realistic · asian_mythology · horror · cyberpunk · western · racing · mystery · japanese · undersea · festival

語意：沉浸感滑桿管「保留多少主題」，標籤管「ref 沒有、但客戶想額外加的調味」。

---

## 5. 規格區（勾選，非滑桿，獨立區塊）
UI 與感受滑桿視覺分開（上面憑感覺滑、下面直接勾）。

- **循環方式（單選）**：`loop`（無縫循環）/ `one_shot`（一次性）。
- **長度（單選，唯一真正新增的 enum）**：`loop_length: 15 | 30 | 60`（秒）。上限 60s（檔案越大 loading 越久）。15s 常見於大獎/特殊音樂、常搭 one_shot → **先問循環方式、再問長度**。

> loop_length 存在 alignment 側（隨 reading 的 session/audio 走），不寫既有 `Annotation`。既有 `loop_capability`（多選 list[float]）不沿用——語意不同（多選 vs 單選），BGM 用自己的單選欄位。

---

## 6. 比對引擎（一個引擎、四種比對 — 不做四個畫面）

核心：**不要做四個比對畫面**。做一個「挑 A、挑 B → 顯示每維差距」引擎；四種需求只是 A/B 選不同兩筆 reading。

一筆 reading 的身分由這些標籤定位：`session_id` · `annotator_role` · `audio_id`+`audio_role` · `version` · `reading_type`。

| 比對 | 按住不變 | 只變動 | 判讀 |
|---|---|---|---|
| 1 音效師 vs 客戶 | 同 ref、perceived | annotator_role | 差距＝認知落差 |
| 2 第一版 vs 第二版 | 同新曲、同一人 | version | 差距＝改版收斂幅度 |
| 3 同關卡不同 ref | 同一人、perceived | audio_id | **變異**（見下） |
| 4 聽到值 vs 預期值 | 客戶、同 ref | reading_type | 差距＝要往哪調多少 |

兩個判讀規則（UI 務必體現）：
1. **一次只變一個軸**。不可讓「音效師 ref-A perceived」比「客戶 ref-B target」（四軸全變、差距無意義）。UI 限制成一次只換一軸，或至少警示。
2. **比對 3 是相反讀法**。比對 1/2/4 看「差距」（越小越好 / 越收斂）；比對 3 看「變異」——多首 ref 裡**穩定**的維度＝客戶在意、鎖定（保留項）；**飄動**的維度＝可自由發揮。同資料、不同讀法。

整體流程：
- 開工前：比對 1（對齊認知）＋ 比對 3（鎖定保留項）＋ 比對 4（知道往哪調）。
- 開工後：比對 2，每版 vs 客戶期望，逐版收斂。

---

## 7. 真正要新增的東西（誠實清單）

1. `src/alignment_db.py` — 獨立 engine + metadata，指向 `data/alignment.db`。
2. `AlignmentReading` 表（綁獨立 metadata）。
3. `AudioFile.audio_type`（既有表加欄，走 `src/migrations.py` idempotent ALTER）。
4. dimensions_config 的 bgm 錨點變體 + `mid_anchor`（附加，不覆寫 SFX）。
5. `loop_length` 單選 enum（alignment 側）。
6. 比對引擎（讀 `AlignmentReading`，輸出每維差距/變異）。
7. BGM 對齊標註 UI（雙值滑桿 + 風格標籤 + 規格區）。

---

## 8. 先不要做（避免 over-build）

- 不做 AI 預標、品質 gate、CCC/ICC、JSON 匯出（這套不進資料集，本就不需要）。
- 風格標籤不開自由輸入、不做後台標籤編輯器。
- 維度錨點寫死在 config，不做後台維度編輯器。
- 不為「未來多客戶/多 session」提前建抽象——照 CLAUDE.md，MVP 為先。

---

## 附：兩首 ref 測試落點（Aaron 自測 UI 用）

| 維度 | 過年喜慶 ref | 舞龍舞獅 ref | 意義 |
|---|---|---|---|
| 情緒正負向 valence | ~0.9 | ~0.85 | 兩首一致 → 保留項 |
| 張力方向 tension_direction | ~0.45 | ~0.6 | 略分歧 → 需確認 |
| 柔烈度 emotional_warmth | ~0.25（柔） | ~0.8（烈） | 最大分歧 → 對齊重點 |
| 世界沉浸感 world_immersion | ~0.9 | ~0.9 | 兩首一致 → 保留項 |
