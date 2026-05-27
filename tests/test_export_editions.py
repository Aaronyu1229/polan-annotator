"""Phase 6 — Creator Edition + Dual-View 匯出。"""
from __future__ import annotations

import json

from sqlmodel import Session

from src.arbitration import write_arbitration
from src.export_editions import build_creator_edition, build_dual_view
from src.models import Annotation, AudioFile

_DIMS = ["valence", "arousal", "emotional_warmth", "tension_direction",
         "temporal_position", "event_significance", "world_immersion"]
_FULL_VALUES = {
    **{d: 0.5 for d in _DIMS},
    "loop_capability": [1.0], "source_type": ["ambience"],
    "function_roles": ["atmosphere"], "genre_tag": ["博弈"],
    "worldview_tag": ["casino"], "style_tag": ["orchestral"],
}


def _audio(engine, fn):
    with Session(engine) as s:
        a = AudioFile(filename=fn, game_name=fn.split("_")[0], game_stage="Base Game")
        s.add(a); s.commit(); s.refresh(a)
        return a.id


def _ann(engine, audio_id, who, **dims):
    base = {d: 0.5 for d in _DIMS}
    base.update(dims)
    with Session(engine) as s:
        s.add(Annotation(
            audio_file_id=audio_id, annotator_id=who, is_complete=True,
            loop_capability=json.dumps([1.0]), source_type=json.dumps(["ambience"]),
            function_roles=json.dumps(["atmosphere"]), genre_tag=json.dumps(["博弈"]),
            worldview_tag=json.dumps(["casino"]), style_tag=json.dumps(["orchestral"]),
            **base,
        ))
        s.commit()


def test_creator_edition_uses_arbitration_values(in_memory_engine):
    aid = _audio(in_memory_engine, "C_X.wav")
    _ann(in_memory_engine, aid, "amber", valence=0.5)
    _ann(in_memory_engine, aid, "yyslin1024", valence=0.55)  # aligned
    # 寫全 13 欄仲裁（valence 仲裁成 0.8，刻意 ≠ raw 0.5 以驗證取仲裁值）
    vals = {**_FULL_VALUES, "valence": 0.8}
    with Session(in_memory_engine) as s:
        write_arbitration(s, audio_id=aid, fields_values=vals,
                          path="fast", notes=None, arbitrated_by="amber")
        s.commit()
        data = build_creator_edition(s)

    assert data["edition"] == "creator"
    assert data["schema_version"] == "1.0.0"
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["dimensions"]["valence"] == 0.8  # 取仲裁值，非 raw 0.5
    assert item["dimension_sources"]["valence"] == "creator_arbitrated"
    assert item["dimension_sources"]["tonal_noise_ratio"] == "librosa_v1"
    assert item["arbitration_meta"]["valence"]["path"] == "fast"
    assert item["genre_tag"] == ["博弈"]


def test_creator_edition_excludes_non_ready(in_memory_engine):
    aid = _audio(in_memory_engine, "D_X.wav")
    _ann(in_memory_engine, aid, "amber")  # creator only, no arbitration → not creator_ready
    with Session(in_memory_engine) as s:
        data = build_creator_edition(s)
    assert data["items"] == []


def test_dual_view_pairs_industry_and_audience(in_memory_engine):
    aid = _audio(in_memory_engine, "V_X.wav")
    _ann(in_memory_engine, aid, "yyslin1024", arousal=0.5)
    _ann(in_memory_engine, aid, "vvgosick", arousal=0.95)  # industry-audience gap 0.45 > 0.40
    with Session(in_memory_engine) as s:
        data = build_dual_view(s)

    assert data["edition"] == "dual_view"
    assert data["meta"]["audience_n"] == 1
    assert "single end-user reference" in data["meta"]["disclaimer"]
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["industry_view"]["arousal"] == 0.5
    assert item["audience_view"]["arousal"] == 0.95
    assert "arousal" in item["product_divergence_dims"]


def test_dual_view_excludes_when_audience_missing(in_memory_engine):
    aid = _audio(in_memory_engine, "W_X.wav")
    _ann(in_memory_engine, aid, "yyslin1024")  # industry only
    with Session(in_memory_engine) as s:
        data = build_dual_view(s)
    assert data["items"] == []


def test_creator_edition_endpoint(client, in_memory_engine):
    r = client.get("/api/export/creator_edition.json")
    assert r.status_code == 200
    assert r.json()["edition"] == "creator"


def test_dual_view_endpoint(client, in_memory_engine):
    r = client.get("/api/export/dual_view.json")
    assert r.status_code == 200
    assert r.json()["edition"] == "dual_view"
