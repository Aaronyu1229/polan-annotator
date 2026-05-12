"""Phase 10 — audiofile_status 邏輯測試。

涵蓋:
- 5 個狀態 derive 正確性(untouched / draft / cross / lockable / gold)
- per_dim_spread 計算 + None 處理
- gold_lock_prerequisites:n<2 reject / spread 超門檻 reject / 都過 → eligible
- status_meets 過濾邏輯
- status_summary 聚合
"""
from __future__ import annotations

import json

import pytest
from sqlmodel import Session

from src.audiofile_status import (
    GOLD_MAX_SPREAD,
    compute_audiofile_status,
    gold_lock_prerequisites,
    per_dim_spread,
    status_meets,
    status_summary,
)
from src.models import Annotation, AudioFile


def _save_audio(engine, filename: str = "X_Base Game.wav", is_gold_locked: bool = False) -> AudioFile:
    with Session(engine) as s:
        audio = AudioFile(
            filename=filename,
            game_name=filename.split("_")[0],
            game_stage=filename.split("_")[1].removesuffix(".wav"),
            is_gold_locked=is_gold_locked,
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


# ---------------------------------------------------------------------------
# status_meets
# ---------------------------------------------------------------------------

def test_status_meets_ordering():
    assert status_meets("gold", "gold")
    assert status_meets("gold", "lockable")
    assert status_meets("gold", "untouched")
    assert status_meets("lockable", "cross_annotated")
    assert not status_meets("cross_annotated", "lockable")
    assert not status_meets("draft", "cross_annotated")
    assert status_meets("untouched", "untouched")


# ---------------------------------------------------------------------------
# per_dim_spread
# ---------------------------------------------------------------------------

def test_per_dim_spread_basic(in_memory_engine):
    aid = _save_audio(in_memory_engine).id
    _save_annotation(in_memory_engine, aid, "amber",
                     valence=0.3, arousal=0.5)
    _save_annotation(in_memory_engine, aid, "yyslin1024",
                     valence=0.7, arousal=0.5)

    with Session(in_memory_engine) as s:
        annotations = s.exec(
            __import__("sqlmodel").select(Annotation).where(Annotation.audio_file_id == aid)
        ).all()
        spreads = per_dim_spread(annotations)

    assert spreads["valence"] == pytest.approx(0.4)
    assert spreads["arousal"] == pytest.approx(0.0)


def test_per_dim_spread_single_value_returns_none(in_memory_engine):
    """單筆 annotation 沒法算 spread。"""
    aid = _save_audio(in_memory_engine).id
    _save_annotation(in_memory_engine, aid, "amber")

    with Session(in_memory_engine) as s:
        annotations = s.exec(
            __import__("sqlmodel").select(Annotation).where(Annotation.audio_file_id == aid)
        ).all()
        spreads = per_dim_spread(annotations)

    for v in spreads.values():
        assert v is None


# ---------------------------------------------------------------------------
# compute_audiofile_status
# ---------------------------------------------------------------------------

def test_status_untouched(in_memory_engine):
    audio = _save_audio(in_memory_engine)
    with Session(in_memory_engine) as s:
        audio = s.get(AudioFile, audio.id)
        assert compute_audiofile_status(audio, s) == "untouched"


def test_status_draft_when_one_annotator(in_memory_engine):
    audio = _save_audio(in_memory_engine)
    _save_annotation(in_memory_engine, audio.id, "amber")
    with Session(in_memory_engine) as s:
        audio = s.get(AudioFile, audio.id)
        assert compute_audiofile_status(audio, s) == "draft"


def test_status_lockable_when_two_annotators_tight_spread(in_memory_engine):
    """2 人標、每維差距 ≤ 0.20 → lockable。"""
    audio = _save_audio(in_memory_engine)
    _save_annotation(in_memory_engine, audio.id, "amber",  valence=0.5)
    _save_annotation(in_memory_engine, audio.id, "yyslin1024", valence=0.55)
    with Session(in_memory_engine) as s:
        audio = s.get(AudioFile, audio.id)
        assert compute_audiofile_status(audio, s) == "lockable"


def test_status_cross_annotated_when_spread_too_wide(in_memory_engine):
    """2 人標但 valence spread = 0.5 → cross_annotated(未達 lockable)。"""
    audio = _save_audio(in_memory_engine)
    _save_annotation(in_memory_engine, audio.id, "amber",  valence=0.2)
    _save_annotation(in_memory_engine, audio.id, "yyslin1024", valence=0.7)  # delta 0.5 > 0.20
    with Session(in_memory_engine) as s:
        audio = s.get(AudioFile, audio.id)
        assert compute_audiofile_status(audio, s) == "cross_annotated"


def test_status_gold_when_locked(in_memory_engine):
    """is_gold_locked=True 一律回 gold(無視 annotation 數)。"""
    audio = _save_audio(in_memory_engine, is_gold_locked=True)
    with Session(in_memory_engine) as s:
        audio = s.get(AudioFile, audio.id)
        assert compute_audiofile_status(audio, s) == "gold"


# ---------------------------------------------------------------------------
# gold_lock_prerequisites
# ---------------------------------------------------------------------------

def test_prereq_reject_when_only_one_annotator(in_memory_engine):
    audio = _save_audio(in_memory_engine)
    _save_annotation(in_memory_engine, audio.id, "amber")
    with Session(in_memory_engine) as s:
        audio = s.get(AudioFile, audio.id)
        result = gold_lock_prerequisites(audio, s)
    assert result["eligible"] is False
    assert any("2 位" in r for r in result["reasons"])


def test_prereq_reject_when_spread_exceeds_threshold(in_memory_engine):
    audio = _save_audio(in_memory_engine)
    _save_annotation(in_memory_engine, audio.id, "amber",  valence=0.1)
    _save_annotation(in_memory_engine, audio.id, "yyslin1024", valence=0.9)  # spread 0.8
    with Session(in_memory_engine) as s:
        audio = s.get(AudioFile, audio.id)
        result = gold_lock_prerequisites(audio, s)
    assert result["eligible"] is False
    assert any("spread" in r.lower() for r in result["reasons"])


def test_prereq_eligible_when_2_annotators_tight(in_memory_engine):
    audio = _save_audio(in_memory_engine)
    _save_annotation(in_memory_engine, audio.id, "amber",  valence=0.5)
    _save_annotation(in_memory_engine, audio.id, "yyslin1024", valence=0.55)
    with Session(in_memory_engine) as s:
        audio = s.get(AudioFile, audio.id)
        result = gold_lock_prerequisites(audio, s)
    assert result["eligible"] is True
    assert result["reasons"] == []


def test_prereq_reject_already_locked(in_memory_engine):
    audio = _save_audio(in_memory_engine, is_gold_locked=True)
    with Session(in_memory_engine) as s:
        audio = s.get(AudioFile, audio.id)
        result = gold_lock_prerequisites(audio, s)
    assert result["eligible"] is False
    assert "已 gold-locked" in result["reasons"][0]


# ---------------------------------------------------------------------------
# status_summary
# ---------------------------------------------------------------------------

def test_status_summary_counts(in_memory_engine):
    """建 5 個 audio、各種狀態,驗 summary 數對。"""
    # untouched
    _save_audio(in_memory_engine, "A1_X.wav")
    # draft
    a2 = _save_audio(in_memory_engine, "A2_X.wav")
    _save_annotation(in_memory_engine, a2.id, "amber")
    # cross_annotated (寬 spread)
    a3 = _save_audio(in_memory_engine, "A3_X.wav")
    _save_annotation(in_memory_engine, a3.id, "amber",  valence=0.1)
    _save_annotation(in_memory_engine, a3.id, "yyslin1024", valence=0.9)
    # lockable (緊 spread)
    a4 = _save_audio(in_memory_engine, "A4_X.wav")
    _save_annotation(in_memory_engine, a4.id, "amber")
    _save_annotation(in_memory_engine, a4.id, "yyslin1024")
    # gold
    _save_audio(in_memory_engine, "A5_X.wav", is_gold_locked=True)

    with Session(in_memory_engine) as s:
        summary = status_summary(s)

    assert summary["untouched"] == 1
    assert summary["draft"] == 1
    assert summary["cross_annotated"] == 1
    assert summary["lockable"] == 1
    assert summary["gold"] == 1
    assert summary["total"] == 5
