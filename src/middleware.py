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
from sqlmodel import Session, select

from src.annotators_loader import AnnotatorsConfigError, get_annotator
from src.auth import email_to_annotator_id, is_admin
from src.cf_jwt import JwtVerificationError, verify_jwt
from src.config import Settings, load_settings
from src.models import Annotation

# Cloudflare Access 在 proxied 流量上注入的 header
CF_EMAIL_HEADER = "cf-access-authenticated-user-email"
CF_JWT_HEADER = "cf-access-jwt-assertion"


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
    """從 Cloudflare Access header 解析 user；無 header 回 None（caller 決定 401）。

    若 settings.cloudflare_access_team_domain 與 cloudflare_access_aud 都設好 →
    強制驗證 `Cf-Access-Jwt-Assertion`，使用 claims['email'] 為權威值
    （忽略 plaintext email header，JWT 簽章更可信）；缺 JWT 或驗證失敗 → 401。

    否則 fallback 為信任 `Cf-Access-Authenticated-User-Email` header（依賴 ufw IP 限制）。
    """
    jwt_required = bool(
        settings.cloudflare_access_team_domain and settings.cloudflare_access_aud
    )

    if jwt_required:
        token = (request.headers.get(CF_JWT_HEADER) or "").strip()
        if not token:
            # CF 邊緣應該已注入；走到這裡多半是 direct-IP 攻擊或設定錯誤
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="尚未登入（缺 Cloudflare Access JWT）",
            )
        try:
            claims = verify_jwt(
                token,
                team_domain=settings.cloudflare_access_team_domain,
                aud=settings.cloudflare_access_aud,
            )
        except JwtVerificationError as e:
            # 不把 verifier 內部訊息直接吐給 client（避免洩漏實作細節）
            # 但 server log 留 debug 用
            import logging

            logging.getLogger("polan.middleware").warning(
                "Cloudflare JWT 驗證失敗：%s", e
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="JWT 驗證失敗：來源未通過 Cloudflare Access",
            ) from e

        email = (claims.get("email") or "").strip().lower()
        if not email:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="JWT 驗證失敗：claims 缺 email",
            )
        return {
            "annotator_id": email_to_annotator_id(email, settings),
            "email": email,
            "is_admin": is_admin(email, settings),
            "name": claims.get("name"),
        }

    # ── Fallback：僅信任 email header（搭配 ufw IP 限制）──
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
        # JWT 強制模式下 _cf_user_from_request 缺 header / 驗證失敗會 raise；
        # optional 語意是「不阻擋」，故吞掉 401 改回 None
        try:
            cf_user = _cf_user_from_request(request, settings)
        except HTTPException:
            return None
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


# ─── Phase 8：team formalization — annotator access gate ──────────────────

# 訊息獨立常數方便測試斷言比對
ARCHIVED_ANNOTATOR_MSG = "此標註員帳號已封存，請聯絡 Amber 重啟。"
PENDING_NEED_CALIBRATION_MSG = (
    "你尚未通過校準。請先標完 Amber 已 is_complete 的音檔，"
    "完成後請 Amber 在 Dashboard 點「認可校準通過」解鎖全部音檔。"
)


def enforce_annotator_access(
    annotator_id: str,
    audio_id: str,
    session: Session,
) -> None:
    """Phase 8 access gate — 給 POST /api/annotations 與 GET /api/audio/{id} 共用。

    狀態判斷（讀 data/annotators_config.json）：
        archived              → 403 一律拒
        pending_calibration   → 只能存取 Amber 已 is_complete 的音檔（calibration set）
        active                → 通過
        annotator 不在 config → 通過（向後相容歷史 annotator_id，如 'guest'）

    刻意不在 config 缺項時 raise — 避免 'guest' 等舊資料突然 403 衝擊產線。
    新人(vvgosick) 必然會被加進 config，所以實際擋的只有 archived / pending 兩種。
    """
    try:
        spec = get_annotator(annotator_id)
    except AnnotatorsConfigError:
        # 設定檔壞了 — fail-open 避免阻斷產線；錯誤已被 loader logging
        return

    if spec is None:
        return  # 向後相容歷史 annotator_id

    status_value = spec.get("status")
    if status_value == "archived":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=ARCHIVED_ANNOTATOR_MSG,
        )

    if status_value == "pending_calibration":
        # 是否在 calibration set（Amber 已 is_complete 標過）
        amber_done = session.exec(
            select(Annotation).where(
                Annotation.audio_file_id == audio_id,
                Annotation.annotator_id == "amber",
                Annotation.is_complete == True,  # noqa: E712
            )
        ).first()
        if amber_done is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=PENDING_NEED_CALIBRATION_MSG,
            )
