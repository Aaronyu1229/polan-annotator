"""Cloudflare Access JWT verification.

Cloudflare Access signs every authenticated request with a JWT in the
`Cf-Access-Jwt-Assertion` header. Verifying this signature against
Cloudflare's JWKS proves the request actually went through the CF edge
and was authenticated against our team's identity providers — defense
in depth on top of email-header trust + ufw IP allowlist.

Public surface:
- `JwtVerificationError` — raised on any verification failure
- `verify_jwt(token, team_domain, aud) -> dict` — returns claims dict on success
- `get_jwks(team_domain) -> list[dict]` — fetch + memoize JWKS for 24 h

設計筆記：
- `pyjwt[crypto]` (含 cryptography) 採 lazy import — apps 沒裝 deps（如純 dev/test）
  整個 import 仍可成功。
- JWKS 在 module-level dict 快取 24 小時；若 verify 因 kid mismatch（CF rotated keys）
  失敗，refetch 一次再嘗試。
- 不接受 `None` algorithm；強制 RS256，避免 `alg: none` 攻擊。
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger("polan.cf_jwt")

# JWKS 快取 24 h；CF 通常數週才 rotate，但保守一點也不會付出多少額外成本
_JWKS_CACHE_TTL_SECONDS = 24 * 60 * 60
_JWKS_FETCH_TIMEOUT_SECONDS = 5

# module-level cache: {team_domain: (fetched_at_epoch, jwks_keys_list)}
_jwks_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


class JwtVerificationError(Exception):
    """Raised when Cloudflare Access JWT verification fails."""


def _jwks_url(team_domain: str) -> str:
    """Build the JWKS endpoint URL for a Cloudflare team."""
    return f"https://{team_domain}/cdn-cgi/access/certs"


def _fetch_jwks(team_domain: str) -> list[dict[str, Any]]:
    """Fetch JWKS from Cloudflare. Raises JwtVerificationError on network/parse failure."""
    url = _jwks_url(team_domain)
    try:
        # nosec B310 — 我們 hardcode https + 受控 team_domain，不接受外部任意 URL
        with urllib.request.urlopen(url, timeout=_JWKS_FETCH_TIMEOUT_SECONDS) as resp:  # noqa: S310
            raw = resp.read()
    except (urllib.error.URLError, TimeoutError) as e:
        raise JwtVerificationError(f"無法連線 Cloudflare JWKS: {e}") from e

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        raise JwtVerificationError(f"Cloudflare JWKS 回傳非 JSON: {e}") from e

    keys = payload.get("keys")
    if not isinstance(keys, list) or not keys:
        raise JwtVerificationError("Cloudflare JWKS 缺少 keys 陣列")
    return keys


def get_jwks(team_domain: str, *, force_refresh: bool = False) -> list[dict[str, Any]]:
    """Return JWKS keys list, using a 24 h in-memory cache.

    Args:
        team_domain: 形如 `polan.cloudflareaccess.com` 的 CF team subdomain
        force_refresh: True 時忽略快取，重新抓取

    Raises:
        JwtVerificationError: 抓取或解析 JWKS 失敗
    """
    now = time.time()
    cached = _jwks_cache.get(team_domain)
    if not force_refresh and cached is not None:
        fetched_at, keys = cached
        if now - fetched_at < _JWKS_CACHE_TTL_SECONDS:
            return keys

    keys = _fetch_jwks(team_domain)
    _jwks_cache[team_domain] = (now, keys)
    return keys


def _find_key_by_kid(keys: list[dict[str, Any]], kid: str) -> dict[str, Any] | None:
    for k in keys:
        if k.get("kid") == kid:
            return k
    return None


def verify_jwt(token: str, team_domain: str, aud: str) -> dict[str, Any]:
    """Verify a Cloudflare Access JWT and return its claims.

    Args:
        token: 從 `Cf-Access-Jwt-Assertion` header 拿到的 JWT 字串
        team_domain: 形如 `polan.cloudflareaccess.com`
        aud: Application AUD Tag（從 CF Zero Trust dashboard 取）

    Returns:
        dict — JWT claims（含 `email`、`aud`、`exp` 等）

    Raises:
        JwtVerificationError: 任何驗證失敗（簽章 / aud / exp / kid 不存在等）
    """
    if not token:
        raise JwtVerificationError("JWT 為空")
    if not team_domain or not aud:
        raise JwtVerificationError("team_domain 與 aud 都必須提供")

    # Lazy import — 沒裝 pyjwt 的 dev/test 環境 import 本 module 不會炸
    try:
        import jwt  # type: ignore[import-not-found]
        from jwt import algorithms as jwt_algorithms  # type: ignore[import-not-found]
        from jwt.exceptions import (  # type: ignore[import-not-found]
            InvalidTokenError,
            PyJWTError,
        )
    except ImportError as e:
        raise JwtVerificationError(
            "未安裝 pyjwt[crypto]；無法驗證 Cloudflare Access JWT"
        ) from e

    # 取出 unverified header 拿到 kid，才能對到 JWKS 中的公鑰
    try:
        unverified_header = jwt.get_unverified_header(token)
    except PyJWTError as e:
        raise JwtVerificationError(f"JWT header 無法解析：{e}") from e

    kid = unverified_header.get("kid")
    if not kid:
        raise JwtVerificationError("JWT header 缺 kid")

    keys = get_jwks(team_domain)
    jwk = _find_key_by_kid(keys, kid)
    if jwk is None:
        # kid 沒在 cache → 可能 CF 剛 rotate；refetch 一次再試
        log.info("CF JWKS kid=%s 不在快取中，refetching", kid)
        keys = get_jwks(team_domain, force_refresh=True)
        jwk = _find_key_by_kid(keys, kid)
        if jwk is None:
            raise JwtVerificationError(f"JWT kid={kid} 在 JWKS 找不到對應公鑰")

    try:
        public_key = jwt_algorithms.RSAAlgorithm.from_jwk(json.dumps(jwk))
    except (ValueError, PyJWTError) as e:
        raise JwtVerificationError(f"無法從 JWK 建構公鑰：{e}") from e

    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            key=public_key,
            algorithms=["RS256"],
            audience=aud,
            options={"require": ["exp", "iat", "aud"]},
        )
    except InvalidTokenError as e:
        # InvalidAudienceError / ExpiredSignatureError / InvalidSignatureError 等都是 InvalidTokenError 子類
        raise JwtVerificationError(f"JWT 驗證失敗：{e}") from e
    except PyJWTError as e:
        raise JwtVerificationError(f"JWT 驗證失敗：{e}") from e

    return claims
