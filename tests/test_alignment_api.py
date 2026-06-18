"""BGM 對齊 API integration 測試。

自帶 client fixture：同時 override get_alignment_session 為 in-memory alignment 庫，
與既有 conftest 的主庫 override 並存（同一個 app、兩個獨立 in-memory 庫）。
"""
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


def _post_set(client, **kw):
    body = dict(
        session_id="s1", annotator_id="cli1", annotator_role="client",
        audio_id="refA", audio_role="ref", version=0, reading_type="perceived",
        values={"valence": 0.9, "emotional_warmth": 0.25},
    )
    body.update(kw)
    return client.post("/api/alignment/readings", json=body)


# ── save + validation ─────────────────────────────────────────────────────
def test_bgm_dimensions_endpoint(align_client):
    r = align_client.get("/api/alignment/dimensions")
    dims = r.json()["dimensions"]
    assert [d["key"] for d in dims] == [
        "valence", "tension_direction", "emotional_warmth", "world_immersion",
    ]
    ew = next(d for d in dims if d["key"] == "emotional_warmth")
    assert ew["display_name"] == "柔烈度"
    assert ew["mid_anchor"] == "柔中帶亮"
    assert ew["client_question"]


def test_save_readings_persists(align_client):
    r = _post_set(align_client)
    assert r.status_code == 200
    assert r.json()["saved"] == 2


def test_save_rejects_bad_role(align_client):
    r = _post_set(align_client, annotator_role="boss")
    assert r.status_code == 400
    assert "annotator_role" in r.json()["detail"]


def test_save_rejects_value_out_of_range(align_client):
    r = _post_set(align_client, values={"valence": 1.5})
    assert r.status_code == 400
    assert "0-1" in r.json()["detail"]


def test_save_rejects_unknown_dimension(align_client):
    r = _post_set(align_client, values={"arousal": 0.5})
    assert r.status_code == 400


def test_save_is_upsert_not_duplicate(align_client):
    _post_set(align_client, values={"valence": 0.9})
    _post_set(align_client, values={"valence": 0.4})  # 同身分再存
    r = align_client.get("/api/alignment/readings", params={"session_id": "s1"})
    sets = r.json()["sets"]
    assert len(sets) == 1
    assert sets[0]["values"] == {"valence": 0.4}  # 被覆寫，非疊加


# ── list ──────────────────────────────────────────────────────────────────
def test_list_groups_into_sets(align_client):
    _post_set(align_client, annotator_role="engineer", annotator_id="eng1")
    _post_set(align_client, annotator_role="client", annotator_id="cli1")
    r = align_client.get("/api/alignment/readings", params={"session_id": "s1"})
    assert len(r.json()["sets"]) == 2


# ── compare/pair ──────────────────────────────────────────────────────────
def test_compare_pair_engineer_vs_client(align_client):
    _post_set(align_client, annotator_role="engineer", annotator_id="eng1",
              values={"valence": 0.9})
    _post_set(align_client, annotator_role="client", annotator_id="cli1",
              values={"valence": 0.4})
    ident = dict(session_id="s1", audio_id="refA", audio_role="ref",
                 version=0, reading_type="perceived")
    r = align_client.post("/api/alignment/compare/pair", json={
        "a": {**ident, "annotator_id": "eng1", "annotator_role": "engineer"},
        "b": {**ident, "annotator_id": "cli1", "annotator_role": "client"},
    })
    body = r.json()
    assert body["valid"] is True
    assert body["differing_axes"] == ["who"]
    assert body["diffs"]["valence"] == 0.5


def test_compare_pair_multi_axis_invalid(align_client):
    _post_set(align_client, annotator_role="engineer", annotator_id="eng1",
              reading_type="perceived", values={"valence": 0.9})
    _post_set(align_client, annotator_role="client", annotator_id="cli1",
              reading_type="target", values={"valence": 0.4})
    ident = dict(session_id="s1", audio_id="refA", audio_role="ref", version=0)
    r = align_client.post("/api/alignment/compare/pair", json={
        "a": {**ident, "annotator_id": "eng1", "annotator_role": "engineer",
              "reading_type": "perceived"},
        "b": {**ident, "annotator_id": "cli1", "annotator_role": "client",
              "reading_type": "target"},
    })
    assert r.json()["valid"] is False


def test_compare_pair_missing_set_404(align_client):
    ident = dict(session_id="s1", audio_id="refA", audio_role="ref",
                 version=0, reading_type="perceived")
    r = align_client.post("/api/alignment/compare/pair", json={
        "a": {**ident, "annotator_id": "eng1", "annotator_role": "engineer"},
        "b": {**ident, "annotator_id": "cli1", "annotator_role": "client"},
    })
    assert r.status_code == 404


# ── compare/variance ──────────────────────────────────────────────────────
def test_compare_variance_across_refs(align_client):
    _post_set(align_client, audio_id="refA",
              values={"valence": 0.9, "emotional_warmth": 0.25})
    _post_set(align_client, audio_id="refB",
              values={"valence": 0.85, "emotional_warmth": 0.80})
    r = align_client.post("/api/alignment/compare/variance", json={
        "session_id": "s1", "annotator_id": "cli1", "annotator_role": "client",
        "audio_role": "ref", "version": 0, "reading_type": "perceived",
        "audio_ids": ["refA", "refB"],
    })
    body = r.json()
    assert body["n"] == 2
    assert round(body["spread"]["valence"], 2) == 0.05
    assert round(body["spread"]["emotional_warmth"], 2) == 0.55
