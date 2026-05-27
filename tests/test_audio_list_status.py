"""Phase 12-C — list_audio 新增 status 欄位的測試。

跟既有 test_audio_upload.py / test_audio_analysis.py 分開,維持 SRP。
"""
from __future__ import annotations

import json

import pytest
from sqlmodel import Session

from src.models import Annotation, AudioFile


def _make_audio(engine, filename: str = "X_Base Game.wav", is_gold_locked: bool = False) -> str:
    with Session(engine) as s:
        a = AudioFile(
            filename=filename,
            game_name=filename.split("_")[0],
            game_stage=filename.split("_")[1].removesuffix(".wav"),
            is_gold_locked=is_gold_locked,
        )
        s.add(a); s.commit(); s.refresh(a)
        return a.id


def _make_annotation(engine, audio_id: str, annotator: str, **dims):
    defaults = {
        "valence": 0.5, "arousal": 0.5, "emotional_warmth": 0.5,
        "tension_direction": 0.5, "temporal_position": 0.5,
        "event_significance": 0.5, "world_immersion": 0.5,
    }
    defaults.update(dims)
    with Session(engine) as s:
        s.add(Annotation(
            audio_file_id=audio_id, annotator_id=annotator,
            is_complete=True,
            loop_capability=json.dumps([1.0]),
            source_type=json.dumps(["ambience"]),
            function_roles=json.dumps(["atmosphere"]),
            genre_tag=json.dumps([]), style_tag=json.dumps([]),
            **defaults,
        ))
        s.commit()


def test_list_audio_includes_status(client, in_memory_engine):
    """每筆音檔該回 status 欄位,5 種狀態都該正確 derive。"""
    _make_audio(in_memory_engine, "U_X.wav")  # untouched
    a_draft = _make_audio(in_memory_engine, "D_X.wav")
    _make_annotation(in_memory_engine, a_draft, "amber")
    a_cross = _make_audio(in_memory_engine, "C_X.wav")
    _make_annotation(in_memory_engine, a_cross, "amber", valence=0.1)
    _make_annotation(in_memory_engine, a_cross, "yyslin1024", valence=0.9)  # gap 0.8 > gate
    a_lock = _make_audio(in_memory_engine, "L_X.wav")
    _make_annotation(in_memory_engine, a_lock, "amber")
    _make_annotation(in_memory_engine, a_lock, "yyslin1024")  # aligned
    # is_gold_locked 已退役 → 即使設 flag 也不驅動 status（無 annotation → untouched）
    _make_audio(in_memory_engine, "G_X.wav", is_gold_locked=True)

    r = client.get("/api/audio")
    assert r.status_code == 200
    items = r.json()
    by_fn = {it["filename"]: it for it in items}
    assert by_fn["U_X.wav"]["status"] == "untouched"
    assert by_fn["D_X.wav"]["status"] == "creator_draft"
    assert by_fn["C_X.wav"]["status"] == "needs_arbitration"
    assert by_fn["L_X.wav"]["status"] == "fast_confirmable"
    assert by_fn["G_X.wav"]["status"] == "untouched"


def test_list_audio_preserves_annotator_flag(client, in_memory_engine):
    """is_annotated_by_current_annotator 跟 status 並存,行為不變。"""
    aid = _make_audio(in_memory_engine, "A_X.wav")
    _make_annotation(in_memory_engine, aid, "amber")  # creator only → creator_draft
    r = client.get("/api/audio?annotator=amber")
    items = r.json()
    assert items[0]["is_annotated_by_current_annotator"] is True
    assert items[0]["status"] == "creator_draft"
    # 換 annotator 視角,is_annotated 該 False,但 status 不變
    r2 = client.get("/api/audio?annotator=yyslin1024")
    items2 = r2.json()
    assert items2[0]["is_annotated_by_current_annotator"] is False
    assert items2[0]["status"] == "creator_draft"
