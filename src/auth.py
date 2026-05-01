"""Phase 6 — Authlib OAuth client 工廠 + email→annotator_id 推導。

`make_oauth(settings)` 只在 OAUTH_ENABLED=true 時呼叫；dev / test 模式整個檔可不被 import，
authlib 也就不會在 OAuth 關閉的環境是硬性 dep（雖然 pyproject 已列入）。
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from src.config import Settings

if TYPE_CHECKING:
    from authlib.integrations.starlette_client import OAuth  # noqa: F401


# annotator_id 只允許小寫英數 + 底線；其餘字元做 sanitize
_SAFE_ID_RE = re.compile(r"[^a-z0-9_]+")


def _sanitize_annotator_id(raw: str) -> str:
    """轉成只含 [a-z0-9_] 的 id，避免 URL / DB 出現怪字。

    - 全部 lower
    - 非合法字元換成 `_`
    - 連續 `_` 合併
    - 兩端 strip `_`
    - 空字串 fallback 為 `unknown`
    """
    cleaned = _SAFE_ID_RE.sub("_", raw.lower())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned or "unknown"


def email_to_annotator_id(email: str, settings: Settings) -> str:
    """email → annotator_id 規則：

    1. lookup `EMAIL_TO_ANNOTATOR_JSON` map（lowercased）
    2. 不在 map 中 → 取 `@` 之前的 local part
    3. 兩種來源都 sanitize 過（只留 [a-z0-9_]，連續 `_` 合併）
    """
    if not email:
        return "unknown"
    key = email.strip().lower()
    mapped = settings.email_to_annotator.get(key)
    if mapped:
        return _sanitize_annotator_id(mapped)
    local_part = key.split("@", 1)[0]
    return _sanitize_annotator_id(local_part)


def is_admin(email: str | None, settings: Settings) -> bool:
    """大小寫不敏感比對 admin_emails。空 email → False。"""
    if not email:
        return False
    return email.strip().lower() in settings.admin_emails


def make_oauth(settings: Settings):  # type: ignore[no-untyped-def]
    """註冊 Google OAuth provider。oauth_enabled=False 時不該呼叫。

    回 Authlib `OAuth` 物件，已註冊 `google` provider；上層用 `oauth.google` 取。
    """
    if not settings.oauth_enabled:
        raise RuntimeError(
            "make_oauth() 不該在 OAUTH_ENABLED=false 時被呼叫"
        )
    # 只有在這條 path 才 import authlib，避免 dev / test 沒裝套件就掛
    from authlib.integrations.starlette_client import OAuth

    oauth = OAuth()
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        server_metadata_url=(
            "https://accounts.google.com/.well-known/openid-configuration"
        ),
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth
