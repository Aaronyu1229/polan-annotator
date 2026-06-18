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
