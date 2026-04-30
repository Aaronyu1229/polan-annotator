# CLAUDE.md

> 這個檔案在 `polan-annotator/` 根目錄。
> Claude Code 啟動時會自動讀取它，建立整個 session 的 convention。
> 你（Claude Code）做任何事都必須遵守這份 convention。

---

## 專案背景

珀瀾聲音 Pōlán Sound — 遊戲音效情緒標註 AI 資料集新創。

這個 repo 是**內部音效標註工具**（不是對外產品），給創辦人 Amber 自己標 33 個 BGM 種子作品用。標完後這批資料會成為：
1. 招募外部標註員時的「黃金校準範例」
2. 賣給 AI 新創公司（ElevenLabs / Hume AI / Inworld AI）的訓練資料集雛形

**Phase 1-4 的定位：**
- Phase 1：專案骨架 + 資料模型
- Phase 2：標註介面核心（UX 重點）
- Phase 3：校準模式 + ICC 計算（**因 Q6 對齊文件確認 100% 雙人標註，本 phase 改為 Phase 2 完成後立即做**）
- Phase 4：資料匯出 + validation

---

## Code Convention

### Python

- Python 3.11+
- **4 space indent**（不用 tab）
- 所有 public function 加 type hints
- Pydantic model 用於 API schema，SQLModel 用於 DB schema
- Exception 要抓具體型別（`except ValueError:` 而非 `except:`）
- 盡量用 `pathlib.Path` 而非 `os.path`

### JavaScript（前端）

- **2 space indent**
- ES Modules（`import` / `export`）
- **不加分號**（依 Prettier default: `semi: false`）
- 用 `const` 為主，需要 reassign 才用 `let`，**不要用 `var`**
- fetch API 為主，不要引入 axios 等外部 HTTP lib

### 檔案/資料夾命名

- Python file：`snake_case.py`
- JS file：`kebab-case.js`
- 前端 HTML：`kebab-case.html`
- 資料夾：`snake_case`
- 環境變數：`UPPER_SNAKE_CASE`

### Commit 訊息

格式：`[Phase N] 簡短描述`

範例：
- `[Phase 1] 建立 FastAPI 骨架 + dimensions_config loader`
- `[Phase 2] 實作滑桿 UI + WaveSurfer 整合`
- `[Phase 2] 修復 Space 鍵在 textarea focus 時被攔截的 bug`
- `[Phase 4] 加上 consensus aggregation 邏輯`

多個修改可以分多次 commit，不要把 Phase 2 跟 Phase 3 的東西混在一個 commit。

---

## UI / 文案 convention

- 所有 **使用者可見文字**用**繁體中文**
- 所有 **code identifier**（變數、函數、class、檔名）用**英文**
- 文案用 **sentence case**，不要官腔。例如用「儲存」不用「點選此處進行儲存」。
- **不要加不必要的動畫 transition**，尤其是滑桿值變化、chip 選中狀態。標註員一次操作幾小時，動畫會讓介面感覺遲鈍。
- **error 訊息要具體**。不要「儲存失敗，請稍後再試」，要「儲存失敗：第 3 維度 (Emotional Warmth) 值 1.2 超出範圍 0-1」。

---

## 絕對不要做的事

1. **不要 auto-install 套件**，要裝新的先停下來跟 Aaron 確認。每個 phase 的 prompt 都會明確列出允許的 stack。
2. **不要 over-engineer**。這是 MVP，不是要賣給 Fortune 500 的企業系統。
3. ~~**不要加 user authentication / OAuth**~~ → **Phase 6 起改為：必須 Google OAuth + email 白名單**（見下方 Phase 6 段）
4. ~~**不要寫 Docker / CI/CD / GitHub Actions**~~ → **Phase 6 起改為：必須 Docker + GitHub Actions**（見下方 Phase 6 段）
5. **不要加動畫、splash screen、音效、Toast 通知**（除非 prompt 明確要）。
6. **不要用 React / Vue / Svelte / Next.js**，這個 repo 用 vanilla HTML + JS + Tailwind CDN。
7. **不要把 33 個音檔名稱 hardcode**，永遠從 `data/audio/` 動態掃描。
8. **不要修改 `dimensions_config.json` 的定義文字**讓它「更精確」—— Amber 會自己改，這是她的權責。
9. **不要修改已有測試資料**（`data/annotations.db`）除非 prompt 明確同意。
10. **不要改其他 Phase 的 code**。做 Phase 2 時只動 Phase 2 相關檔案，做 Phase 4 時只動 Phase 4。跨 phase 的修改要先講。

---

## Phase 6（cloud deployment）覆寫規則

Phase 1-5 是單機 MVP；Phase 6 把工具搬上 VPS、加多人協作能力。**Phase 6 範圍內**以下 MVP 規則作廢：

- ✅ **可以**用 Docker / docker-compose
- ✅ **可以**寫 GitHub Actions deploy workflow
- ✅ **必須**加 Google OAuth + email 白名單（員工會直接打開網址登入，不再用 `?annotator=amber`）
- ✅ **可以**新增的 Python 套件：`authlib` / `itsdangerous` / `python-multipart` / `python-dotenv` / `sentry-sdk[fastapi]`

**仍然不變**：
- 不引入 React/Vue/Next（前端維持 vanilla + Tailwind CDN）
- 不換 DB（SQLite + Litestream 即可，不用 Postgres）
- 不引入 Redis / Celery / Kubernetes 等重型基建
- 既有 API 行為維持向後相容（annotator_id 既支援從 session 拿、也支援 query string fallback）

**權威 spec**：見 [PHASE6_DEPLOYMENT.md](./PHASE6_DEPLOYMENT.md)。動 Phase 6 任何檔案前先讀那份文件。

---

## 重要的資料流與設計原則

### `dimensions_config.json` 是唯一資料來源

所有維度的定義文字、錨定範例、auto_compute 設定**都從這個 JSON 讀取**，**不要硬編碼在 Python 或 HTML 裡**。

這個設計的用意：**Amber 試標發現定義需要調整時，只需要改一個 JSON 檔、重啟 server 就生效**，不用改 code。

### `amber_confirmed: false` 的 4 個維度

當前版本有 4 個維度的定義是 Aaron 根據 Amber 給的範例推敲出來的，標記為 `amber_confirmed: false`：
- Emotional Warmth
- Tension Direction
- Event Significance
- World Immersion

**你（Claude Code）不要改這 4 個維度的 definition / low_anchor / high_anchor 文字讓它「更精確」**。Amber 在 Phase 2 試標時會自己驗收並改 JSON。

UI 必須對這 4 個維度顯示 ⚠️ icon 提醒標註員。

### localStorage draft save

- key 格式：`draft:{annotator_id}:{audio_id}`
- 時機：**只在 3 秒沒操作時**存一次，**不要**連續拖滑桿時每次都存
- 已儲存的正式 annotation **不要讀 localStorage**，優先讀 DB
- 頁面載入時檢查是否有 draft，有跳 modal 問「繼續草稿還是重新開始」

### 音檔命名 parser

音檔有兩種格式：

**(A) 兩段式**（30 個）：`{Game Name}_{Stage}.wav`
- 範例：`Volcano Goddess_Base Game.wav`
- Stage 只有 5 種：`Base Game` / `Free Game` / `Bonus Game` / `Main Game` / `Winning Panel`

**(B) 三段式**（3 個）：`Game Brand Theme Music_{品牌名}_AI Virtual Voice.wav`
- 範例：`Game Brand Theme Music_金鑫_AI Virtual Voice.wav`
- Parser 要特別處理：當偵測到前綴是 `Game Brand Theme Music` 時，`game_name = "Game Brand Theme Music"`，`game_stage = "{品牌名} (AI Virtual Voice)"`，不要把中間的品牌名當 stage，也不要讓 filename_mapping auto-suggest 套用（這三首不是典型博弈音樂，時序位置應由標註員手動選）

**Parser 實作位置：** `src/constants.py` 的 `parse_audio_filename(filename: str) -> tuple[str, str]` 函數。

---

## 如果你（Claude Code）遇到 spec 有矛盾或遺漏

**不要自己腦補解法**。

- 在 code 相關位置加 `# TODO(aaron): spec 對 X 有矛盾，我暫時 Y` 註解
- 在完成報告的「發現的 spec 問題」區塊列出來
- 讓 Aaron 決定

---

## 環境偏好

- macOS（Aaron 主要在 Mac 上跑）
- 終端機可能是 zsh 或 bash，不要寫 zsh 專屬語法
- 所有指令 `uv` 跟 `pip + venv` 兩種都要支援（README 裡兩種都列）
