# Amber 管理指南

> 最後更新：2026-05-01
> 給 Amber，日常運作不用問 Aaron。

## 我能做 vs 必須找 Aaron

| 任務 | Amber 自己 | 要找 Aaron |
|------|:---:|:---:|
| 加 / 移除員工（Cloudflare 白名單） | ✓ | |
| 把員工升為 admin（可上傳音檔） | | ✓ |
| 上傳新音檔到 `/upload` | ✓ | |
| 匯出資料集（curl 或瀏覽器） | ✓ | |
| 看 dashboard（ICC、進度） | ✓ | |
| 改 `dimensions_config.json` 維度文字 | | ✓ |
| 重啟系統（恢復連線） | | ✓ |
| 處理收到的 ⚠️ 維度反饋 | ✓ | |

理由：admin email 寫在 VPS 的 `.env` 裡，動到要重新部署；維度設定檔改錯系統會啟動失敗，要 Aaron 在 staging 驗一次比較安全。

## 加新員工

員工不需要 Gmail 之外的東西，CF Access 會用 OTP 寄到他的信箱。

1. 員工把要登入的 Gmail 給你
2. 你登入 [Cloudflare Zero Trust](https://one.dash.cloudflare.com/)（用你 polanmusic2025@gmail.com 那個帳號）
3. 左側選單 → **Access** → **Applications** → 點 `polan-annotator`
4. 切到 **Policies** tab → 編輯 `Allow polan team` policy → **Include** → **Emails** 區塊把員工 Gmail 加進去 → **Save**
5. （非 admin 到此為止）LINE 員工：「打開 https://annotate.dolcenforte.com 用你給我的 Gmail 登入，會收到 OTP」
6. **若要給 admin 權限**：LINE Aaron 說「請把 xxx@gmail.com 加進 ADMIN_EMAILS」，他會改 env 並 redeploy（19 秒）

員工生效時間：CF 那邊存檔即生效，不必等。

## 移除員工

同上路徑，把 email 從 **Emails** 列表移掉 → Save。下次他開網址會被 CF 擋下，已經登入的 session 在 OTP 過期時也會失效（最多 30 天，要立刻砍可以同時 Logout all sessions）。

如果他是 admin，也要請 Aaron 從 ADMIN_EMAILS 移除。

## 上傳新音檔

1. 進 `https://annotate.dolcenforte.com/upload`（admin 才看得到 nav 上「上傳」）
2. drag-and-drop 或選檔，每檔 ≤ 100 MB
3. 上傳完會 auto-rescan，標註首頁會出現

支援格式：`.wav` `.mp3` `.ogg` `.m4a` `.flac`。

### 檔名規則（重要）

Parser 認得兩種格式，名字符合才能 auto-suggest 時序位置：

**(A) 兩段式**（一般博弈音樂）：
```
{Game Name}_{Stage}.wav
```
範例：`Volcano Goddess_Base Game.wav`

合法 Stage：`Base Game` / `Free Game` / `Bonus Game` / `Main Game` / `Winning Panel`

**(B) 三段式品牌主題曲**：
```
Game Brand Theme Music_{品牌名}_AI Virtual Voice.wav
```
範例：`Game Brand Theme Music_金鑫_AI Virtual Voice.wav`

不符合上面兩種規則的檔名會用 fallback 處理（前段當 game name），但**標註頁不會 auto-suggest stage**，員工要自己選。建議檔名統一以免混淆。

## 匯出資料

3 個 endpoint，直接在瀏覽器網址列貼即可下載 JSON，或用 curl：

```bash
# 完整資料集（多人標註合併共識值）— 賣給買方的主要交付
curl https://annotate.dolcenforte.com/api/export/dataset.json -o dataset.json

# 校準集（只含 amber 的標註，給新人練習比對）
curl https://annotate.dolcenforte.com/api/export/calibration_set.json -o calibration.json

# 個別標註員的所有完成標註（{id} 換成 amber、aaron 等）
curl "https://annotate.dolcenforte.com/api/export/individual.json?annotator=amber" -o amber.json
```

只會匯出 `is_complete=true` 的標註。沒有任何完成標註時 `dataset.json` 的 `items` 會是空陣列，但 `total_audio_files` 仍計入。

## 看進度與 ICC

`https://annotate.dolcenforte.com/dashboard`

- **每位標註員完成數 / 總檔數** — 看誰標到哪
- **跨標註員 ICC**（需要至少 2 人標過同一檔）— 一致性指標：
  - emotion + function 類維度：門檻 **0.7**（綠燈），主觀允許較寬鬆
  - acoustic 類維度：門檻 **0.85**（綠燈），客觀必須嚴格
  - 紅燈 = 該維度兩人歧異大，需要校準討論
- **skipped_dimensions** 區塊：`loop_capability` 是離散多選，不算 ICC（這是預期行為）

DB 還沒有第二位真實標註員時，dashboard 顯示「尚無跨標註員資料」。

## 改維度定義

`src/dimensions_config.json` 是 10 個維度的單一資料來源。**改文字 / 錨定範例**會影響員工怎麼解讀，是 Amber 的權責沒錯，但流程一定要走 Aaron：

1. LINE Aaron：「我想把 Emotional Warmth 的 high anchor 改成 XXX」
2. Aaron 在 staging 驗一次（JSON 格式錯會讓 server 啟動失敗）
3. Aaron push → 19 秒後生效
4. 員工重整頁面看到新文字

**不要直接登進 VPS 改檔**，下一次 auto-deploy 會 reset hard 把你的修改蓋掉。
