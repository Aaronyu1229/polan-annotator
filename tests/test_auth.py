"""Phase 6 — auth + middleware 測試。

涵蓋：
- dev 模式（OAUTH_ENABLED=false）：query string 仍可帶 ?annotator=
- prod 模式（OAUTH_ENABLED=true）：未登入 → 401；白名單外 → 403；白名單內 → 200 + annotator_id 對應 map
- email_to_annotator_id 映射 / sanitize 行為

prod 模式的 fixture 需 `itsdangerous` (SessionMiddleware) 與 `authlib`（OAuth）；
若任一未安裝則整段 prod 測試 skip — Phase 6 deps 還沒 install 時 dev 模式測試仍能跑。
"""
from __future__ import annotations

import importlib

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.auth import email_to_annotator_id, is_admin
from src.config import Settings


# ─── 1. dev 模式：?annotator= 還能用 ────────────────────

def test_dev_mode_query_string_annotator_works(client, in_memory_engine):
    """既有 Phase 1-5 行為：dev 模式（OAUTH_ENABLED=false）下，
    `?annotator=bob` 仍能驅動 require_auth。
    """
    # /api/stats/progress 在 Phase 6 改用 require_auth；?annotator=bob 應該能通
    r = client.get("/api/stats/progress?annotator=bob")
    assert r.status_code == 200
    body = r.json()
    # bob 沒標過任何東西，回 has_data=False / 0 件
    assert body["annotator_id"] == "bob"


def test_dev_mode_no_query_defaults_to_amber(client):
    """spec 要求：dev 模式無 query 時 fallback `amber`，保留既有 UX。"""
    r = client.get("/api/stats/progress")
    assert r.status_code == 200
    body = r.json()
    assert body["annotator_id"] == "amber"


def test_api_me_dev_mode_returns_query_annotator(client):
    r = client.get("/api/me?annotator=carol")
    assert r.status_code == 200
    body = r.json()
    assert body["annotator_id"] == "carol"
    assert body["email"] is None
    # dev 模式刻意給 is_admin=True，讓本機可以測 admin-only 功能（如上傳音源）。
    # production 走 OAuth 分支，仍嚴格依 ADMIN_EMAILS 判斷。
    assert body["is_admin"] is True


# ─── 2. email → annotator_id 對應規則 ────────────────────

def _settings_with_map(map_dict: dict[str, str], admins: set[str] | None = None) -> Settings:
    return Settings(
        oauth_enabled=True,
        app_domain="annotate.dolcenforte.com",
        app_secret_key="x" * 32,
        google_client_id="cid",
        google_client_secret="csec",
        oauth_redirect_uri="https://annotate.dolcenforte.com/auth/callback",
        allowed_emails=frozenset({k.lower() for k in map_dict}),
        email_to_annotator={k.lower(): v for k, v in map_dict.items()},
        admin_emails=frozenset({a.lower() for a in (admins or set())}),
    )


def test_email_to_annotator_id_uses_map_first():
    s = _settings_with_map({
        "reborn.uidesigner@gmail.com": "aaron",
        "polanmusic2025@gmail.com": "amber",
    })
    assert email_to_annotator_id("reborn.uidesigner@gmail.com", s) == "aaron"
    assert email_to_annotator_id("polanmusic2025@gmail.com", s) == "amber"


def test_email_to_annotator_id_case_insensitive():
    s = _settings_with_map({"AlIcE@example.com": "alice_real"})
    assert email_to_annotator_id("ALICE@EXAMPLE.COM", s) == "alice_real"
    assert email_to_annotator_id("alice@example.com", s) == "alice_real"


def test_email_to_annotator_id_falls_back_to_local_part():
    s = _settings_with_map({})
    assert email_to_annotator_id("kevin@example.com", s) == "kevin"


def test_email_to_annotator_id_sanitizes_weird_chars():
    s = _settings_with_map({})
    # 加號 + 點 + 大寫 → 都 normalize 成 _
    assert email_to_annotator_id("Kev.in+work@example.com", s) == "kev_in_work"


def test_email_to_annotator_id_empty_email():
    s = _settings_with_map({})
    assert email_to_annotator_id("", s) == "unknown"


def test_is_admin_uses_admin_emails():
    s = _settings_with_map(
        {"a@b.com": "a", "c@b.com": "c"},
        admins={"a@b.com"},
    )
    assert is_admin("a@b.com", s) is True
    assert is_admin("A@B.COM", s) is True  # 大小寫不敏感
    assert is_admin("c@b.com", s) is False
    assert is_admin(None, s) is False


# ─── 3. prod 模式：完整 OAuth flow（需 deps）────────────

def _has_session_middleware_deps() -> bool:
    """SessionMiddleware 需要 itsdangerous；未安裝則 skip prod tests。"""
    try:
        importlib.import_module("itsdangerous")
        return True
    except ImportError:
        return False


prod_skip = pytest.mark.skipif(
    not _has_session_middleware_deps(),
    reason="Phase 6 依賴 (itsdangerous) 尚未安裝；跑 `pip install -e .` 後可啟用",
)


@pytest.fixture
def prod_app(in_memory_engine):
    """組一個 OAuth 啟用的 FastAPI app，接 SessionMiddleware + 路由 + in-memory DB。

    為避免 SessionMiddleware 的 https_only=True 在 TestClient 下吃不到 cookie，
    這裡刻意建一個獨立 app instance（不用 main.app），把 https_only=False。
    """
    from sqlmodel import Session
    from starlette.middleware.sessions import SessionMiddleware

    from src import middleware as middleware_module
    from src.db import get_session
    from src.routes import auth as auth_routes
    from src.routes import calibration, feedback, stats

    settings = _settings_with_map(
        {
            "reborn.uidesigner@gmail.com": "aaron",
            "polanmusic2025@gmail.com": "amber",
        },
        admins={"reborn.uidesigner@gmail.com"},
    )
    app = FastAPI()
    app.state.settings = settings
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.app_secret_key,
        https_only=False,  # TestClient 不會帶 https
        same_site="lax",
        session_cookie="polan_session",
    )
    app.include_router(auth_routes.router)
    app.include_router(stats.router)
    app.include_router(feedback.router)
    app.include_router(calibration.api_router)

    def _override_session():
        with Session(in_memory_engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override_session

    # 確保 require_auth 拿到正確的 settings（透過 request.app.state）
    _ = middleware_module  # 防 lint unused
    return app


@prod_skip
def test_prod_no_session_returns_401(prod_app):
    client = TestClient(prod_app)
    r = client.get("/api/me")
    assert r.status_code == 401


@prod_skip
def test_prod_whitelisted_email_passes(prod_app):
    client = TestClient(prod_app)
    # 模擬已登入：直接寫 session（透過 endpoint 注入 — 用 starlette 提供的 session 機制）
    # 用一個極簡 helper endpoint 暫時注入 session
    @prod_app.get("/_test/login")
    def _test_login(request: Request) -> dict:
        request.session["user"] = {
            "email": "reborn.uidesigner@gmail.com",
            "name": "Aaron",
            "annotator_id": "aaron",
            "is_admin": True,
        }
        return {"ok": True}

    r0 = client.get("/_test/login")
    assert r0.status_code == 200

    r = client.get("/api/me")
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "reborn.uidesigner@gmail.com"
    assert body["annotator_id"] == "aaron"
    assert body["is_admin"] is True


@prod_skip
def test_prod_non_whitelisted_email_returns_403(prod_app):
    client = TestClient(prod_app)

    @prod_app.get("/_test/login_bad")
    def _test_login_bad(request: Request) -> dict:
        request.session["user"] = {
            "email": "stranger@example.com",
            "name": "Stranger",
            "annotator_id": "stranger",
            "is_admin": False,
        }
        return {"ok": True}

    r0 = client.get("/_test/login_bad")
    assert r0.status_code == 200

    r = client.get("/api/me")
    assert r.status_code == 403


@prod_skip
def test_prod_logout_clears_session(prod_app):
    client = TestClient(prod_app)

    @prod_app.get("/_test/login_amber")
    def _test_login_amber(request: Request) -> dict:
        request.session["user"] = {
            "email": "polanmusic2025@gmail.com",
            "name": "Amber",
            "annotator_id": "amber",
            "is_admin": False,
        }
        return {"ok": True}

    client.get("/_test/login_amber")
    assert client.get("/api/me").status_code == 200

    # 登出後 session 應被清空
    r = client.post("/logout", follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/login"

    # 之後再請求 → 401
    r2 = client.get("/api/me")
    assert r2.status_code == 401


# ─── 4. Cloudflare Access tests ────────────────────────────
#
# CF Access 路徑有兩種：
# (a) header-only（CLOUDFLARE_ACCESS_TEAM_DOMAIN/AUD 任一空）— 信任 ufw + email header
# (b) JWT 驗證（兩個都設）— 強制驗證 Cf-Access-Jwt-Assertion 簽章
#
# 為避免測試需要真的去 Cloudflare 抓 JWKS，整段 CF 測試用 monkeypatch
# 把 src.middleware.verify_jwt 換成 fake。


def _cf_settings(
    *,
    team_domain: str = "",
    aud: str = "",
    map_dict: dict[str, str] | None = None,
    admins: set[str] | None = None,
) -> Settings:
    """Build a Settings with Cloudflare Access enabled (oauth disabled)."""
    map_dict = map_dict or {
        "reborn.uidesigner@gmail.com": "aaron",
        "polanmusic2025@gmail.com": "amber",
    }
    return Settings(
        oauth_enabled=False,
        app_domain="annotate.dolcenforte.com",
        app_secret_key="x" * 32,
        google_client_id="",
        google_client_secret="",
        oauth_redirect_uri="https://annotate.dolcenforte.com/auth/callback",
        allowed_emails=frozenset({k.lower() for k in map_dict}),
        email_to_annotator={k.lower(): v for k, v in map_dict.items()},
        admin_emails=frozenset({a.lower() for a in (admins or set())}),
        cloudflare_access_enabled=True,
        cloudflare_access_team_domain=team_domain,
        cloudflare_access_aud=aud,
    )


@pytest.fixture
def cf_app(in_memory_engine):
    """組一個 Cloudflare Access 模式的 app（無 SessionMiddleware；不需 OAuth）。

    回 callable `make(settings)` — caller 自選 header-only 或 JWT mode。
    """
    from sqlmodel import Session

    from src.db import get_session
    from src.routes import auth as auth_routes
    from src.routes import calibration, feedback, stats

    def make(settings: Settings) -> FastAPI:
        app = FastAPI()
        app.state.settings = settings
        app.include_router(auth_routes.router)
        app.include_router(stats.router)
        app.include_router(feedback.router)
        app.include_router(calibration.api_router)

        def _override_session():
            with Session(in_memory_engine) as s:
                yield s

        app.dependency_overrides[get_session] = _override_session
        return app

    return make


def test_cf_mode_header_only_trust(cf_app):
    """CF enabled、未設 JWT env → 信任 Cf-Access-Authenticated-User-Email header。"""
    settings = _cf_settings()  # team_domain='' aud='' → header-only
    app = cf_app(settings)
    client = TestClient(app)
    r = client.get(
        "/api/me",
        headers={"Cf-Access-Authenticated-User-Email": "reborn.uidesigner@gmail.com"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "reborn.uidesigner@gmail.com"
    assert body["annotator_id"] == "aaron"


def test_cf_mode_no_header_returns_401(cf_app):
    """CF enabled、header-only mode、完全沒帶 header → 401。"""
    settings = _cf_settings()
    app = cf_app(settings)
    client = TestClient(app)
    r = client.get("/api/me")
    assert r.status_code == 401


def test_cf_mode_jwt_required_when_aud_set_but_no_jwt(cf_app):
    """JWT mode（team+aud 都設）但只帶 email header、沒帶 JWT header → 401。"""
    settings = _cf_settings(
        team_domain="polan.cloudflareaccess.com",
        aud="abc123def",
    )
    app = cf_app(settings)
    client = TestClient(app)
    r = client.get(
        "/api/me",
        headers={"Cf-Access-Authenticated-User-Email": "reborn.uidesigner@gmail.com"},
    )
    assert r.status_code == 401


def test_cf_mode_jwt_invalid_signature_rejected(cf_app, monkeypatch):
    """JWT mode、帶了 JWT 但簽章爛 → 401（mock verify_jwt 拋 JwtVerificationError）。"""
    from src import middleware as middleware_module
    from src.cf_jwt import JwtVerificationError

    def _fake_verify(token, *, team_domain, aud):  # noqa: ARG001
        raise JwtVerificationError("simulated bad signature")

    monkeypatch.setattr(middleware_module, "verify_jwt", _fake_verify)

    settings = _cf_settings(
        team_domain="polan.cloudflareaccess.com",
        aud="abc123def",
    )
    app = cf_app(settings)
    client = TestClient(app)
    r = client.get(
        "/api/me",
        headers={
            "Cf-Access-Authenticated-User-Email": "reborn.uidesigner@gmail.com",
            "Cf-Access-Jwt-Assertion": "this.is.not.a.real.jwt",
        },
    )
    assert r.status_code == 401
    # 內部錯誤訊息不該洩漏給 client
    assert "simulated bad signature" not in r.text


def test_cf_mode_jwt_valid_signature_accepts(cf_app, monkeypatch):
    """JWT mode、verify_jwt 回成功 claims → 200，使用 claims['email']。"""
    from src import middleware as middleware_module

    def _fake_verify(token, *, team_domain, aud):  # noqa: ARG001
        return {
            "email": "polanmusic2025@gmail.com",
            "name": "Amber",
            "aud": aud,
            "exp": 9999999999,
        }

    monkeypatch.setattr(middleware_module, "verify_jwt", _fake_verify)

    settings = _cf_settings(
        team_domain="polan.cloudflareaccess.com",
        aud="abc123def",
    )
    app = cf_app(settings)
    client = TestClient(app)
    r = client.get(
        "/api/me",
        headers={
            # 故意給不同 email 在 plaintext header；JWT mode 應以 claims 為準（忽略此 header）
            "Cf-Access-Authenticated-User-Email": "stranger@example.com",
            "Cf-Access-Jwt-Assertion": "valid.signed.token",
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "polanmusic2025@gmail.com"
    assert body["annotator_id"] == "amber"


def test_cf_mode_admin_email_yields_admin_true(cf_app):
    """admin email（在 ADMIN_EMAILS）→ is_admin=True；非 admin → False。"""
    settings = _cf_settings(admins={"reborn.uidesigner@gmail.com"})
    app = cf_app(settings)
    client = TestClient(app)

    r_admin = client.get(
        "/api/me",
        headers={"Cf-Access-Authenticated-User-Email": "reborn.uidesigner@gmail.com"},
    )
    assert r_admin.status_code == 200
    assert r_admin.json()["is_admin"] is True

    r_amber = client.get(
        "/api/me",
        headers={"Cf-Access-Authenticated-User-Email": "polanmusic2025@gmail.com"},
    )
    assert r_amber.status_code == 200
    assert r_amber.json()["is_admin"] is False


def test_email_to_annotator_mapping_used(cf_app):
    """polanmusic2025@gmail.com 透過 EMAIL_TO_ANNOTATOR_JSON 映射成 'amber'。"""
    settings = _cf_settings()
    app = cf_app(settings)
    client = TestClient(app)
    r = client.get(
        "/api/me",
        headers={"Cf-Access-Authenticated-User-Email": "polanmusic2025@gmail.com"},
    )
    assert r.status_code == 200
    assert r.json()["annotator_id"] == "amber"
