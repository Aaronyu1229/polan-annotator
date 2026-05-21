"""Phase 6 — `/login` `/logout` `/auth/callback` `/api/me` 路由。

- `GET  /login`           OAuth 關閉 → 顯示 dev 提示頁；OAuth 開 → 302 到 Google
- `GET  /auth/callback`   接 Google 回呼，存 session，跳回 `/`
- `POST /logout`          清 session，跳回 `/login`
- `GET  /api/me`          回當前 user（OAuth 關 → annotator_id from query；OAuth 開 → from session）
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from src.auth import email_to_annotator_id, is_admin
from src.config import Settings
from src.middleware import _get_settings, require_auth

router = APIRouter(tags=["auth"])
log = logging.getLogger("polan.routes.auth")


# ─── 小 UI helpers ────────────────────────────────────────

def _html_page(title: str, body: str, status_code: int = 200) -> HTMLResponse:
    """簡易 HTML 殼 — 不引入 framework，跟主介面 Tailwind CDN 一致。"""
    html = f"""<!DOCTYPE html>
<html lang="zh-Hant">
<head>
  <meta charset="UTF-8">
  <title>{title} — 珀瀾標註工具</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-50 text-slate-900 dark:bg-slate-900 dark:text-slate-100 min-h-screen flex items-center justify-center p-6">
  <main class="max-w-md w-full bg-white dark:bg-slate-800 rounded-lg shadow-sm border border-slate-200 dark:border-slate-700 p-6">
    {body}
  </main>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=status_code)


# ─── /login ───────────────────────────────────────────────

@router.get("/login", include_in_schema=False)
async def login(request: Request) -> Response:
    settings: Settings = _get_settings(request)

    # CF Access 模式: 真正的閘道在 Cloudflare,使用者該透過 OTP email 連結進來,
    # 不該看到「dev 模式」叫他加 `?annotator=` query string (在 CF Access 模式無效)。
    if settings.cloudflare_access_enabled:
        body = """
        <h1 class="text-xl font-semibold mb-3">請從 OTP 信件登入</h1>
        <p class="text-sm text-slate-600 dark:text-slate-300 mb-5">
          本工具由 Cloudflare Access 把關。要進入請收 Amber 發給你的 OTP 驗證信,
          照信中連結登入。沒有信請聯絡 Amber。
        </p>
        <a href="/" class="inline-block px-4 py-2 bg-amber-500 hover:bg-amber-600 text-white rounded font-medium">嘗試進入 →</a>
        """
        return _html_page("登入", body)

    if not settings.oauth_enabled:
        body = """
        <h1 class="text-xl font-semibold mb-3">登入（dev 模式）</h1>
        <p class="text-sm text-slate-600 dark:text-slate-300 mb-4">
          OAuth 在此環境停用。請在網址後加 <code class="px-1 bg-slate-100 dark:bg-slate-700 rounded">?annotator=你的id</code> 直接使用。
        </p>
        <a href="/" class="inline-block px-4 py-2 bg-amber-500 text-slate-900 rounded font-medium">回主頁</a>
        """
        return _html_page("登入", body)

    oauth = getattr(request.app.state, "oauth", None)
    if oauth is None:
        # OAuth 開啟但 client 沒註冊 — 視為設定錯
        log.error("OAuth 啟用中但 app.state.oauth 不存在")
        raise HTTPException(status_code=500, detail="OAuth client 未初始化")

    redirect_uri = settings.oauth_redirect_uri
    return await oauth.google.authorize_redirect(request, redirect_uri)


# ─── /logged-out ──────────────────────────────────────────
#
# CF Access logout URL 的 returnTo target。CF 清完 cookie 後會把使用者導到這頁。
#
# ⚠️ 部署 ops 須知: 需在 Cloudflare Zero Trust → Access → Applications →
# annotate.dolcenforte.com → Policies 加一條 Bypass policy 對 path `/logged-out`,
# 否則使用者回到這頁時 CF 又會挑戰 OTP, 反而把人放回登入狀態,登出 UX 失敗。

@router.get("/logged-out", include_in_schema=False)
def logged_out_page() -> Response:
    """CF Access logout returnTo 的「你已登出」確認頁。"""
    body = """
    <h1 class="text-xl font-semibold mb-3">你已登出 ✓</h1>
    <p class="text-sm text-slate-600 dark:text-slate-300 mb-5">
      已從 Cloudflare Access 登出。要重新進入,請按下方連結並收 OTP 驗證信。
    </p>
    <a href="/" class="inline-block px-4 py-2 bg-amber-500 hover:bg-amber-600 text-white rounded font-medium">重新登入 →</a>
    <p class="text-xs text-slate-400 mt-4">或直接關閉這個分頁。</p>
    """
    return _html_page("已登出", body)


# ─── /auth/callback ───────────────────────────────────────

@router.get("/auth/callback", include_in_schema=False)
async def auth_callback(request: Request) -> Response:
    settings: Settings = _get_settings(request)

    if not settings.oauth_enabled:
        # callback 在 dev 模式下不該被 hit
        return RedirectResponse(url="/login", status_code=302)

    oauth = getattr(request.app.state, "oauth", None)
    if oauth is None:
        raise HTTPException(status_code=500, detail="OAuth client 未初始化")

    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception as e:  # noqa: BLE001 — Authlib raises various
        log.warning("OAuth callback 失敗：%s", e)
        body = """
        <h1 class="text-xl font-semibold mb-3 text-red-600">登入失敗</h1>
        <p class="text-sm text-slate-600 dark:text-slate-300 mb-4">
          Google 授權流程中斷或被拒絕。請重試。
        </p>
        <a href="/login" class="inline-block px-4 py-2 bg-amber-500 text-slate-900 rounded font-medium">重新登入</a>
        """
        return _html_page("登入失敗", body, status_code=400)

    userinfo = token.get("userinfo") if isinstance(token, dict) else None
    if not userinfo:
        # 部分情況需手動 fetch
        try:
            resp = await oauth.google.get(
                "https://openidconnect.googleapis.com/v1/userinfo",
                token=token,
            )
            userinfo = resp.json()
        except Exception as e:  # noqa: BLE001
            log.warning("無法取得 userinfo：%s", e)
            raise HTTPException(status_code=400, detail="無法取得使用者資訊") from e

    email = (userinfo.get("email") or "").strip().lower()
    name = userinfo.get("name") or userinfo.get("given_name") or email

    if not email:
        raise HTTPException(status_code=400, detail="Google 未回傳 email")

    if email not in settings.allowed_emails:
        body = f"""
        <h1 class="text-xl font-semibold mb-3 text-red-600">未獲授權</h1>
        <p class="text-sm text-slate-600 dark:text-slate-300 mb-2">
          你的 email 尚未獲授權，請聯絡管理員。
        </p>
        <p class="text-xs text-slate-500 dark:text-slate-400 mb-4">
          已嘗試登入：<code class="px-1 bg-slate-100 dark:bg-slate-700 rounded">{email}</code>
        </p>
        <a href="/login" class="inline-block px-4 py-2 bg-slate-200 hover:bg-slate-300 dark:bg-slate-700 dark:hover:bg-slate-600 text-slate-800 dark:text-slate-100 rounded font-medium">回登入頁</a>
        """
        # 拒絕未授權 email 不該寫進 session
        return _html_page("未獲授權", body, status_code=403)

    annotator_id = email_to_annotator_id(email, settings)
    request.session["user"] = {
        "email": email,
        "name": name,
        "annotator_id": annotator_id,
        "is_admin": is_admin(email, settings),
    }
    return RedirectResponse(url="/", status_code=302)


# ─── /logout ──────────────────────────────────────────────

@router.post("/logout", include_in_schema=False)
def logout(request: Request) -> Response:
    """清 session 並真正登出。POST only,避免 CSRF / 預載行為觸發登出。

    流程依模式:
    - OAuth 模式 (有 SessionMiddleware): 清 app session → redirect /login。
    - CF Access JWT 模式 (有 team_domain): redirect 到 CF 官方 logout URL,
      讓 CF 清掉自己的 cookie,再 returnTo `/logged-out` 確認頁。**這條才會真正登出**,
      只回 /login 等於沒登出(CF cookie 還在,重整就會回來)。
    - CF Access header-only / dev 模式 (無 team_domain): fallback redirect /login。
    """
    settings = _get_settings(request)
    if "session" in request.scope:
        request.session.clear()

    if (
        settings.cloudflare_access_enabled
        and settings.cloudflare_access_team_domain
        and settings.app_domain
    ):
        team = settings.cloudflare_access_team_domain
        return_to = f"https://{settings.app_domain}/logged-out"
        return RedirectResponse(
            url=f"https://{team}/cdn-cgi/access/logout?returnTo={return_to}",
            status_code=302,
        )

    return RedirectResponse(url="/login", status_code=302)


# ─── /api/me ──────────────────────────────────────────────

@router.get("/api/me")
def get_me(user: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
    """回當前使用者 + Phase 8 annotators_config 的 profile / status。

    給前端 auth.js 用:401 → redirect、200 → 顯示 email/logout、
    pending_calibration → 觸發 welcome modal + 引導至校準頁。
    """
    from src.annotators_loader import get_annotator, AnnotatorsConfigError  # noqa: PLC0415
    enriched = dict(user)
    try:
        spec = get_annotator(user.get("annotator_id", ""))
    except AnnotatorsConfigError:
        spec = None
    if spec is not None:
        enriched["annotator_profile"] = spec.get("annotator_profile")
        enriched["status"] = spec.get("status")
        enriched["display_name"] = spec.get("name")
    return enriched
