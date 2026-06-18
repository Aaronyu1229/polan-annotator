"""admin 發佈 / 列表 / 撤銷 client link。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src import main as main_module
from src.alignment_db import AlignmentBase, ClientLink, get_alignment_session
import src.routes.admin as admin_routes


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    eng = create_engine("sqlite:///:memory:",
                        connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    AlignmentBase.metadata.create_all(eng)

    src_dir = tmp_path / "audio"
    src_dir.mkdir()
    (src_dir / "ref.wav").write_bytes(b"RIFF0000WAVE")
    # publish 端點用的來源/目的目錄都導到 tmp
    monkeypatch.setattr(admin_routes, "AUDIO_DIR", src_dir, raising=False)
    monkeypatch.setattr(admin_routes, "ALIGNMENT_AUDIO_DIR", tmp_path / "out", raising=False)

    def _override():
        with Session(eng) as s:
            yield s
    main_module.app.dependency_overrides[get_alignment_session] = _override
    yield TestClient(main_module.app), eng
    main_module.app.dependency_overrides.clear()


def test_publish_returns_url_and_token(admin_client):
    client, _ = admin_client
    r = client.post("/api/admin/alignment/publish", json={
        "filename": "ref.wav", "label": "客戶A", "annotator_id": "cli1",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["token"]
    assert "/alignment?token=" in body["client_url"]
    assert body["session_id"]


def test_links_list_excludes_token(admin_client):
    client, _ = admin_client
    client.post("/api/admin/alignment/publish", json={"filename": "ref.wav", "label": "A", "annotator_id": "c1"})
    r = client.get("/api/admin/alignment/links")
    assert r.status_code == 200
    links = r.json()["links"]
    assert len(links) == 1
    assert "token" not in links[0]
    assert "token_hash" not in links[0]


def test_revoke_marks_link(admin_client):
    client, eng = admin_client
    pub = client.post("/api/admin/alignment/publish", json={"filename": "ref.wav", "label": "A", "annotator_id": "c1"}).json()
    r = client.post(f"/api/admin/alignment/links/{pub['link_id']}/revoke")
    assert r.status_code == 200
    with Session(eng) as s:
        assert s.get(ClientLink, pub["link_id"]).revoked is True
