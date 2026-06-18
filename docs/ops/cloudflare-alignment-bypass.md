# Cloudflare Access：alignment 客戶 bypass 設定

> 目標：讓外部客戶能用 token 連結進 `/alignment`，但內部工具維持 OTP 白名單保護。

## 要加的 Bypass policy（只對這兩個 path pattern）

在 Zero Trust → Access → Applications，對 `annotate.dolcenforte.com` 既有 application：

新增一條 **Bypass** policy（或一個獨立 application，path 限定）：
- Path: `/alignment`
- Path: `/api/alignment/*`

Action: **Bypass**（Everyone）。

## 絕對不要 bypass 的 path（維持 OTP）

- `/dashboard`、`/upload`、`/annotator/*`
- `/admin/*`
- `/api/export/*`  ← 資料集本體
- `/api/stats/*`、`/api/audio/*`、`/calibration*`
- 其餘所有 path

## 驗收

1. 無痕視窗開 `…/alignment?token=<有效>` → 進得去、能播音檔、能標。
2. 同視窗（已帶 alignment cookie）直打 `…/dashboard` → 被 CF Access OTP 擋（你不在白名單的角色）。
3. 同視窗直打 `…/api/export/dataset.json` → 被 CF Access OTP 擋。
4. 撤銷該 token 後重開連結 → app 回 403。

## 原理

bypass 後 `/alignment` + `/api/alignment/*` 由 app 層 token gate（`src/client_auth.py`）把關；
token 存 hash、可撤銷、可過期。其餘 path 仍在 CF Access 後，客戶從未被加進白名單，故進不去。
