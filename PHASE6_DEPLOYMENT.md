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

## 已鎖決策（**最終實際部署**，2026-05-01 update）

| 項目 | 值 | 備註 |
|------|------|------|
| 主網域 | `dolcenforte.com` | Amber 持有，DNS 在 Cloudflare |
| Subdomain | `annotate.dolcenforte.com` | proxied (橘雲) |
| VPS | DigitalOcean `152.42.226.237` (SGP1) | **新開的 droplet，2 vCPU / 2GB / 60GB Ubuntu 24.04**；非共用既有那台 |
| Container runtime | Docker + docker compose | nginx 在 host (apt install)，proxy_pass 到 127.0.0.1:8000 |
| Auth | **Cloudflare Access (OTP)** + email 白名單 | 取代原計畫的 Google OAuth；team `polanyu.cloudflareaccess.com`，Free plan |
| Auth defense-in-depth | JWT 驗證 (`src/cf_jwt.py`) + ufw 限制 443 為 CF IP | JWT env 暫未啟用；ufw 已限 |
| DB | SQLite + Litestream（待設） | 不換 Postgres |
| 備份目的地 | Cloudflare R2 (待建 bucket) | Aaron 已有 Cloudflare 帳號 |
| Error tracking | Sentry (free tier) | env-gated `SENTRY_DSN`，待填 |
| Uptime | UptimeRobot (free tier) | 待設 5min ping |
| CI/CD | GitHub Actions → SSH deploy | workflow 寫好；secrets 待加 |
| Log rotation | Docker json-file，10MB × 5 / service | 已設 |
| SSL | Let's Encrypt via certbot --nginx | 自動續，到 2026-07-29 |
| Internal Google OAuth code | 保留 dormant（`OAUTH_ENABLED=false`） | 留作 CF Access 失敗時 fallback |

### Email → annotator_id 對應

```env
ALLOWED_EMAILS=reborn.uidesigner@gmail.com,polanmusic2025@gmail.com
EMAIL_TO_ANNOTATOR_JSON={"reborn.uidesigner@gmail.com":"aaron","polanmusic2025@gmail.com":"amber"}
```

未在 `EMAIL_TO_ANNOTATOR_JSON` 的 email → 預設取 `@` 前段為 annotator_id。
新增員工：把 Gmail 加進 `ALLOWED_EMAILS` + 對應到 annotator_id 進 `EMAIL_TO_ANNOTATOR_JSON`，重啟服務即可。

---

## 待補資訊（已完成 ✓ / 待 Aaron 提供 ☐）

- [x] DNS：Cloudflare proxied
- [x] 員工 Gmail：第一位 `yyslin1024@gmail.com` 已加 Cloudflare 白名單；後續員工同樣作法
- [x] 新 VPS provisioned + 已交付 `https://annotate.dolcenforte.com`
- [ ] **Cloudflare R2 bucket 名稱 + Access Key / Secret**（給我即可設 Litestream 即時備份）
- [ ] GitHub Actions secrets：`VPS_HOST` / `VPS_USER` / `VPS_SSH_KEY`
- [ ] (選) Sentry DSN
- [ ] (選) UptimeRobot 帳號 + 加 monitor `https://annotate.dolcenforte.com`
- [ ] (選) Cloudflare Application AUD Tag → 啟用 JWT 驗證（多一層 defense in depth）

### 啟用 JWT 驗證（之後想做時）

Aaron 在 Zero Trust Dashboard → Access → Applications → polan-annotator → Overview 找到 **AUD Tag**（hex 字串），把以下兩行寫進 VPS `.env`：

```
CLOUDFLARE_ACCESS_TEAM_DOMAIN=polanyu.cloudflareaccess.com
CLOUDFLARE_ACCESS_AUD=<從 Dashboard 複製>
```

Restart：`docker compose restart app`。之後每個請求都會驗 JWT 簽章 + audience claim。

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

### dev 模式 admin 行為（刻意）

`OAUTH_ENABLED=false` 時 `require_auth` 會回 `is_admin=True`。理由：
- dev / 單機本沒有 `ALLOWED_EMAILS` / `ADMIN_EMAILS`
- 若 dev `is_admin=False`，則 admin-only 功能（上傳音源）在本機完全無法測試
- production 走 OAuth 分支，`is_admin` 仍嚴格依 `ADMIN_EMAILS` env 判斷，dev 開放不影響線上安全

實作見 `src/middleware.py` 的 `_dev_mode_user`。如需在 dev 模擬非 admin 行為（例如測 403），用 `app.dependency_overrides[require_auth]` 注入一個回 `is_admin=False` 的 fake（見 `tests/test_audio_upload.py`）。

### 音源上傳 API（Phase 6 已實作）

- `POST /api/audio/upload` — multipart/form-data，單檔；admin only
- `?replace=true` query 可覆蓋同名檔
- 100 MB size 上限（與 nginx `client_max_body_size` 對齊）
- 檔名必須符合 `parse_audio_filename` 的兩段式（`{Game}_{Stage}.wav`）或三段式品牌主題曲；落到 fallback 分支會回 400 + 繁中具體訊息
- 寫檔走 `<filename>.uploading.tmp` 然後 `os.replace` atomic rename，避免半寫入的破檔被掃到
- 寫完後 call `scan_audio_directory` upsert 到 `AudioFile` table
- 前端：`/upload` 頁（admin nav 自動注入），drag-drop / file picker，per-file XHR + progress event

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
