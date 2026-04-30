# Phase 6 — Cloud Deployment & Multi-user

> 此文件為 Phase 6 的權威 spec。Phase 1-5 是單機 MVP，Phase 6 把工具搬上 VPS、加 Google OAuth 登入、建立自動部署管線。
> Phase 6 **明確覆蓋** CLAUDE.md 中「不要 Docker / CI / OAuth」這條 MVP 期規則 —
> 該規則寫於工具還只是 Amber 自己標 33 個檔的階段，如今員工已加入、需要多人共用一份 DB，必須升級。

---

## 目標

讓 Amber + 她的員工**直接從瀏覽器登入** `https://annotate.dolcenforte.com` 標註，
不再需要 Aaron 的 Mac 開機 / cloudflared tunnel / 桌面 symlink。
設好之後 Aaron **不需介入日常運作**：
- 加員工 → 改 env 白名單 → 重新 deploy
- 加音源 → Amber 自己從後台上傳
- 改 bug → git push → 自動 deploy
- DB 備份 → Litestream 即時 stream 到 R2，零維護

---

## 架構

```
[Amber/員工瀏覽器]
        │ HTTPS
        ▼
[Cloudflare DNS] ─→ [VPS 68.183.232.52]
                          │
                  ┌───────┴────────┐
                  │  nginx :443    │  (SSL termination, certbot auto-renew)
                  └───────┬────────┘
                          │ proxy_pass
                          ▼
                  ┌────────────────┐
                  │ FastAPI :8000  │  (uvicorn in Docker)
                  │ + OAuth        │
                  │ + upload API   │
                  └───┬────────┬───┘
                      │        │
              ┌───────▼─┐    ┌─▼────────────┐
              │ data/   │    │ Litestream   │ ──→ Cloudflare R2 (即時備份)
              │ audio/  │    │ (sidecar)    │
              │ *.db    │    └──────────────┘
              └─────────┘

GitHub push to main ──→ Actions ──→ ssh VPS ──→ docker compose pull + up -d
```

---

## 已鎖決策

| 項目 | 值 | 備註 |
|------|------|------|
| 主網域 | `dolcenforte.com` | Amber 持有 |
| Subdomain | `annotate.dolcenforte.com` | 可改，code 內走 env `APP_DOMAIN` |
| VPS | DigitalOcean `68.183.232.52` | 既有，已跑 n8n / SerpBear / nika-tech，需確認 port 8000 / 8001 衝突 |
| Container runtime | Docker + docker compose | nginx 已有人在跑 → 共用 nginx 或新跑一個 reverse proxy（待 SSH 進去看） |
| Auth | Google OAuth (Authlib) + email 白名單 | session cookie，HttpOnly + Secure |
| DB | SQLite + Litestream | 不換 Postgres — SQLite 對單寫多讀夠用，Litestream 解決備份 |
| 備份目的地 | Cloudflare R2 | Aaron 已有 Cloudflare 帳號 |
| Error tracking | Sentry (free tier) | env-gated，可選 |
| Uptime | UptimeRobot (free tier) | 不需 code |
| CI/CD | GitHub Actions → SSH deploy | push main 即部署 |

### Email → annotator_id 對應

```env
ALLOWED_EMAILS=reborn.uidesigner@gmail.com,polanmusic2025@gmail.com
EMAIL_TO_ANNOTATOR_JSON={"reborn.uidesigner@gmail.com":"aaron","polanmusic2025@gmail.com":"amber"}
```

未在 `EMAIL_TO_ANNOTATOR_JSON` 的 email → 預設取 `@` 前段為 annotator_id。
新增員工：把 Gmail 加進 `ALLOWED_EMAILS` + 對應到 annotator_id 進 `EMAIL_TO_ANNOTATOR_JSON`，重啟服務即可。

---

## 待補資訊（Aaron 給齊才能完成 deploy）

- [ ] DNS 在哪管？（Cloudflare / Namecheap / GoDaddy / 其他）
- [ ] 員工 Gmail 列表（Amber 員工們的 email，N 位都列）
- [ ] VPS 診斷輸出：
  ```bash
  ssh root@68.183.232.52 "df -h / && docker ps && netstat -tlnp 2>/dev/null | grep -E ':(80|443|8000|8001|8002)' && uname -a"
  ```
- [ ] R2 bucket 名稱（建議 `polan-annotator-backup`）+ Access Key / Secret

---

## 新增檔案結構

```
polan-annotator/
├── Dockerfile                       # multi-stage, slim, non-root user
├── .dockerignore
├── docker-compose.yml               # app + litestream（nginx 走 host 上既有的 / 新建，視 VPS 狀況）
├── .env.example                     # 所有 env 列出（無實值）
├── deploy/
│   ├── nginx/
│   │   └── annotate.dolcenforte.com.conf   # reverse proxy + SSL
│   └── litestream.yml               # SQLite → R2 配置
├── .github/
│   └── workflows/
│       └── deploy.yml               # push main → ssh VPS → docker compose up
├── src/
│   ├── auth.py                      # Authlib Google OAuth client
│   ├── config.py                    # env loader (SESSION_SECRET / OAuth / whitelist)
│   ├── middleware.py                # session-based auth check
│   └── routes/
│       └── auth.py                  # /login /logout /auth/callback /me
├── scripts/
│   └── migrate_from_amber.sh        # 切換日：從 Amber Mac rsync DB + audio 到 VPS
└── PHASE6_DEPLOYMENT.md             # 本檔
```

CLAUDE.md 會加 Phase 6 章節，**不刪除** MVP 期規則（保留歷史脈絡）。

---

## 環境變數總表

| 變數 | 用途 | 範例 |
|------|------|------|
| `APP_DOMAIN` | 對外網域 | `annotate.dolcenforte.com` |
| `APP_SECRET_KEY` | session 加密 | 隨機 32 bytes |
| `GOOGLE_CLIENT_ID` | OAuth | 從 GCP console 拿 |
| `GOOGLE_CLIENT_SECRET` | OAuth | 從 GCP console 拿 |
| `OAUTH_REDIRECT_URI` | OAuth callback | `https://annotate.dolcenforte.com/auth/callback` |
| `ALLOWED_EMAILS` | 白名單，逗號分隔 | `aaron@gmail.com,amber@gmail.com` |
| `EMAIL_TO_ANNOTATOR_JSON` | email → annotator_id 映射 | `{"a@b.com":"aaron"}` |
| `R2_ACCESS_KEY_ID` | Litestream | Cloudflare R2 token |
| `R2_SECRET_ACCESS_KEY` | Litestream | Cloudflare R2 token |
| `R2_BUCKET` | Litestream | `polan-annotator-backup` |
| `R2_ENDPOINT` | Litestream | `https://<account>.r2.cloudflarestorage.com` |
| `SENTRY_DSN` | 可選 | 從 Sentry 拿 |

---

## 切換日流程（Amber 機器 → VPS）

```bash
# Aaron 在自己 Mac 上跑（前提：Amber 已收工、按過存檔、關掉本地 server）
bash scripts/migrate_from_amber.sh amber@<Amber-Mac-Tailscale-or-IP>
```

腳本會：
1. SSH 進 Amber Mac → 確認 server 已停（`pgrep uvicorn` 應該空）
2. `rsync` `data/annotations.db` 到 Aaron Mac 暫存
3. `rsync` `data/audio/` 到 Aaron Mac 暫存
4. `scp` 兩者到 VPS `/var/lib/polan-annotator/data/`
5. `ssh` 到 VPS → `docker compose restart app`
6. 印出 ✅ 完成 + 給 Amber 的 LINE 文字模板（網址 + 登入步驟）

之後 Amber 機器**廢棄**（local server 不再啟動），所有人改用網址。

---

## Phase 6 開發 Convention（補充 CLAUDE.md）

- **新加 Python 套件需在這裡列出**：`authlib`, `itsdangerous`, `python-multipart`, `python-dotenv`, `sentry-sdk[fastapi]`
- **不引入 Postgres / Redis** — SQLite + 本機 session 夠用
- **OAuth 不影響既有 API 行為** — 既有 endpoint 維持原樣，加 middleware 在前面攔；annotator_id 從 session 拿，不再從 query string（但保留 query string fallback 給 dev / test）
- **音檔上傳要走 admin** — 員工只能標、不能上傳新檔；admin 角色用 email 判斷（whitelist 中標 `is_admin=true` 的 email）
- **Docker image 不含 data/** — `data/` 是 volume，從 host mount 進去；image 重 build 不影響資料
- **Litestream sidecar mode** — 跟 app 同 compose，shared volume；app 寫 SQLite、Litestream 自動 stream 到 R2

---

## 預計時程

| 階段 | 內容 | 估時 |
|------|------|------|
| Day 1 上午 | Docker stack + nginx + GitHub Actions（infra code） | 2-3 hrs |
| Day 1 下午 | OAuth 整合 + 白名單 middleware + login/logout 頁 | 3-4 hrs |
| Day 2 上午 | 上傳音源 UI + admin 判斷 + Litestream | 2-3 hrs |
| Day 2 下午 | VPS 實機部署 + DNS + SSL + 煙霧測試 | 2-3 hrs |
| Day 3 | 切換日：rsync Amber 資料 + 通知 | 1 hr |

**Aaron 手動環節**：DNS 加 A record、GCP console 建 OAuth client、Cloudflare R2 開 bucket、VPS SSH key 設定。其餘自動化。
