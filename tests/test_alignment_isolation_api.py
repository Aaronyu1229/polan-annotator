"""alignment 端點的存取隔離：context 回鎖定 ctx、音檔只在獨立倉解析。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src import main as main_module
from src.alignment_db import AlignmentAudio, AlignmentBase, ClientLink, get_alignment_session
from src.client_auth import generate_token, hash_token
import src.routes.alignment as align_routes


@pytest.fixture
def iso(tmp_path, monkeypatch):
    eng = create_engine("sqlite:///:memory:",
                        connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    AlignmentBase.metadata.create_all(eng)

    # 獨立音檔倉指向 tmp
    audio_dir = tmp_path / "alignment_audio"
    audio_dir.mkdir()
    (audio_dir / "ref.wav").write_bytes(b"RIFF0000WAVE")
    monkeypatch.setattr(align_routes, "ALIGNMENT_AUDIO_DIR", audio_dir)

    tok = generate_token()
    with Session(eng) as s:
        s.add(AlignmentAudio(id="aa1", filename="ref.wav"))
        s.add(ClientLink(id="cl1", token_hash=hash_token(tok), role="client",
                         label="A", annotator_id="cli1", session_id="s1",
                         alignment_audio_id="aa1"))
        s.commit()

    def _override():
        with Session(eng) as s:
            yield s
    main_module.app.dependency_overrides[get_alignment_session] = _override

    # 啟用 prod gate（強制 token）
    class _S:
        cloudflare_access_enabled = True
        oauth_enabled = False
        cloudflare_access_team_domain = ""
        cloudflare_access_aud = ""
    monkeypatch.setattr(main_module.app.state, "settings", _S())

    yield TestClient(main_module.app), tok
    main_module.app.dependency_overrides.clear()


def test_context_returns_locked_ctx(iso):
    client, tok = iso
    r = client.get(f"/api/alignment/context?token={tok}")
    assert r.status_code == 200
    assert r.json() == {
        "role": "client", "annotator_id": "cli1",
        "session_id": "s1", "alignment_audio_id": "aa1",
    }


def test_stream_serves_from_isolated_store(iso):
    client, tok = iso
    r = client.get(f"/api/alignment/audio/aa1/stream?token={tok}")
    assert r.status_code == 200
    assert r.content == b"RIFF0000WAVE"


def test_stream_rejects_audio_outside_link(iso):
    client, tok = iso
    # 客戶要別支音檔 → 403（不在 token 綁定範圍）
    r = client.get(f"/api/alignment/audio/aa-other/stream?token={tok}")
    assert r.status_code == 403


def test_context_without_token_rejected(iso):
    client, _tok = iso
    assert client.get("/api/alignment/context").status_code == 401
