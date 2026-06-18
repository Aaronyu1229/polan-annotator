# BGM 規格卡 — 原稿 vs 實作版對照

> 用途：負責人原稿（config 規格・對齊 BGM 版）與 Aaron 實作版的逐點對照，方便夥伴在 GitHub 上看「改了哪些、為什麼」。
> 權威設計文件：[2026-06-18-bgm-alignment-mode-design.md](./2026-06-18-bgm-alignment-mode-design.md)
> 狀態：後端 + UI 骨架已實作（branch `feat/bgm-alignment-mode`，425 tests 全綠）

---

## 調整對照表（原稿 → 實作版）

| # | 原稿這樣寫 | 🔧 實作改成 | 為什麼 |
|---|---|---|---|
| 1 | 「不新增欄位、不開新表」，雙值塞同一筆 | 新增 `AlignmentReading` 表，雙值靠**多 row**（perceived 一筆、target 一筆） | 既有 Annotation 一個 (audio,annotator) 只有一 row、每維一個 float、有 UniqueConstraint，塞不下雙值 |
| 2 | 資料存進既有標註庫 | 存進獨立 `data/alignment.db` 檔，與 annotations.db 實體隔離 | 負責人拍板：BGM 對齊**不進**要賣的資料集，連 .db 檔都分開 |
| 3 | enum 欄位叫 `value_type` | 改名 `reading_type` | 既有 `Arbitration.value_type` 已存在且意思不同（序列化型別），同名會撞 |
| 4 | 柔烈度欄位名寫 `soft_intensity` | 底層仍是 `emotional_warmth`，只在 bgm block 換 `display_name=柔烈度` | 不開新維度欄位（原稿第三節本意也是這樣，名稱統一） |
| 5 | 循環方式「映射既有 loop_capability 欄位」 | BGM 用自己的單選 `loop` 欄位（在 AlignmentSpec），不沿用 loop_capability | 既有 loop_capability 是多選 list[float]，語意（多選 vs 單選）不同，硬接會混 |
| 6 | 風格標籤「存進既有 style tags」 | 存進 `AlignmentSpec.style_tags`（alignment 側），白名單驗證 | 守隔離：不寫既有 Annotation.style_tag，避免污染資料集 |
| 7 | 錨點低/中/高（隱含） | config 明確加 `mid_anchor` 三段 + `client_question` | 原 config 只有 low/high 兩段，補齊中段 |
| 8 | `annotator_role: engineer\|client` | 照用，但獨立於既有 creator/industry/audience | 那套是內部三角校準角色，不混用 |

**沒改的（原稿就對，照做）**：第五節「一個引擎四種比對、不做四畫面」、「一次只變一軸」、「比對 3 看變異其餘看差距」、第七節「先不要做」清單、兩首 ref 測試落點。

---

## 實作版規格卡（🔧 = 相對原稿的調整）

### 用途
對齊模式下 BGM ref 專用維度設定。錨點全用「音樂語言」重寫（SFX 的 reel stop / UI 點擊不適用）。
🔧 **做法修正**：不是「同欄位切換顯示」那麼簡單——感受值寫獨立的 alignment 庫，與賣給 AI 新創的資料集**完全隔離、不互相污染**。

### 一、感受滑桿（4 條，0.00–1.00，每條雙值 perceived + target）
錨點文字四維原稿照收，已逐字寫進 `dimensions_config.json` 各維的 `bgm` block：
1. **情緒正負向** valence — 低:陰暗緊繃 / 中:中性平穩 / 高:明亮歡樂
2. **張力方向** tension_direction — 低:穩定維持（🔧 BGM 版低錨已重寫，非 SFX 的「放鬆釋放」）/ 中:略有起伏 / 高:越來越緊
3. **柔烈度** emotional_warmth — 低:柔暖 / 中:柔中帶亮 / 高:亮而衝（🔧 `display_name=柔烈度`，底層仍 emotional_warmth）
4. **世界沉浸感** world_immersion — 低:通用無國界 / 中:有主題色彩 / 高:濃烈特定世界觀

🔧 每維補了 **mid_anchor 中段** + **client_question**（API `GET /api/alignment/dimensions` 回給前端）。

### 二、風格附加標籤（掛在世界沉浸感下方）
只能點選、可多選、不可自由輸入（白名單 24 項，spec 第四節清單照收）。
🔧 存進 `AlignmentSpec.style_tags`（不寫既有 Annotation.style_tag）；白名單由 API 強制驗證（`GET /api/alignment/style-options`）。

### 三、規格區（勾選，與滑桿視覺分開）
- 循環方式（單選）：loop / one_shot — 🔧 BGM 自己的 `loop` 欄位，不映射既有 loop_capability
- 長度（單選，新增）：`loop_length` 15 / 30 / 60 — 先問循環、再問長度

🔧 兩者存 `AlignmentSpec`（key = session+annotator+role+audio+audio_role+version，不分 perceived/target）。

### 四、資料模型（🔧 這節相對原稿改最多）
- 🔧 **新表 `AlignmentReading`**（不是「不開新表」）：long format，一 row = 一維度一值。雙值靠 perceived/target 兩 row。
- 🔧 **獨立 `data/alignment.db`**（純 SQLAlchemy + 專屬 metadata，與主庫不共用）。
- 🔧 enum 欄位 `reading_type`（不叫 value_type）：perceived | target。
- 欄位：`(session_id, annotator_id, annotator_role, audio_id, audio_role, version, dimension, value, reading_type, note)`。
- 世界沉浸感滑桿值進 `AlignmentReading`（進比對）；風格標籤進 `AlignmentSpec`（不進比對）。

### 五、比對引擎（🔧 無修改，原稿照做）
一個「挑 A、挑 B → 每維差距」引擎，四種比對只是選不同兩筆。`differing_axes` 強制「一次只變一軸」；比對 3 用 `compute_variance` 看變異、其餘 `compare_pair` 看差距。（原稿的 value_type 那軸現為 reading_type）

### 六、維度清單（同原稿）
valence / tension_direction / emotional_warmth / world_immersion + 規格區 loop/loop_length；移除 arousal / event_significance（改條件隱藏，非刪欄位）。

### 七、先不要做（同原稿）
不做 AI 預標 / 品質 gate / CCC/ICC / 匯出 / 風格自由輸入 / 後台編輯器。

### 附：兩首 ref 測試落點（同原稿）
| 維度 | 過年喜慶 ref | 舞龍舞獅 ref | 意義 |
|---|---|---|---|
| 情緒正負向 | ~0.9 | ~0.85 | 一致 → 保留項 |
| 張力方向 | ~0.45 | ~0.6 | 略分歧 → 需確認 |
| 柔烈度 | ~0.25 | ~0.8 | 最大分歧 → 對齊重點 |
| 世界沉浸感 | ~0.9 | ~0.9 | 一致 → 保留項 |
