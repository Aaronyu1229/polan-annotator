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
