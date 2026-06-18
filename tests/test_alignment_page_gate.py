"""/alignment 頁面路由的 token gate（prod 擋、dev 放行）。"""
import pytest
from fastapi.testclient import TestClient

from src import main as main_module


def test_alignment_page_dev_mode_serves():
    # conftest 預設 OAUTH=false、CF=false → dev 放行
    c = TestClient(main_module.app)
    r = c.get("/alignment")
    assert r.status_code == 200


def test_alignment_page_prod_mode_requires_token(monkeypatch):
    class _S:
        cloudflare_access_enabled = True
        oauth_enabled = False
    monkeypatch.setattr(main_module.app.state, "settings", _S())
    c = TestClient(main_module.app)
    r = c.get("/alignment")
    assert r.status_code == 401
    monkeypatch.undo()


def test_alignment_page_sets_cookie_with_valid_token(monkeypatch):
    """頁面帶有效 token → 必須回 Set-Cookie，否則前端打 /api/alignment/* 會缺 cookie。

    回歸防護：gate 在注入 Response 種的 cookie 會被直接回傳的 FileResponse 丟掉，
    必須在頁路由實際回傳的 response 上補種。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session as SASession
    from sqlalchemy.pool import StaticPool

    from src.alignment_db import AlignmentBase, ClientLink, get_alignment_session
    from src.client_auth import CLIENT_COOKIE, generate_token, hash_token

    eng = create_engine("sqlite:///:memory:",
                        connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    AlignmentBase.metadata.create_all(eng)
    tok = generate_token()
    with SASession(eng) as s:
        s.add(ClientLink(id="cl1", token_hash=hash_token(tok), role="client",
                         label="A", annotator_id="cli1", session_id="s1",
                         alignment_audio_id="aa1"))
        s.commit()

    def _override():
        with SASession(eng) as s:
            yield s
    main_module.app.dependency_overrides[get_alignment_session] = _override

    class _S:
        cloudflare_access_enabled = True
        oauth_enabled = False
    monkeypatch.setattr(main_module.app.state, "settings", _S())

    try:
        c = TestClient(main_module.app)
        r = c.get(f"/alignment?token={tok}")
        assert r.status_code == 200
        # 關鍵：頁面回應必須帶 Set-Cookie 把 token 種給瀏覽器
        set_cookie = r.headers.get("set-cookie", "")
        assert f"{CLIENT_COOKIE}=" in set_cookie
    finally:
        main_module.app.dependency_overrides.clear()
        monkeypatch.undo()
