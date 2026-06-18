"""token 工具 + resolve_alignment_access 依賴。"""
import base64
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.alignment_db import AlignmentBase, ClientLink, get_alignment_session
from src.client_auth import (
    CLIENT_COOKIE,
    AlignmentAccess,
    generate_token,
    hash_token,
    resolve_alignment_access,
    verify_token_hash,
)


def test_generate_token_is_high_entropy_and_unique():
    a, b = generate_token(), generate_token()
    assert a != b
    assert len(base64.urlsafe_b64decode(a + "==")) >= 32


def test_hash_and_verify_roundtrip():
    tok = generate_token()
    h = hash_token(tok)
    assert h != tok                       # 不存明文
    assert verify_token_hash(tok, h) is True
    assert verify_token_hash("wrong", h) is False


def _app_with_gate(eng, cf_enabled: bool):
    """最小 app：一個受 gate 保護的路由，回傳解析出的 access。"""
    app = FastAPI()

    class _S:
        cloudflare_access_enabled = cf_enabled
        oauth_enabled = False
    app.state.settings = _S()

    def _override():
        with Session(eng) as s:
            yield s
    app.dependency_overrides[get_alignment_session] = _override

    @app.get("/probe")
    def probe(acc: AlignmentAccess = Depends(resolve_alignment_access)):
        return {"role": acc.role, "session_id": acc.session_id}

    return app


@pytest.fixture
def gate_engine():
    eng = create_engine("sqlite:///:memory:",
                        connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    AlignmentBase.metadata.create_all(eng)
    return eng


def test_dev_mode_passes_through_as_engineer(gate_engine):
    c = TestClient(_app_with_gate(gate_engine, cf_enabled=False))
    r = c.get("/probe")
    assert r.status_code == 200
    assert r.json()["role"] == "engineer"


def test_prod_mode_rejects_missing_token(gate_engine):
    c = TestClient(_app_with_gate(gate_engine, cf_enabled=True))
    r = c.get("/probe")
    assert r.status_code == 401


def test_prod_mode_accepts_valid_client_token_and_sets_cookie(gate_engine):
    tok = generate_token()
    with Session(gate_engine) as s:
        s.add(ClientLink(id="cl1", token_hash=hash_token(tok), role="client",
                         label="A", annotator_id="cli1", session_id="s1",
                         alignment_audio_id="aa1"))
        s.commit()
    c = TestClient(_app_with_gate(gate_engine, cf_enabled=True))
    r = c.get(f"/probe?token={tok}")
    assert r.status_code == 200
    assert r.json() == {"role": "client", "session_id": "s1"}
    assert CLIENT_COOKIE in r.cookies


def test_prod_mode_rejects_revoked_token(gate_engine):
    tok = generate_token()
    with Session(gate_engine) as s:
        s.add(ClientLink(id="cl2", token_hash=hash_token(tok), role="client",
                         label="A", annotator_id="cli1", session_id="s1",
                         alignment_audio_id="aa1", revoked=True))
        s.commit()
    c = TestClient(_app_with_gate(gate_engine, cf_enabled=True))
    assert c.get(f"/probe?token={tok}").status_code == 403


def test_prod_mode_rejects_expired_token(gate_engine):
    tok = generate_token()
    with Session(gate_engine) as s:
        s.add(ClientLink(id="cl3", token_hash=hash_token(tok), role="client",
                         label="A", annotator_id="cli1", session_id="s1",
                         alignment_audio_id="aa1",
                         expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        s.commit()
    c = TestClient(_app_with_gate(gate_engine, cf_enabled=True))
    assert c.get(f"/probe?token={tok}").status_code == 403


def test_prod_mode_accepts_token_via_cookie(gate_engine):
    tok = generate_token()
    with Session(gate_engine) as s:
        s.add(ClientLink(id="cl4", token_hash=hash_token(tok), role="client",
                         label="A", annotator_id="cli1", session_id="s1",
                         alignment_audio_id="aa1"))
        s.commit()
    c = TestClient(_app_with_gate(gate_engine, cf_enabled=True))
    c.cookies.set(CLIENT_COOKIE, tok)
    r = c.get("/probe")
    assert r.status_code == 200
    assert r.json()["role"] == "client"
