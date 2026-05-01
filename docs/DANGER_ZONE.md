# 危險區（紅色警告）

> 最後更新：2026-05-01
> Amber 必看。下面每一條都是「做了會壞 prod」的操作。

每條前面 ⚠️，後面附「萬一誤觸怎麼救」。讀完這頁，碰 Cloudflare / GitHub / VPS 之前先回頭看一次。

---

## ⚠️ Cloudflare DNS 不可改回灰色雲

**為什麼**：DNS 必須是 **proxied (橘雲)**。改成 DNS only（灰雲）後，外部請求會直接走員工 IP → VPS，但 VPS 的 ufw 只開放 Cloudflare IP 範圍 → 全部請求被擋 → 系統對外 502。

**怎麼救**：登 Cloudflare → DNS → 把 `annotate.dolcenforte.com` 那筆改回橘雲 → 30 秒內恢復。

---

## ⚠️ Cloudflare SSL/TLS 模式不可從 Full (strict) 改回 Flexible

**為什麼**：VPS nginx 走 HTTPS（Let's Encrypt），CF 也對 origin 走 HTTPS = Full (strict)。改 Flexible 後 CF 用 HTTP 連 origin，但 nginx 把 HTTP 自動 301 到 HTTPS → CF 又 301 回去 → **redirect loop**，瀏覽器顯示 ERR_TOO_MANY_REDIRECTS。

**怎麼救**：CF Dashboard → SSL/TLS → Overview → 切回 **Full (strict)** → 即時恢復。

---

## ⚠️ Cloudflare Access policy 不可移除自己

**為什麼**：CF Access 是唯一登入閘道。policy 裡若不小心把 `polanmusic2025@gmail.com` 移掉並 Save，下次 OTP 過期後你也進不去 CF Dashboard 之外的任何頁，**包含這個 policy 自己**。Aaron 還能進是因為他的 email 也在白名單。

**怎麼救**：LINE Aaron，他用自己 email 登進 CF → Access → Applications → polan-annotator → Policies → 把你 email 加回去。**這就是為什麼一定要保兩位 admin email**，永遠不要只剩一個。

---

## ⚠️ 不可手動修改 VPS 上的 .env 而不告訴 Aaron

**為什麼**：auto-deploy 流程在 VPS 上跑 `git pull` 之前會 `git reset --hard origin/master` 確保 working tree 乾淨。任何在 `/opt/polan-annotator/.env` 直接 nano 的修改在下次 push 時會被蓋掉……不對，`.env` 在 `.gitignore` 不會被 git 動。**但** docker compose restart 時讀新 env，若改錯（如 ALLOWED_EMAILS 漏掉自己）會直接全員鎖外。

**怎麼救**：
- 鎖外了 → SSH 進 VPS（Aaron 還有 root SSH）→ 編 `/opt/polan-annotator/.env` → `docker compose restart app`
- 永遠的解：env 變更走 PR，不要直接編 prod。

---

## ⚠️ 不可直接 push 到 master

**為什麼**：master 一被 push 就觸發 GitHub Actions → 19 秒後 prod 已經是新 code。**沒有人 review、沒有 staging**。一個語法錯誤可能 5 分鐘下線。

**怎麼救**：永遠開 PR、CI 跑綠、Amber/Aaron review 後再 merge。誤 push 把 prod 弄壞了 → `git revert <commit>` push 上去 → 19 秒回滾。

---

## ⚠️ 不可改 dimensions_config.json 不知道在做什麼

**為什麼**：這個 JSON 是維度定義的單一資料來源。改錯（少一個 `}`、key 拼錯、`type` 改成沒在 enum 裡的字）→ FastAPI 啟動時 `dimensions_loader.py` fail-fast → 整個 service 起不來 → **prod 502**。

**怎麼救**：
- 開了會 → `git revert` 上一筆 commit → push → 19 秒恢復
- 預防：所有 dimensions 改動透過 Aaron 在 staging 驗一次。

---

## ⚠️ 不可移除 ufw 443 規則

**為什麼**：當前 ufw 只開 `443/tcp from <Cloudflare IPs>` + `22/tcp from <Aaron IP>`。若用 `ufw disable` 或 `ufw delete <rule>`，VPS 直接暴露在整個 internet 上，攻擊面從「Cloudflare 邊緣 + CF Access OTP」變成「裸 nginx + ssh」，當天就會看到 brute force 嘗試。

**怎麼救**：
- 立刻 `sudo ufw enable` + 重套 rules（看 `/etc/ufw/user.rules` 或請 Aaron 從 ansible recipe 重套）
- 預防：**完全不要碰 ufw**。需要開新 port 找 Aaron。

---

## 一般原則

- **動 prod 配置前 LINE Aaron 一句**，5 分鐘的確認省幾小時災難
- **永遠保留兩位 admin email**（Amber + Aaron），policy / .env / CF account 都是
- **看不懂的設定不要按 Save**，先截圖問
- **每次改完到 `https://annotate.dolcenforte.com` 開無痕視窗實測一次** 是不是還能登入 + 標一筆
