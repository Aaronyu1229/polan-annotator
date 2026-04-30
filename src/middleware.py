"""Phase 6 — FastAPI auth dependency。

`require_auth` 是統一入口，支援三種 auth 模式（依優先序）：
1. **Cloudflare Access**（CLOUDFLARE_ACCESS_ENABLED=true）— 由 Cloudflare 邊緣
   驗證身分後注入 `Cf-Access-Authenticated-User-Email` header。app 信任此 header
   並 derive annotator_id。**前提**：domain DNS 設為 Cloudflare proxied (橘雲)
   且 Zero Trust Application 已 gating 該 domain；ufw 應限制 443 只接受 Cloudflare
   IP，避免 direct-IP 偽造 header。
2. **Session OAuth**（OAUTH_ENABLED=true）— 由本 app 內建的 Google OAuth flow
   設定 session['user']。
3. **Dev / single-user**（兩者皆 false）— 從 query string `?annotator=` 取，
   無 query 預設 'amber'（與 Phase 1-5 行為一致）。

CLOUDFLARE_ACCESS_ENABLED 與 OAUTH_ENABLED 同時 true 時，**Cloudflare 優先**
（邊緣已驗證，無需再走 session）。

回傳 dict 統一 shape：
    {
      "annotator_id": str,
      "email": str | None,       # dev 模式為 None
      "is_admin": bool,
      "name":  str | None,
    }
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Query, Request, status

from src.auth import email_to_annotator_id, is_admin
from src.config import Settings, load_settings

# Cloudflare Access 在 proxied 流量上注入的 header
CF_EMAIL_HEADER = "cf-access-authenticated-user-email"


def _get_settings(request: Request) -> Settings:
    """從 app.state 拿 settings；未設置則 fallback 即時 load。

    main.py 會在 startup 時 attach `app.state.settings`；這裡的 fallback
    讓單元測試直接 import middleware 也能跑。
    """
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        settings = load_settings()
    return settings


def _dev_mode_user(annotator: str | None) -> dict[str, Any]:
    """OAUTH_ENABLED=false 的回傳。預設 annotator='amber'（與 Phase 5 行為一致）。

    `is_admin=True` 是刻意決定 — dev / 單機模式沒有 ALLOWED_EMAILS / ADMIN_EMAILS
    白名單，若把 admin 設為 False，admin-only 功能（如音源上傳）在本機就無法測試。
    Production 走 OAuth 分支，admin 仍嚴格依 `ADMIN_EMAILS` env 判斷，不受影響。
    詳見 PHASE6_DEPLOYMENT.md「dev 模式 admin 行為」段。
    """
    annotator_id = (annotator or "amber").strip() or "amber"
    return {
        "annotator_id": annotator_id,
        "email": None,
        "is_admin": True,
        "name": None,
    }


def _cf_user_from_request(request: Request, settings: Settings) -> dict[str, Any] | None:
    """從 Cloudflare Access header 解析 user；無 header 回 None（caller 決定 401）。"""
    email = (request.headers.get(CF_EMAIL_HEADER) or "").strip().lower()
    if not email:
        return None
    return {
        "annotator_id": email_to_annotator_id(email, settings),
        "email": email,
        "is_admin": is_admin(email, settings),
        "name": None,
    }


def optional_annotator(
    request: Request,
    annotator: str | None = Query(default=None),
) -> str | None:
    """**選擇性**取當前 annotator_id，給原本 Optional[annotator] 的 endpoint 用。

    優先序同 `require_auth`：CF Access → session OAuth → dev query string。
    無法解析時回 None（不 raise）— 路由視情況拒絕。
    """
    settings = _get_settings(request)

    if settings.cloudflare_access_enabled:
        cf_user = _cf_user_from_request(request, settings)
        if cf_user is not None:
            return cf_user["annotator_id"]
        # CF 開了但 header 缺：可能是 health check / 內部呼叫 → 不阻擋
        return None

    if not settings.oauth_enabled:
        return annotator.strip() if annotator and annotator.strip() else None

    session = getattr(request, "session", None)
    if session is None:
        return None
    user = session.get("user")
    if not user or not isinstance(user, dict):
        return None
    return user.get("annotator_id")


def require_auth(
    request: Request,
    annotator: str | None = Query(default=None),
) -> dict[str, Any]:
    """FastAPI dependency — 解析當前使用者。

    優先序：Cloudflare Access header → session OAuth → dev query string。
    """
    settings = _get_settings(request)

    # ── Cloudflare Access 模式 ──
    if settings.cloudflare_access_enabled:
        cf_user = _cf_user_from_request(request, settings)
        if cf_user is None:
            # 邊緣應該擋掉所有未驗證流量；走到這裡通常是 direct-IP 攻擊或設定錯誤
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="尚未登入（缺 Cloudflare Access header）",
            )
        return cf_user

    if not settings.oauth_enabled:
        return _dev_mode_user(annotator)

    # ── OAuth 模式 ──
    session = getattr(request, "session", None)
    if session is None:
        # SessionMiddleware 沒裝（不該發生）— 視為未登入
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="尚未登入",
        )

    user = session.get("user")
    if not user or not isinstance(user, dict):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="尚未登入",
        )

    email = (user.get("email") or "").strip().lower()
    if not email or email not in settings.allowed_emails:
        # 白名單外的 email（例如後台移除了）→ 清 session 強制重新登入
        try:
            session.clear()
        except Exception:  # noqa: BLE001 — clear 不該失敗，但別阻斷流程
            pass
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="此 email 未獲授權",
        )

    return {
        "annotator_id": user.get("annotator_id") or "unknown",
        "email": email,
        "is_admin": bool(user.get("is_admin", False)),
        "name": user.get("name"),
    }
