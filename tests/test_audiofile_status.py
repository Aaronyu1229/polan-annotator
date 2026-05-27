"""audiofile_status DB-path 測試（三角架構）。

純函式狀態邏輯在 tests/test_audiofile_status_v2.py；這裡測 DB 版 compute_audiofile_status、
status_summary、status_meets。角色 id 取自真實 config：creator=amber / industry=yyslin1024 /
audience=vvgosick。
"""
from __future__ import annotations

import json

from sqlmodel import Session

from src.audiofile_status import (
    compute_audiofile_status,
    status_meets,
    status_summary,
)
from src.models import Annotation, AudioFile


def _save_audio(engine, filename: str = "X_Base Game.wav") -> AudioFile:
    with Session(engine) as s:
        audio = AudioFile(
            filename=filename,
            game_name=filename.split("_")[0],
            game_stage=filename.split("_")[1].removesuffix(".wav"),
        )
        s.add(audio); s.commit(); s.refresh(audio)
        return audio


def _save_annotation(engine, audio_id: str, annotator: str, **dims):
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


# ─── compute_audiofile_status (DB path) ───────────────────────────

def test_untouched_when_no_annotations(in_memory_engine):
    audio = _save_audio(in_memory_engine)
    with Session(in_memory_engine) as s:
        assert compute_audiofile_status(s.get(AudioFile, audio.id), s) == "untouched"


def test_creator_only_is_creator_draft(in_memory_engine):
    audio = _save_audio(in_memory_engine)
    _save_annotation(in_memory_engine, audio.id, "amber")
    with Session(in_memory_engine) as s:
        assert compute_audiofile_status(s.get(AudioFile, audio.id), s) == "creator_draft"


def test_creator_industry_aligned_is_fast_confirmable(in_memory_engine):
    audio = _save_audio(in_memory_engine)
    _save_annotation(in_memory_engine, audio.id, "amber", valence=0.5)
    _save_annotation(in_memory_engine, audio.id, "yyslin1024", valence=0.55)
    with Session(in_memory_engine) as s:
        assert compute_audiofile_status(s.get(AudioFile, audio.id), s) == "fast_confirmable"


def test_audience_divergence_does_not_block(in_memory_engine):
    # 回歸舊 spread bug：audience (vvgosick) 大幅偏離不可卡住狀態
    audio = _save_audio(in_memory_engine)
    _save_annotation(in_memory_engine, audio.id, "amber", valence=0.5)
    _save_annotation(in_memory_engine, audio.id, "yyslin1024", valence=0.55)
    _save_annotation(in_memory_engine, audio.id, "vvgosick", valence=0.95)
    with Session(in_memory_engine) as s:
        assert compute_audiofile_status(s.get(AudioFile, audio.id), s) == "fast_confirmable"


def test_creator_industry_gap_over_gate_needs_arbitration(in_memory_engine):
    audio = _save_audio(in_memory_engine)
    _save_annotation(in_memory_engine, audio.id, "amber", valence=0.5)
    _save_annotation(in_memory_engine, audio.id, "yyslin1024", valence=0.85)  # gap 0.35
    with Session(in_memory_engine) as s:
        assert compute_audiofile_status(s.get(AudioFile, audio.id), s) == "needs_arbitration"


# ─── status_meets ─────────────────────────────────────────────────

def test_status_meets_ordering():
    assert status_meets("creator_ready", "fast_confirmable")
    assert status_meets("fast_confirmable", "untouched")
    assert not status_meets("untouched", "fast_confirmable")
    # 舊 alias 仍可比對（export min_status 向後相容）
    assert status_meets("creator_ready", "gold")
    assert status_meets("fast_confirmable", "lockable")


# ─── status_summary ───────────────────────────────────────────────

def test_status_summary_counts(in_memory_engine):
    a1 = _save_audio(in_memory_engine, "A_Base Game.wav")
    a2 = _save_audio(in_memory_engine, "B_Base Game.wav")  # noqa: F841 — untouched
    _save_annotation(in_memory_engine, a1.id, "amber", valence=0.5)
    _save_annotation(in_memory_engine, a1.id, "yyslin1024", valence=0.55)
    with Session(in_memory_engine) as s:
        summary = status_summary(s)
    assert summary["total"] == 2
    assert summary["fast_confirmable"] == 1
    assert summary["untouched"] == 1
