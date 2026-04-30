"""Phase 6 — FastAPI auth dependency。

`require_auth` 是統一入口：
- OAUTH_ENABLED=false（dev / test）→ 從 query string `?annotator=` 取，
  保留 Phase 1-5 的測試用法；無 query 預設 `amber`（既有 dev UX）。
- OAUTH_ENABLED=true（prod）→ 從 `request.session["user"]` 取。
  - 沒 session：raise 401 — HTML route 自行 catch 後 redirect 到 /login
  - email 不在白名單：清 session，raise 403
  - 通過：回 user dict

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

from src.config import Settings, load_settings


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


def optional_annotator(
    request: Request,
    annotator: str | None = Query(default=None),
) -> str | None:
    """**選擇性**取當前 annotator_id，給原本 Optional[annotator] 的 endpoint 用。

    - dev：query `?annotator=` 直接回（None 也允許 → 不做 filter）
    - prod：session["user"]["annotator_id"]；無 session 回 None（路由視情況拒絕）

    跟 `require_auth` 的差別是 prod 模式下不 raise 401 — 由路由自己決定如何處理 None。
    這個 helper 只給 list / get-by-id 這類「無 annotator 也能回資料」的 endpoint。
    """
    settings = _get_settings(request)
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

    - dev：query `?annotator=` → annotator_id
    - prod：session["user"] → annotator_id（query 被忽略以避免偽造）
    """
    settings = _get_settings(request)

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
