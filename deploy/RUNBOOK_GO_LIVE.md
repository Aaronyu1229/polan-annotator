# Phase 6 Go-Live Runbook

> 這份是 **Aaron 上線時的逐步操作手冊**。
> 配套的設計文件見 [PHASE6_DEPLOYMENT.md](../PHASE6_DEPLOYMENT.md)。
> 所有 code 已寫好在 `phase6-cloud-deployment` branch；剩下的全是 GUI / SSH 操作。

---

## 1. 跑 VPS 診斷（5 分鐘）

開終端機，貼這行：

```bash
ssh root@68.183.232.52 "echo '=== disk ==='; df -h /; echo '=== docker ==='; docker ps; echo '=== ports ==='; netstat -tlnp 2>/dev/null | grep -E ':(80|443|8000|8001|8002)' || ss -tlnp | grep -E ':(80|443|8000|8001|8002)'; echo '=== os ==='; uname -a; echo '=== nginx ==='; which nginx && nginx -v 2>&1"
```

把輸出貼給我。我要看：
- 磁碟剩多少（音檔會占 ~500MB）
- 既有 docker 服務名單（有沒有名稱衝突）
- port 8000 / 8001 / 8002 有沒有被占用 → 決定 app 內部 port
- nginx 是 host 服務還是另一個 container

---

## 2. 在 GCP Console 建 OAuth Client（10 分鐘，你的帳號）

1. 開 https://console.cloud.google.com/apis/credentials
2. 確認左上專案是 `southern-surge-492604-s8`（你的 owner 帳號 reborn.uidesigner@gmail.com）
3. 上方 **+ CREATE CREDENTIALS** → **OAuth client ID**
4. 第一次會要求你 configure consent screen：
   - User type: **External**
   - App name: `珀瀾標註工具`
   - User support email: `reborn.uidesigner@gmail.com`
   - Developer contact: 同上
   - Scopes: 加 `.../auth/userinfo.email` + `.../auth/userinfo.profile` + `openid`
   - Test users: 加 `reborn.uidesigner@gmail.com` + `polanmusic2025@gmail.com`（+ 之後員工的 Gmail）
   - **不**要送出 verification（Internal scope 不需要）
5. 回 Credentials → Create OAuth client ID：
   - Application type: **Web application**
   - Name: `polan-annotator-prod`
   - **Authorized JavaScript origins**: `https://annotate.dolcenforte.com`
   - **Authorized redirect URIs**: `https://annotate.dolcenforte.com/auth/callback`
6. 建好後跳出的 Client ID + Client Secret 複製下來，給我貼進 VPS 的 `.env`

---

## 3. 在 Cloudflare 開 R2 bucket（5 分鐘）

1. 登入 Cloudflare → 左側 **R2 Object Storage**
2. **Create bucket**：name = `polan-annotator-backup`，location = APAC
3. 進 bucket → 右上 **Settings** → 拷貝 **S3 API endpoint**（形如 `https://<account>.r2.cloudflarestorage.com`）
4. 回 R2 首頁 → **Manage R2 API Tokens** → **Create API Token**
   - Token name: `litestream-polan-annotator`
   - Permissions: **Object Read & Write**
   - Specify bucket: `polan-annotator-backup`
   - TTL: forever
5. 建好後 Access Key ID + Secret Access Key **只會顯示一次**，貼到安全地方，給我貼進 `.env`

---

## 4. 設 DNS A record（5 分鐘）

要先確認你的網域 `dolcenforte.com` 的 DNS 在哪管：
- 如果 nameserver 是 Cloudflare → 在 Cloudflare → DNS → Records 加 A record
- 如果在 Namecheap / GoDaddy → 在那邊管理介面加

加一筆：

| Type | Name | Value | Proxy / TTL |
|------|------|-------|------|
| A | `annotate` | `68.183.232.52` | DNS only（Cloudflare 灰雲）/ TTL Auto |

**注意**：如果你用 Cloudflare proxy（橘雲），certbot 會抓不到憑證 — **第一次先設灰雲**，certbot 跑成功後再改回橘雲。

設好後等 5 分鐘，跑：
```bash
dig annotate.dolcenforte.com +short
# 應該回 68.183.232.52
```

---

## 5. 員工 Gmail 列表

把 Amber 員工的 Gmail 列給我，我會幫你寫進 `.env` 的 `ALLOWED_EMAILS` 跟 `EMAIL_TO_ANNOTATOR_JSON`。

範例格式：

```
員工 1: bob@gmail.com → annotator_id: bob
員工 2: charlie123@gmail.com → annotator_id: charlie
員工 3: ...
```

---

## 6. 我做：實際 deploy（你給齊上面 5 項後 1-2 小時）

我會：
1. SSH 進 VPS、安裝 Docker（如果沒裝）、clone repo 到 `/opt/polan-annotator`
2. 寫 `/opt/polan-annotator/.env`（用你給的所有值）
3. `docker compose build` + `docker compose up -d`
4. 跑 certbot 拿 Let's Encrypt 憑證
5. 把 `deploy/nginx/annotate.dolcenforte.com.conf` 加進 host nginx（或新 container），reload
6. 開 GitHub repo → Settings → Secrets and variables → Actions，加：
   - `VPS_HOST=68.183.232.52`
   - `VPS_USER=root`
   - `VPS_SSH_KEY=<你的 private key>` — 你給我，我設好後刪本機紀錄
7. 在你電腦上 `git push origin main` 觸發 GitHub Actions，驗 auto-deploy 成功
8. 開瀏覽器登入 `https://annotate.dolcenforte.com`、用你的 Gmail 完成 OAuth flow、確認 `/api/me` 回 admin
9. 上傳 1-2 個 .wav 測 `/upload` 頁，標 1 筆測 annotation 流程
10. 把 PR `phase6-cloud-deployment → main` 開出來給你 review，merge

---

## 7. 切換日（你指定的時間）

跑：
```bash
bash scripts/migrate_from_amber.sh amber@<Amber-Mac-IP-or-Tailscale>
```

**前提**：Amber 已收工、按過存檔、關掉本地 server。

腳本會：rsync 她 Mac 的 DB + audio → 上傳到 VPS → 重啟 container → 印給她的 LINE 訊息。

---

## 8. 上線後（你不用再做的事）

| 工作 | 怎麼處理 |
|------|---------|
| 加員工 | 改 `.env` `ALLOWED_EMAILS` + `EMAIL_TO_ANNOTATOR_JSON` → push 即自動 deploy |
| 加音源 | Amber 自己上 `https://annotate.dolcenforte.com/upload` 拖檔 |
| 改 bug | git push main 自動部署 |
| DB 備份 | Litestream 即時 stream 到 R2，零維護 |
| 監控 | UptimeRobot 設一個 5 分鐘 ping，掛了寄你 email |
| Error tracking | （可選）開 Sentry free tier，貼 DSN 進 `.env` |

---

## 你現在要做的事 — 簡化清單

- [ ] 跑第 1 步那個 SSH 診斷指令，把輸出貼給我
- [ ] 走完第 2 步 GCP OAuth client，把 Client ID + Secret 給我
- [ ] 走完第 3 步 Cloudflare R2，把 endpoint + key + secret 給我
- [ ] 走完第 4 步 DNS，跑 `dig` 確認 A record 解析正確
- [ ] 把員工 Gmail 列表給我

五項齊了，我就 deploy。
