# 月費與付款帳號

> 最後更新：2026-05-01
> 給 Amber，每月燒多少、誰刷卡，一張表看完。

## 當前月費

| 服務 | 月費（USD） | 付款人 Gmail | 備註 |
|------|------:|------|------|
| DigitalOcean droplet（Singapore, 2 vCPU / 2 GB / 60 GB） | $18 | reborn.uidesigner@gmail.com | 唯一硬成本 |
| Cloudflare R2（備份儲存） | $0 | reborn.uidesigner@gmail.com | Free tier 10 GB；目前用 < 1 GB |
| Cloudflare Access（OTP 登入） | $0 | reborn.uidesigner@gmail.com | Free plan 50 user 內免費 |
| Sentry（error tracking） | $0 | reborn.uidesigner@gmail.com | Developer plan 5K events / month |
| UptimeRobot（5 分鐘 ping） | $0 | reborn.uidesigner@gmail.com | Free plan 50 monitors |
| GitHub（private repo + Actions） | $0 | reborn.uidesigner@gmail.com | Free 2000 min / month CI |
| **每月合計** | **~$18** | | 約 NT$580 |
| `dolcenforte.com` 網域 | ~$10 / **年** | polanmusic2025@gmail.com | Amber 自己的 Cloudflare |

## 何時會升級

當前 droplet 配足以撐到下列任一 trigger 才需動：

- **員工數 > 5**：concurrent users 多、可能要升 4 GB RAM（$24/mo）
- **音檔總量 > 50 GB**：60 GB disk 開始緊，加 Volume（$0.10/GB/mo）或升大 droplet
- **跑 ICC 大量 batch 計算**：CPU 吃緊，升到 4 vCPU（$48/mo）
- **R2 用量 > 10 GB**：超過 free tier，每 GB $0.015/mo（10 倍量也才 $1.5）
- **Sentry events > 5K/mo**：開始有 user error 量再煩惱

換句話說，現在到 5 人團隊、~50 GB 音檔之間都是 $18/mo 平躺。

## 萬一 Aaron 退場：billing 轉給 Amber

3 個服務需要轉 ownership，其餘都是免費的可以直接重建。

### 1. DigitalOcean droplet

最關鍵，因為 prod 跑在這裡。流程：

1. Aaron 登入 DigitalOcean → Settings → Team → Invite Member → 用 polanmusic2025@gmail.com 邀請 Amber 為 Owner
2. Amber 接受邀請、加自己的信用卡到 Billing
3. Aaron 把 polanmusic2025 升為 Owner，自己降為 Member 或退出
4. （可選）Amber 把信用卡設為 default、Aaron 卡移除

**不要直接刪 droplet 重建** — 會掉 SSH key、IP，要重設 DNS、CF 白名單、SSL。

### 2. GitHub repo `Aaronyu1229/polan-annotator`

1. Aaron Settings → Collaborators → Add Amber as **Admin**
2. 確認 Amber 能看 Actions / Secrets
3. （長期）Aaron 用 Settings → General → Transfer ownership 整個 repo 移到 Amber 的 GitHub 帳號（GitHub Actions secrets 會保留，但 deploy SSH key 要重對）

### 3. 網域 `dolcenforte.com`

**已經在 Amber 的 Cloudflare 帳號**，不需轉。Aaron 只是用 API token 改 DNS，移除 token 即斷開。

### 4. 其他免費服務

- **Cloudflare Access / R2**：Aaron 退場前先把 polanmusic2025 加為 Cloudflare account member with **Super Administrator** role
- **Sentry / UptimeRobot**：直接重註冊新帳號接上即可，配置 5 分鐘搬完

### 切換清單（給 Aaron 退場那天）

- [ ] DO droplet 轉 Amber 為 Owner
- [ ] GitHub repo 轉 Amber 或加 Admin
- [ ] Cloudflare account 加 Amber Super Admin
- [ ] 把 `.env` 從 1Password / 安全管道交給 Amber
- [ ] 把 SSH private key（VPS 用）交給 Amber
- [ ] 約 1 小時 walk-through：登入各家 dashboard、看 logs、做一次 manual deploy

最壞情況 Aaron 直接消失，Amber 只要付 DO 卡費 + 重簽 SSH key 就能維持服務跑。
