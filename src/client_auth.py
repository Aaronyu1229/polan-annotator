"""Alignment 客戶端存取把關（CF Access bypass 後的唯一鎖）。

token 只存 SHA-256 hash；明文僅在發佈當下回傳一次。dev 模式（CF + OAuth 皆關）
直接放行為信任 engineer，沿用 src/middleware.py 的 dev 哲學，讓本機/測試免帶 token。
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, UTC

from fastapi import Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.alignment_db import ClientLink, get_alignment_session
from src.middleware import _get_settings

CLIENT_COOKIE = "polan_align"
CLIENT_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 天


def set_client_cookie(response: Response, token: str) -> None:
    """把已驗過的 client token 種成 cookie（gate 與 /alignment 頁路由共用，避免參數漂移）。

    注意：FastAPI 路由直接 return FileResponse 時會丟掉 dependency 在注入 Response 上種的
    cookie，故 /alignment 頁路由必須在它「實際回傳的」FileResponse 上呼叫本函式。
    """
    response.set_cookie(
        key=CLIENT_COOKIE, value=token, httponly=True, secure=True,
        samesite="lax", max_age=CLIENT_COOKIE_MAX_AGE,
    )


def generate_token() -> str:
    """≥32 bytes 熵的 urlsafe token（明文，只在發佈當下用）。"""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 hex。DB 只存這個。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_token_hash(token: str, expected_hash: str) -> bool:
    """constant-time 比對，避免 timing 洩漏。"""
    return hmac.compare_digest(hash_token(token), expected_hash)


@dataclass(frozen=True)
class AlignmentAccess:
    """gate 解析出的存取上下文。client 三欄被鎖死；engineer 三欄為 None。"""
    role: str
    annotator_id: str | None
    session_id: str | None
    alignment_audio_id: str | None


def _link_to_access(link: ClientLink) -> AlignmentAccess:
    return AlignmentAccess(
        role=link.role,
        annotator_id=link.annotator_id,
        session_id=link.session_id,
        alignment_audio_id=link.alignment_audio_id,
    )


def resolve_alignment_access(
    request: Request,
    response: Response,
    token: str | None = Query(default=None),
    db: Session = Depends(get_alignment_session),
) -> AlignmentAccess:
    """alignment 頁與所有 /api/alignment/* 的把關依賴。

    dev 模式（CF + OAuth 皆關）→ 信任 engineer，免 token。
    否則：token 取自 ?token= 或 cookie；驗 hash + 未撤銷 + 未過期；
    首次帶 query token 時種 cookie（後續 API / 音檔自動帶）。
    """
    settings = _get_settings(request)
    if not settings.cloudflare_access_enabled and not settings.oauth_enabled:
        return AlignmentAccess(role="engineer", annotator_id=None,
                               session_id=None, alignment_audio_id=None)

    from_query = token is not None and token.strip() != ""
    raw = token.strip() if from_query else request.cookies.get(CLIENT_COOKIE)
    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺存取 token，請使用發佈的連結進入")

    link = db.scalars(
        select(ClientLink).where(ClientLink.token_hash == hash_token(raw))
    ).first()
    if link is None or not verify_token_hash(raw, link.token_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "token 無效")
    if link.revoked:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "此連結已被撤銷")
    if link.expires_at is not None:
        exp = link.expires_at if link.expires_at.tzinfo is not None else link.expires_at.replace(tzinfo=UTC)
        if datetime.now(UTC) > exp:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "此連結已過期")

    if from_query:
        set_client_cookie(response, raw)
    return _link_to_access(link)
