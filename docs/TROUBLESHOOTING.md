# 故障排除

> 最後更新：2026-05-01
> Amber 與員工都看，90% 問題自己解。

格式：**症狀 → 可能原因 → 立刻試 → 還沒好 LINE Aaron**。

## 1. 打開網址跳到 Cloudflare 但收不到 OTP

**可能原因**：信箱在垃圾信、Gmail 拼錯、CF Access policy 沒包含這個 email。

**立刻試**：
- 翻 spam / 促銷信夾，搜尋寄件人 `noreply@notify.cloudflare.com`
- 確認自己用的 Gmail 跟給 Amber 的那個一字不差
- 等 60 秒後重點「Send code again」

**還沒好**：LINE Amber 確認你的 email 在 CF Access 白名單。

## 2. 登入後白屏 / Network error

**可能原因**：剛好在自動部署的 19 秒空窗、瀏覽器快取壞掉、網路問題。

**立刻試**：
- 等 30 秒後 Cmd+R 重整
- 硬重整：Cmd+Shift+R（清快取）
- 換無痕視窗開一次

**還沒好**：截圖 console error（F12 → Console tab）發給 Aaron。

## 3. 上傳檔案出現 413 / 「檔案過大」

**原因**：上限是 100 MB，nginx 跟 app 都會擋。

**立刻試**：
- 用 ffmpeg 或 Audacity 壓成 256kbps mp3：通常 < 30 MB
- 拆成多段（但 stage 概念可能被破壞，先問 Amber）

**還沒好**：請 Amber 評估是否該升級上限（要動 nginx 設定，Aaron 出手）。

## 4. 拖播放頭時 audio 跳針 / 不順

**可能原因**：mp3 編碼 VBR 拖曳支援不好。

**立刻試**：
- 換 .wav 版同一首聽聽
- 換 Chrome 或 Edge（Safari 的 Web Audio 偶爾出怪事）

**還沒好**：截圖 + 註明檔名給 Aaron，他會看是不是檔本身的問題。

## 5. 中文檔名顯示亂碼

**原因**：瀏覽器 encoding 不是 UTF-8，或檔名是 Big5 上傳的。

**立刻試**：
- Cmd+R 重整
- View → Text Encoding → Unicode (UTF-8)

**還沒好**：把原始檔名（含路徑）貼給 Aaron，他在 VPS 上 fix。

## 6. 標完按 Enter 沒反應

**最常見原因**：function_roles 沒勾。前端會擋送出，但有時錯誤訊息被遮住。

**立刻試**：
- 滾到 function roles 區塊，至少勾一個
- 看連續維度滑桿是否有 10 個都拉過（沒拉的呈灰色）
- 看 source_type 是否有選

**還沒好**：F12 → Console，截圖紅字訊息給 Aaron。

## 7. 匯出的 JSON 是空的（`items: []`）

**原因**：還沒有任何 `is_complete=true` 的 annotation。

**立刻試**：
- 進 dashboard 看「完成數」是多少；0 就是沒標完
- 匯出前確認有人按過「儲存並下一筆」（draft 不算）

**還沒好**：Amber 看 dashboard 數字 vs DB 真實情況，找 Aaron 對。

## 8. 自動 deploy 失敗（GitHub email 通知）

**這條只給 Aaron 看 — Amber 收到請轉給他。**

**立刻試**：
1. 開 GitHub Actions 那筆 run（email 內附連結）
2. 看哪個 step 紅
   - **build 階段失敗**：Python import error / pyproject.toml 寫壞 → 本機 reproduce
   - **smoke test 失敗**：deploy 完打 `/api/health` 沒 200 → SSH 進 VPS 看 `docker compose logs app`
   - **rsync / ssh fail**：檢查 GitHub secrets `VPS_SSH_KEY` 是否還有效

**還沒好** 30 分內：手動 SSH VPS 跑 `docker compose pull && docker compose up -d`，恢復 prod 後再查 root cause。

---

## LINE Aaron 時請附

- 哪個帳號（你的 Gmail）
- 哪一頁出問題（網址）
- 看到的錯誤訊息（截圖最好）
- 重現步驟（一二三）

這 4 樣齊全，9 成可以遠端解，省你一個來回。
