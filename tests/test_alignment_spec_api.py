"""BGM 對齊規格區 API 測試（loop / loop_length / style_tags）。"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src import main as main_module
from src.alignment_db import AlignmentBase, get_alignment_session


@pytest.fixture
def align_client():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AlignmentBase.metadata.create_all(eng)

    def _override():
        with Session(eng) as s:
            yield s

    main_module.app.dependency_overrides[get_alignment_session] = _override
    yield TestClient(main_module.app)
    main_module.app.dependency_overrides.clear()


def _post_spec(client, **kw):
    body = dict(
        session_id="s1", annotator_id="cli1", annotator_role="client",
        audio_id="newV1", audio_role="deliverable", version=1,
        loop="loop", loop_length=30, style_tags=["chinese_traditional", "festival"],
    )
    body.update(kw)
    return client.post("/api/alignment/spec", json=body)


def test_save_spec_persists_and_round_trips(align_client):
    assert _post_spec(align_client).status_code == 200
    r = align_client.get("/api/alignment/spec", params={"session_id": "s1"})
    specs = r.json()["specs"]
    assert len(specs) == 1
    assert specs[0]["loop"] == "loop"
    assert specs[0]["loop_length"] == 30
    assert specs[0]["style_tags"] == ["chinese_traditional", "festival"]


def test_save_spec_is_upsert(align_client):
    _post_spec(align_client, loop_length=30)
    _post_spec(align_client, loop_length=60)
    specs = align_client.get("/api/alignment/spec", params={"session_id": "s1"}).json()["specs"]
    assert len(specs) == 1
    assert specs[0]["loop_length"] == 60


def test_reject_bad_loop(align_client):
    r = _post_spec(align_client, loop="forever")
    assert r.status_code == 400
    assert "loop" in r.json()["detail"]


def test_reject_bad_loop_length(align_client):
    r = _post_spec(align_client, loop_length=45)
    assert r.status_code == 400
    assert "loop_length" in r.json()["detail"]


def test_reject_freeform_style_tag(align_client):
    r = _post_spec(align_client, style_tags=["chinese_traditional", "my_custom_vibe"])
    assert r.status_code == 400
    assert "my_custom_vibe" in r.json()["detail"]


def test_nullable_fields_ok(align_client):
    r = _post_spec(align_client, loop=None, loop_length=None, style_tags=[])
    assert r.status_code == 200


def test_style_options_endpoint_lists_whitelist(align_client):
    r = align_client.get("/api/alignment/style-options")
    tags = r.json()["style_tags"]
    assert "chinese_traditional" in tags
    assert "festival" in tags
    assert len(tags) == 24
