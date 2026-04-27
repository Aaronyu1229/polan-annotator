"""Phase 3 calibration API 測試。

覆蓋：
- /api/calibration/queue：reference 已標、self 未標 → list；annotator==reference → 空；
  reference 沒任何標註 → 空
- /api/calibration/reference/{id}：404 if reference 沒標、200 if 有
"""
from __future__ import annotations

import json

from sqlmodel import Session

from src.models import Annotation, AudioFile


_DEFAULT_DIMS = {
    "valence": 0.6,
    "arousal": 0.6,
    "emotional_warmth": 0.5,
    "tension_direction": 0.4,
    "temporal_position": 0.5,
    "event_significance": 0.6,
    "tonal_noise_ratio": 0.7,
    "spectral_density": 0.5,
    "world_immersion": 0.6,
}


def _make_audio(s: Session, filename: str) -> AudioFile:
    a = AudioFile(
        filename=filename,
        game_name=filename.split("_")[0],
        game_stage=filename.split("_")[1].removesuffix(".wav"),
    )
    s.add(a)
    s.commit()
    s.refresh(a)
    return a


def _make_ann(
    s: Session, audio_id: str, annotator_id: str, *, is_complete: bool = True
) -> Annotation:
    ann = Annotation(
        audio_file_id=audio_id,
        annotator_id=annotator_id,
        loop_capability=json.dumps([1.0]),
        source_type=json.dumps(["ambience"]),
        function_roles=json.dumps(["atmosphere"]),
        genre_tag=json.dumps(["博弈"]),
        worldview_tag="asian_mythology",
        style_tag=json.dumps(["chinese_traditional"]),
        is_complete=is_complete,
        **_DEFAULT_DIMS,
    )
    s.add(ann)
    s.commit()
    s.refresh(ann)
    return ann


# ─── /api/calibration/queue ─────────────────────────────

def test_queue_empty_when_reference_has_nothing(client, in_memory_engine):
    """amber 沒 is_complete 紀錄 → bob 的 queue 為空。"""
    r = client.get("/api/calibration/queue", params={"annotator": "bob"})
    assert r.status_code == 200
    assert r.json() == []


def test_queue_returns_reference_minus_self(client, in_memory_engine):
    with Session(in_memory_engine) as s:
        a1 = _make_audio(s, "A_Base Game.wav")
        a2 = _make_audio(s, "B_Base Game.wav")
        a3 = _make_audio(s, "C_Base Game.wav")
        a1_id, a2_id, a3_id = a1.id, a2.id, a3.id
        # amber 標 a1 + a2 + a3；bob 已 is_complete 標 a1 → queue = [a2, a3]
        for aid in [a1_id, a2_id, a3_id]:
            _make_ann(s, aid, "amber")
        _make_ann(s, a1_id, "bob")

    r = client.get("/api/calibration/queue", params={"annotator": "bob"})
    assert r.status_code == 200
    queue = r.json()
    ids = sorted(item["id"] for item in queue)
    assert ids == sorted([a2_id, a3_id])


def test_queue_excludes_reference_own_request(client, in_memory_engine):
    """annotator=amber（reference 自己）→ queue 必定空。"""
    with Session(in_memory_engine) as s:
        a1 = _make_audio(s, "A_Base Game.wav")
        _make_ann(s, a1.id, "amber")
    r = client.get("/api/calibration/queue", params={"annotator": "amber"})
    assert r.status_code == 200
    assert r.json() == []


def test_queue_ignores_reference_drafts(client, in_memory_engine):
    """amber 對 a1 是 draft（is_complete=False）→ a1 不在 queue。"""
    with Session(in_memory_engine) as s:
        a1 = _make_audio(s, "A_Base Game.wav")
        _make_ann(s, a1.id, "amber", is_complete=False)
    r = client.get("/api/calibration/queue", params={"annotator": "bob"})
    assert r.status_code == 200
    assert r.json() == []


# ─── /api/calibration/reference/{id} ────────────────────

def test_reference_endpoint_404_when_amber_not_standard(client, in_memory_engine):
    with Session(in_memory_engine) as s:
        a1 = _make_audio(s, "A_Base Game.wav")
    r = client.get(f"/api/calibration/reference/{a1.id}")
    assert r.status_code == 404


def test_reference_endpoint_returns_amber_annotation(client, in_memory_engine):
    with Session(in_memory_engine) as s:
        a1 = _make_audio(s, "A_Base Game.wav")
        a1_id = a1.id
        _make_ann(s, a1_id, "amber")

    r = client.get(f"/api/calibration/reference/{a1_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["annotator_id"] == "amber"
    assert data["valence"] == 0.6
    # multi-select fields 應 decode 為 list
    assert isinstance(data["loop_capability"], list)
    assert data["loop_capability"] == [1.0]
    assert data["source_type"] == ["ambience"]


def test_reference_endpoint_404_when_amber_only_draft(client, in_memory_engine):
    """amber 對該 audio 是 draft → reference endpoint 仍 404。"""
    with Session(in_memory_engine) as s:
        a1 = _make_audio(s, "A_Base Game.wav")
        a1_id = a1.id
        _make_ann(s, a1_id, "amber", is_complete=False)
    r = client.get(f"/api/calibration/reference/{a1_id}")
    assert r.status_code == 404
