# 系統架構（一頁版）

> 最後更新：2026-05-01
> 給 Amber 與未來工程師，2 分鐘掌握全貌。

## 拓樸

```
[員工瀏覽器]
     │  HTTPS
     ▼
[Cloudflare DNS + Access (OTP)]
     │  proxied (橘雲)，邊緣擋未授權 email
     ▼
[VPS  152.42.226.237  Ubuntu 24.04 / 2 vCPU / 2 GB / 60 GB]
     │
     ├── nginx (host, apt) :443  ── SSL termination (Let's Encrypt, 2026-07-29 到期 auto-renew)
     │       └─ proxy_pass 127.0.0.1:8000
     │
     ├── docker compose
     │       ├── app          FastAPI + uvicorn :8000
     │       │   └─ mount /var/lib/polan-annotator/data → /app/data
     │       └── litestream   sidecar，跟 app 共用 volume
     │
     └── ufw  443 only allows Cloudflare IP ranges
                                    │
                                    ▼
[Cloudflare R2  bucket: polan-annotator-backup]
   即時 stream（~1 秒 lag），SQLite WAL 增量備份
```

外圍：UptimeRobot 每 5 分鐘 ping `/`，Sentry 收 backend exception，GitHub Actions 在 push to master 時 19 秒內 deploy 完。

## 三大服務各做什麼

- **app（FastAPI）**：HTTP API + 靜態頁。處理 annotation upsert、export、calibration、ICC dashboard。SQLite single-writer，session-per-request。
- **nginx**：SSL 終結、反向代理、`client_max_body_size 100M`、access log。在 host 不在 compose（見下方理由）。
- **litestream**：跟 app sidecar，watch `data/annotations.db`，把 WAL frames 即時推到 R2。災難復原時 pull WAL 即可重建到秒級。

## 設計取捨

### 為什麼 SQLite 不用 Postgres

- 標註是 **single-writer multi-reader** workload（一個人標一筆，他人純讀）
- 規模上限 200 標註員、< 100 萬筆 row — SQLite 跑得比 Postgres 快
- 備份痛點 Litestream 解掉了（流式、零維護、point-in-time recovery）
- 換 Postgres 多一個 service、多一份 ops 負擔，沒帶來新價值

升級 trigger：concurrent writer > 1（多人同時標**同一檔**）或 row > 1000 萬。當前場景都還很遠。

### 為什麼 Cloudflare Access 不自己寫 OAuth

- **員工不必有 Gmail**（OTP 寄到任何 email，公司信也行）
- Free plan 含 50 users，目前 3 人離天花板很遠
- 邊緣擋未授權，攻擊面比起自寫 OAuth callback 小一個量級
- 零維護：白名單在 CF dashboard 改，不用 redeploy

代碼裡仍保留 dormant 的 Google OAuth（`OAUTH_ENABLED=false`），萬一 CF Access 故障可作 fallback。

### 為什麼 nginx 在 host 不在 compose

- 這台 VPS 規劃之後跑其他 service（POC、staging、其他 polan 工具），共用同一個 nginx 反向代理多 subdomain 比較乾淨
- certbot --nginx auto-renew 在 host 上設定一次，新加 domain 直接 `--expand`
- compose 內如果再起一份 nginx 要 host port mapping，會跟 host 那份打架

代價：nginx 設定變更要 SSH 動 `/etc/nginx/sites-available/`，沒有版控。當前頻率低（< 1 次/月），可接受。

## 資料流

```
員工填表 → POST /api/annotations → SQLite upsert → WAL frame → Litestream → R2
                                       ▲
                                       │ scan
GET /api/export/dataset.json ──────────┘ aggregate is_complete=true → JSON
```

## CI / CD

```
git push origin master
   ↓
GitHub Actions deploy.yml
   ├── checkout
   ├── ssh VPS：cd /opt/polan-annotator && git pull
   ├── docker compose build app
   ├── docker compose up -d app litestream
   └── smoke test：curl https://annotate.dolcenforte.com/ → 200
總時間 ~19 秒
```

失敗會 email reborn.uidesigner@gmail.com。

## 備份與恢復

- Litestream 持續推 WAL 到 R2，lag ~1 秒
- 災難演練：`litestream restore -o /tmp/recovered.db s3://polan-annotator-backup/db`，可指定 `-timestamp` 拿任何過去時間點
- VPS 整台爆掉 → 開新 droplet → restore DB + rsync audio → CF DNS 改 IP → 30 分內重上線

audio 檔（~30 GB）目前**沒**進 Litestream，重建要靠原始上傳檔或 rsync from Amber 機器。長期方案是另跑 rclone 同步 audio 到 R2，但 MVP 不上。

## 既有 repo 結構（簡化）

```
polan-annotator/
├── src/                    FastAPI app
├── static/                 vanilla HTML + JS + Tailwind CDN
├── deploy/
│   ├── nginx/              annotate.dolcenforte.com.conf
│   └── litestream.yml
├── .github/workflows/      deploy.yml
├── docker-compose.yml      app + litestream
├── Dockerfile              multi-stage, non-root
└── docs/                   本資料夾
```
