"""Phase 6 — env loader / settings 物件。

所有環境變數在這裡集中讀取、驗證、給 type-safe 的 Settings。
其餘程式碼**只**從 `load_settings()` 拿值，不要散落在四處讀 `os.environ`。

開發者預設行為（OAUTH_ENABLED=false）：
- 不需設任何 OAuth env，工具仍可用 ?annotator= 跑測試 / dev server
- 只有 `app_domain` 有意義（給日後 link 構造）

正式部署（OAUTH_ENABLED=true）：
- 啟動時 fail-fast 缺項檢查
- 缺 GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / APP_SECRET_KEY 直接 raise，
  比 server 起來後第一個請求才炸友善得多
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("polan.config")

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 嘗試讀 .env（dev 用），缺檔不算錯
try:
    from dotenv import load_dotenv  # type: ignore[import-not-found]

    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    # python-dotenv 尚未安裝（如測試環境只跑 pytest 沒裝 Phase 6 deps）
    log.debug("python-dotenv 不存在；跳過 .env 載入")


def _parse_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_email_set(raw: str | None) -> frozenset[str]:
    if not raw:
        return frozenset()
    return frozenset(
        e.strip().lower() for e in raw.split(",") if e.strip()
    )


def _parse_email_to_annotator(raw: str | None) -> dict[str, str]:
    """JSON object，key 是 email，value 是 annotator_id。

    回傳的 dict 已 lowercase key（email 大小寫不敏感）。
    """
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"EMAIL_TO_ANNOTATOR_JSON 不是合法 JSON：{e}"
        ) from e
    if not isinstance(parsed, dict):
        raise ValueError("EMAIL_TO_ANNOTATOR_JSON 必須是 JSON object")
    result: dict[str, str] = {}
    for k, v in parsed.items():
        if not isinstance(k, str) or not isinstance(v, str):
            raise ValueError(
                "EMAIL_TO_ANNOTATOR_JSON 的 key/value 必須都是字串"
            )
        result[k.strip().lower()] = v.strip()
    return result


@dataclass(frozen=True)
class Settings:
    """執行期不可變設定。startup 時 build 一次，全 app 共用。"""

    oauth_enabled: bool
    app_domain: str
    app_secret_key: str
    google_client_id: str
    google_client_secret: str
    oauth_redirect_uri: str
    allowed_emails: frozenset[str] = field(default_factory=frozenset)
    email_to_annotator: dict[str, str] = field(default_factory=dict)
    admin_emails: frozenset[str] = field(default_factory=frozenset)
    sentry_dsn: str | None = None
    # Cloudflare Access — 由 Cloudflare 邊緣處理登入並帶 header `Cf-Access-Authenticated-User-Email`
    cloudflare_access_enabled: bool = False
    # Cloudflare Access JWT verification (defense in depth)：兩個都填才生效；
    # 任一空則中介層只信任 email header（仍安全，因為 ufw 限制 direct-IP 流量）
    cloudflare_access_team_domain: str = ""
    cloudflare_access_aud: str = ""


def load_settings() -> Settings:
    """從 process env 組 Settings。oauth_enabled=true 時 fail-fast 檢查必填。"""
    oauth_enabled = _parse_bool(os.environ.get("OAUTH_ENABLED"), default=False)
    app_domain = os.environ.get("APP_DOMAIN", "localhost:8000").strip()

    app_secret_key = os.environ.get("APP_SECRET_KEY", "").strip()
    google_client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    google_client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()

    default_redirect = (
        f"https://{app_domain}/auth/callback"
        if not app_domain.startswith(("http://", "https://"))
        else f"{app_domain.rstrip('/')}/auth/callback"
    )
    oauth_redirect_uri = os.environ.get(
        "OAUTH_REDIRECT_URI", default_redirect
    ).strip()

    allowed_emails = _parse_email_set(os.environ.get("ALLOWED_EMAILS"))
    email_to_annotator = _parse_email_to_annotator(
        os.environ.get("EMAIL_TO_ANNOTATOR_JSON")
    )
    admin_emails = _parse_email_set(os.environ.get("ADMIN_EMAILS"))

    sentry_dsn_raw = os.environ.get("SENTRY_DSN", "").strip()
    sentry_dsn: str | None = sentry_dsn_raw or None

    cloudflare_access_enabled = _parse_bool(
        os.environ.get("CLOUDFLARE_ACCESS_ENABLED"), default=False
    )
    cloudflare_access_team_domain = os.environ.get(
        "CLOUDFLARE_ACCESS_TEAM_DOMAIN", ""
    ).strip()
    cloudflare_access_aud = os.environ.get(
        "CLOUDFLARE_ACCESS_AUD", ""
    ).strip()

    # Cloudflare Access JWT verification — 兩個 env 必須一起設或一起空
    # 不 raise（CF 仍能在邊緣 gate），只 log warning 提醒設定不一致
    if cloudflare_access_enabled:
        has_domain = bool(cloudflare_access_team_domain)
        has_aud = bool(cloudflare_access_aud)
        if has_domain and has_aud:
            log.info(
                "Cloudflare Access JWT verification 啟用 (team=%s)",
                cloudflare_access_team_domain,
            )
        elif has_domain != has_aud:
            log.warning(
                "Cloudflare Access JWT 設定不完整：CLOUDFLARE_ACCESS_TEAM_DOMAIN=%r，"
                "CLOUDFLARE_ACCESS_AUD=%r — 兩個都要填才會啟用 JWT 驗證；"
                "目前 fallback 為僅信任 email header",
                cloudflare_access_team_domain,
                cloudflare_access_aud,
            )
        else:
            log.info(
                "Cloudflare Access 啟用，但未設 JWT 驗證 env — "
                "僅信任 Cf-Access-Authenticated-User-Email header（依賴 ufw IP 限制）"
            )

    if oauth_enabled:
        missing: list[str] = []
        if not app_secret_key:
            missing.append("APP_SECRET_KEY")
        if not google_client_id:
            missing.append("GOOGLE_CLIENT_ID")
        if not google_client_secret:
            missing.append("GOOGLE_CLIENT_SECRET")
        if missing:
            raise RuntimeError(
                "OAUTH_ENABLED=true 但缺少必填環境變數："
                f"{', '.join(missing)}"
            )
        if not allowed_emails:
            log.warning(
                "OAUTH_ENABLED=true 但 ALLOWED_EMAILS 為空 — 沒有人能登入"
            )
        # admin_emails 必須是 allowed_emails 的子集
        bad_admins = admin_emails - allowed_emails
        if bad_admins:
            raise RuntimeError(
                "ADMIN_EMAILS 包含不在 ALLOWED_EMAILS 的 email："
                f"{sorted(bad_admins)}"
            )

    return Settings(
        oauth_enabled=oauth_enabled,
        app_domain=app_domain,
        app_secret_key=app_secret_key,
        google_client_id=google_client_id,
        google_client_secret=google_client_secret,
        oauth_redirect_uri=oauth_redirect_uri,
        allowed_emails=allowed_emails,
        email_to_annotator=email_to_annotator,
        admin_emails=admin_emails,
        sentry_dsn=sentry_dsn,
        cloudflare_access_enabled=cloudflare_access_enabled,
        cloudflare_access_team_domain=cloudflare_access_team_domain,
        cloudflare_access_aud=cloudflare_access_aud,
    )
