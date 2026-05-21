"""校準報告 API 層 — admin / 非 admin 揭露 gating。

dependency_overrides[require_auth] 模擬登入者（沿用 test_annotator_detail_api.py 模式）。
"""
from __future__ import annotations

import json

from sqlmodel import Session

import src.main as main_module
from src.models import Annotation, AudioFile


def _override_user(annotator_id: str, *, is_admin: bool):
    from src.middleware import require_auth
    main_module.app.dependency_overrides[require_auth] = lambda: {
        "annotator_id": annotator_id,
        "is_admin": is_admin,
        "name": None,
    }


def _seed(engine):
    with Session(engine) as s:
        a = AudioFile(filename="G_Free Game.wav", game_name="G", game_stage="Free Game")
        s.add(a)
        s.commit()
        s.refresh(a)
        aid = a.id
        common = dict(
            is_complete=True,
            loop_capability=json.dumps([1.0]),
            source_type=json.dumps(["ambience"]),
            function_roles=json.dumps(["atmosphere"]),
            genre_tag=json.dumps([]),
            style_tag=json.dumps([]),
            arousal=0.5, emotional_warmth=0.5, tension_direction=0.5,
            temporal_position=0.5, event_significance=0.5, world_immersion=0.5,
        )
        s.add(Annotation(audio_file_id=aid, annotator_id="amber", valence=0.9, **common))
        s.add(Annotation(audio_file_id=aid, annotator_id="vvgosick", valence=0.2, **common))
        s.commit()


def test_admin_sees_scatter_and_top(client, in_memory_engine):
    _seed(in_memory_engine)
    _override_user("amber", is_admin=True)
    r = client.get("/api/calibration/report?annotator=vvgosick")
    assert r.status_code == 200
    body = r.json()
    assert "scatter_data" in body
    assert "top_deviations" in body
    assert body["overall"]["recommendation"] in {
        "approved", "needs_training", "not_recommended",
    }


def test_non_admin_no_scatter(client, in_memory_engine):
    _seed(in_memory_engine)
    _override_user("vvgosick", is_admin=False)
    r = client.get("/api/calibration/report?annotator=vvgosick")
    assert r.status_code == 200
    body = r.json()
    assert "scatter_data" not in body
    assert "top_deviations" not in body
    assert body["overall"] is not None


def test_amber_is_reference(client, in_memory_engine):
    _seed(in_memory_engine)
    _override_user("amber", is_admin=True)
    r = client.get("/api/calibration/report?annotator=amber")
    assert r.status_code == 200
    assert r.json()["is_reference"] is True

