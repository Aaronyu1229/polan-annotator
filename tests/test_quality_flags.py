"""Phase 5 — quality_flags 聚合 + audience straight-lining。"""
from __future__ import annotations

import json

from sqlmodel import Session

from src.models import Annotation, AudioFile
from src.quality_flags import aggregate_quality, audience_straight_lining

ROLE_MAP = {"creator": "amber", "industry": "yyslin1024", "audience": "vvgosick"}


def _audio(engine, fn):
    with Session(engine) as s:
        a = AudioFile(filename=fn, game_name=fn.split("_")[0], game_stage="Base Game")
        s.add(a); s.commit(); s.refresh(a)
        return a.id


def _ann(engine, audio_id, who, **dims):
    base = {d: 0.5 for d in
            ["valence", "arousal", "emotional_warmth", "tension_direction",
             "temporal_position", "event_significance", "world_immersion"]}
    base.update(dims)
    with Session(engine) as s:
        s.add(Annotation(
            audio_file_id=audio_id, annotator_id=who, is_complete=True,
            loop_capability=json.dumps([1.0]), source_type=json.dumps(["ambience"]),
            function_roles=json.dumps(["atmosphere"]), genre_tag=json.dumps([]),
            style_tag=json.dumps([]), **base,
        ))
        s.commit()


def test_industry_divergence_counted_and_recommendation_threshold(in_memory_engine):
    # 3 檔 valence creator-industry gap 0.5 (>0.30) → 達 RECAL_MIN_FILES=3 → 建議重新校準
    for i in range(3):
        aid = _audio(in_memory_engine, f"G{i}_x.wav")
        _ann(in_memory_engine, aid, "amber", valence=0.2)
        _ann(in_memory_engine, aid, "yyslin1024", valence=0.7)
    with Session(in_memory_engine) as s:
        q = aggregate_quality(s, ROLE_MAP)
    assert q["industry_divergence_by_dim"]["valence"]["count"] == 3
    assert "valence" in q["recalibration_recommended_dims"]


def test_two_files_below_recal_threshold(in_memory_engine):
    for i in range(2):  # 只 2 檔 → 不建議（< 3）
        aid = _audio(in_memory_engine, f"H{i}_x.wav")
        _ann(in_memory_engine, aid, "amber", valence=0.2)
        _ann(in_memory_engine, aid, "yyslin1024", valence=0.7)
    with Session(in_memory_engine) as s:
        q = aggregate_quality(s, ROLE_MAP)
    assert q["industry_divergence_by_dim"]["valence"]["count"] == 2
    assert "valence" not in q["recalibration_recommended_dims"]


def test_product_divergence_file_listed(in_memory_engine):
    aid = _audio(in_memory_engine, "P_x.wav")
    _ann(in_memory_engine, aid, "amber", arousal=0.5)
    _ann(in_memory_engine, aid, "yyslin1024", arousal=0.5)
    _ann(in_memory_engine, aid, "vvgosick", arousal=0.95)  # industry-audience gap 0.45 > 0.40
    with Session(in_memory_engine) as s:
        q = aggregate_quality(s, ROLE_MAP)
    files = q["product_divergence_files"]
    assert len(files) == 1
    assert files[0]["filename"] == "P_x.wav"
    assert "arousal" in files[0]["dims"]


def test_audience_straight_lining_suspect():
    # 6 筆 Vic 全部每維同值 → distinct=1 → suspect
    anns = [Annotation(audio_file_id=f"a{i}", annotator_id="vvgosick",
                       valence=0.5, arousal=0.5, emotional_warmth=0.5,
                       tension_direction=0.5, temporal_position=0.5,
                       event_significance=0.5, world_immersion=0.5)
            for i in range(6)]
    r = audience_straight_lining(anns)
    assert r["suspect"] is True


def test_audience_varied_not_suspect():
    import random
    rng = random.Random(0)
    anns = [Annotation(audio_file_id=f"a{i}", annotator_id="vvgosick",
                       valence=rng.random(), arousal=rng.random(),
                       emotional_warmth=rng.random(), tension_direction=rng.random(),
                       temporal_position=rng.random(), event_significance=rng.random(),
                       world_immersion=rng.random())
            for i in range(10)]
    r = audience_straight_lining(anns)
    assert r["suspect"] is False
