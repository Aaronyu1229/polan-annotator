"""自動晉升（auto-promote）— 對齊且非盲審的檔初標完成即 creator_ready。

覆蓋：
- maybe_auto_promote 單元：對齊非盲審→晉升(path=auto)、盲審→不晉升、gap 過大→不晉升、缺 industry→不晉升
- POST /api/annotations 觸發：amber/yyslin 標完對齊 → 回 auto_promoted=True 且狀態 creator_ready
- 盲審抽中 → 不自動晉升，仍 fast_confirmable
- POST /api/admin/arbitrate/auto-promote-all 補晉升既有對齊檔，跳過盲審
"""
from __future__ import annotations

import json

import pytest
from sqlmodel import Session, select

from src import annotators_loader
from src.audiofile_status import compute_audiofile_status, resolve_role_map
from src.auto_promote import maybe_auto_promote
from src.models import Annotation, Arbitration, AudioFile


@pytest.fixture
def tmp_annotators_config(tmp_path, monkeypatch):
    """amber=creator(admin), yyslin1024=industry, vic=audience。"""
    payload = {
        "amber": {
            "name": "Amber", "email": "a@x.com", "annotator_profile": "music_professional",
            "status": "active", "is_admin": True, "joined_at": "2025-12-15", "role": "creator",
        },
        "yyslin1024": {
            "name": "養心", "email": "y@x.com", "annotator_profile": "general_audience",
            "status": "active", "is_admin": False, "joined_at": "2026-02-01", "role": "industry",
        },
        "vic": {
            "name": "Vic", "email": "v@x.com", "annotator_profile": "general_audience",
            "status": "active", "is_admin": False, "joined_at": "2026-02-01", "role": "audience",
        },
    }
    path = tmp_path / "annotators_config.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(annotators_loader, "_CONFIG_PATH", path)
    return path


def _make_audio(engine, filename: str = "G_Base Game.wav") -> str:
    with Session(engine) as s:
        audio = AudioFile(
            filename=filename,
            game_name=filename.split("_")[0],
            game_stage=filename.split("_")[1].removesuffix(".wav"),
        )
        s.add(audio)
        s.commit()
        s.refresh(audio)
        return audio.id


def _add_annotation(engine, audio_id: str, annotator_id: str, valence: float) -> None:
    with Session(engine) as s:
        s.add(Annotation(
            audio_file_id=audio_id, annotator_id=annotator_id,
            valence=valence, arousal=0.5, emotional_warmth=0.5,
            tension_direction=0.5, temporal_position=0.5,
            event_significance=0.5, world_immersion=0.5,
            loop_capability=json.dumps([1.0]),
            source_type=json.dumps(["ambience"]),
            function_roles=json.dumps(["atmosphere"]),
            genre_tag=json.dumps([]), style_tag=json.dumps([]),
            is_complete=True,
        ))
        s.commit()


def _status(engine, audio_id: str) -> str:
    with Session(engine) as s:
        audio = s.get(AudioFile, audio_id)
        return compute_audiofile_status(audio, s)


# ── maybe_auto_promote 單元 ────────────────────────────────────────────────

def test_promote_aligned_non_blind(in_memory_engine, tmp_annotators_config, monkeypatch):
    monkeypatch.setattr("src.auto_promote.is_blind_audit", lambda _aid: False)
    audio_id = _make_audio(in_memory_engine)
    _add_annotation(in_memory_engine, audio_id, "amber", 0.50)
    _add_annotation(in_memory_engine, audio_id, "yyslin1024", 0.55)  # gap 0.05 ≤ 0.20

    with Session(in_memory_engine) as s:
        audio = s.get(AudioFile, audio_id)
        promoted = maybe_auto_promote(s, audio, resolve_role_map())
        s.commit()
    assert promoted is True
    assert _status(in_memory_engine, audio_id) == "creator_ready"


def test_promote_writes_path_auto(in_memory_engine, tmp_annotators_config, monkeypatch):
    """晉升來源可審計：path == 'auto'。"""
    monkeypatch.setattr("src.auto_promote.is_blind_audit", lambda _aid: False)
    audio_id = _make_audio(in_memory_engine)
    _add_annotation(in_memory_engine, audio_id, "amber", 0.50)
    _add_annotation(in_memory_engine, audio_id, "yyslin1024", 0.55)
    with Session(in_memory_engine) as s:
        audio = s.get(AudioFile, audio_id)
        maybe_auto_promote(s, audio, resolve_role_map())
        s.commit()
    with Session(in_memory_engine) as s:
        paths = {a.path for a in s.exec(
            select(Arbitration).where(Arbitration.audio_file_id == audio_id)
        ).all()}
    assert paths == {"auto"}


def test_no_promote_when_blind_audit(in_memory_engine, tmp_annotators_config, monkeypatch):
    monkeypatch.setattr("src.auto_promote.is_blind_audit", lambda _aid: True)
    audio_id = _make_audio(in_memory_engine)
    _add_annotation(in_memory_engine, audio_id, "amber", 0.50)
    _add_annotation(in_memory_engine, audio_id, "yyslin1024", 0.55)
    with Session(in_memory_engine) as s:
        audio = s.get(AudioFile, audio_id)
        promoted = maybe_auto_promote(s, audio, resolve_role_map())
        s.commit()
    assert promoted is False
    assert _status(in_memory_engine, audio_id) == "fast_confirmable"


def test_no_promote_when_gap_exceeds_gate(in_memory_engine, tmp_annotators_config, monkeypatch):
    monkeypatch.setattr("src.auto_promote.is_blind_audit", lambda _aid: False)
    audio_id = _make_audio(in_memory_engine)
    _add_annotation(in_memory_engine, audio_id, "amber", 0.10)
    _add_annotation(in_memory_engine, audio_id, "yyslin1024", 0.90)  # gap 0.80 > 0.20
    with Session(in_memory_engine) as s:
        audio = s.get(AudioFile, audio_id)
        promoted = maybe_auto_promote(s, audio, resolve_role_map())
        s.commit()
    assert promoted is False
    assert _status(in_memory_engine, audio_id) == "needs_arbitration"


def test_no_promote_when_industry_missing(in_memory_engine, tmp_annotators_config, monkeypatch):
    monkeypatch.setattr("src.auto_promote.is_blind_audit", lambda _aid: False)
    audio_id = _make_audio(in_memory_engine)
    _add_annotation(in_memory_engine, audio_id, "amber", 0.50)  # 只有 creator
    with Session(in_memory_engine) as s:
        audio = s.get(AudioFile, audio_id)
        promoted = maybe_auto_promote(s, audio, resolve_role_map())
        s.commit()
    assert promoted is False
    assert _status(in_memory_engine, audio_id) == "creator_draft"


# ── POST /api/annotations 觸發 ─────────────────────────────────────────────

def _complete_payload(audio_id: str, annotator_id: str, valence: float) -> dict:
    return {
        "audio_id": audio_id, "annotator_id": annotator_id,
        "valence": valence, "arousal": 0.5, "emotional_warmth": 0.5,
        "tension_direction": 0.5, "temporal_position": 0.5,
        "event_significance": 0.5, "world_immersion": 0.5,
        "loop_capability": [1.0], "source_type": ["ambience"],
        "function_roles": ["atmosphere"], "genre_tag": [],
        "worldview_tag": [], "style_tag": [], "notes": None,
    }


def test_post_completing_pair_auto_promotes(client, in_memory_engine, tmp_annotators_config, monkeypatch):
    """industry 先標 → amber 後標完成對齊 → 回 auto_promoted=True，狀態 creator_ready。"""
    monkeypatch.setattr("src.auto_promote.is_blind_audit", lambda _aid: False)
    audio_id = _make_audio(in_memory_engine)
    _add_annotation(in_memory_engine, audio_id, "yyslin1024", 0.55)

    r = client.post("/api/annotations", json=_complete_payload(audio_id, "amber", 0.50))
    assert r.status_code == 200, r.text
    assert r.json()["auto_promoted"] is True
    assert _status(in_memory_engine, audio_id) == "creator_ready"


def test_post_blind_audit_does_not_auto_promote(client, in_memory_engine, tmp_annotators_config, monkeypatch):
    monkeypatch.setattr("src.auto_promote.is_blind_audit", lambda _aid: True)
    audio_id = _make_audio(in_memory_engine)
    _add_annotation(in_memory_engine, audio_id, "yyslin1024", 0.55)

    r = client.post("/api/annotations", json=_complete_payload(audio_id, "amber", 0.50))
    assert r.status_code == 200, r.text
    assert r.json()["auto_promoted"] is False
    assert _status(in_memory_engine, audio_id) == "fast_confirmable"


def test_post_only_creator_no_promote(client, in_memory_engine, tmp_annotators_config, monkeypatch):
    monkeypatch.setattr("src.auto_promote.is_blind_audit", lambda _aid: False)
    audio_id = _make_audio(in_memory_engine)
    r = client.post("/api/annotations", json=_complete_payload(audio_id, "amber", 0.50))
    assert r.json()["auto_promoted"] is False
    assert _status(in_memory_engine, audio_id) == "creator_draft"


# ── 補晉升端點 ─────────────────────────────────────────────────────────────

def test_auto_promote_all_backfills_aligned(client, in_memory_engine, tmp_annotators_config, monkeypatch):
    monkeypatch.setattr("src.auto_promote.is_blind_audit", lambda _aid: False)
    aligned = _make_audio(in_memory_engine, "A_Base Game.wav")
    _add_annotation(in_memory_engine, aligned, "amber", 0.50)
    _add_annotation(in_memory_engine, aligned, "yyslin1024", 0.55)
    diverged = _make_audio(in_memory_engine, "D_Base Game.wav")
    _add_annotation(in_memory_engine, diverged, "amber", 0.10)
    _add_annotation(in_memory_engine, diverged, "yyslin1024", 0.90)

    r = client.post("/api/admin/arbitrate/auto-promote-all?annotator=amber")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["promoted"] == [aligned]
    assert _status(in_memory_engine, aligned) == "creator_ready"
    assert _status(in_memory_engine, diverged) == "needs_arbitration"


def test_auto_promote_all_skips_blind_audit(client, in_memory_engine, tmp_annotators_config, monkeypatch):
    monkeypatch.setattr("src.auto_promote.is_blind_audit", lambda _aid: True)
    aligned = _make_audio(in_memory_engine, "A_Base Game.wav")
    _add_annotation(in_memory_engine, aligned, "amber", 0.50)
    _add_annotation(in_memory_engine, aligned, "yyslin1024", 0.55)

    r = client.post("/api/admin/arbitrate/auto-promote-all?annotator=amber")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["promoted"] == []
    assert body["skipped_blind_audit"] == [aligned]
    assert _status(in_memory_engine, aligned) == "fast_confirmable"


def test_auto_promote_all_requires_admin(client, in_memory_engine, tmp_annotators_config):
    from src import main as main_module
    from src.middleware import require_auth
    main_module.app.dependency_overrides[require_auth] = lambda: {
        "annotator_id": "yyslin1024", "email": "y@x.com", "is_admin": False, "name": None,
    }
    try:
        r = client.post("/api/admin/arbitrate/auto-promote-all")
        assert r.status_code == 403
    finally:
        main_module.app.dependency_overrides.pop(require_auth, None)
