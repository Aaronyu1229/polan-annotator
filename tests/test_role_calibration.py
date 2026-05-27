"""Phase 7 — per-role 校準指標。role 取自真實 config（amber/yyslin1024/vvgosick）。"""
from __future__ import annotations

import json

from sqlmodel import Session, select

from src.models import Annotation, AnnotationSnapshot, AudioFile
from src.role_calibration import (
    audience_intra_rater,
    industry_alignment,
    role_aware_calibration_status,
    self_mae,
)

_DIMS = ["valence", "arousal", "emotional_warmth", "tension_direction",
         "temporal_position", "event_significance", "world_immersion"]


def _audio(engine, fn):
    with Session(engine) as s:
        a = AudioFile(filename=fn, game_name=fn.split("_")[0], game_stage="Base")
        s.add(a); s.commit(); s.refresh(a)
        return a.id


def _ann(engine, audio_id, who, val):
    with Session(engine) as s:
        s.add(Annotation(
            audio_file_id=audio_id, annotator_id=who, is_complete=True,
            loop_capability=json.dumps([1.0]), source_type=json.dumps(["ambience"]),
            function_roles=json.dumps(["atmosphere"]),
            **{d: val for d in _DIMS},
        ))
        s.commit()


def _snap(engine, audio_id, who, val, pass_no=2):
    with Session(engine) as s:
        s.add(AnnotationSnapshot(
            audio_file_id=audio_id, annotator_id=who, pass_no=pass_no,
            **{d: val for d in _DIMS},
        ))
        s.commit()


def test_self_mae_pass(in_memory_engine):
    for i in range(3):  # 3 audios × 7 dims = 21 ≥ CALIB_MIN_N(20)
        aid = _audio(in_memory_engine, f"S{i}_x.wav")
        _ann(in_memory_engine, aid, "amber", 0.5)
        _snap(in_memory_engine, aid, "amber", 0.55)  # |Δ|=0.05
    with Session(in_memory_engine) as s:
        r = self_mae(s, "amber")
    assert r["insufficient"] is False
    assert r["value"] == 0.05
    assert r["pass"] is True  # 0.05 < 0.10


def test_self_mae_insufficient(in_memory_engine):
    aid = _audio(in_memory_engine, "S_x.wav")  # 只 1 audio = 7 < 20
    _ann(in_memory_engine, aid, "amber", 0.5)
    _snap(in_memory_engine, aid, "amber", 0.55)
    with Session(in_memory_engine) as s:
        assert self_mae(s, "amber")["insufficient"] is True


def test_industry_alignment_upper_bound_only(in_memory_engine):
    # yyslin == amber → MAE 0 → **pass**（拿掉下界，低 MAE 不再 fail）
    aid = _audio(in_memory_engine, "I_x.wav")
    _ann(in_memory_engine, aid, "amber", 0.5)
    _ann(in_memory_engine, aid, "yyslin1024", 0.5)
    with Session(in_memory_engine) as s:
        r = industry_alignment(s, "yyslin1024")
    assert r["value"] == 0.0
    assert r["pass"] is True


def test_industry_alignment_fails_over_upper(in_memory_engine):
    aid = _audio(in_memory_engine, "I2_x.wav")
    _ann(in_memory_engine, aid, "amber", 0.2)
    _ann(in_memory_engine, aid, "yyslin1024", 0.7)  # |Δ|=0.5 > 0.20
    with Session(in_memory_engine) as s:
        assert industry_alignment(s, "yyslin1024")["pass"] is False


def test_audience_intra_rater(in_memory_engine):
    for i in range(3):
        aid = _audio(in_memory_engine, f"A{i}_x.wav")
        _ann(in_memory_engine, aid, "vvgosick", 0.5)
        _snap(in_memory_engine, aid, "vvgosick", 0.5)  # 完全一致 → 1-0=1.0
    with Session(in_memory_engine) as s:
        r = audience_intra_rater(s, "vvgosick")
    assert r["value"] == 1.0
    assert r["pass"] is True


def test_role_aware_dispatch(in_memory_engine):
    aid = _audio(in_memory_engine, "R_x.wav")
    _ann(in_memory_engine, aid, "yyslin1024", 0.5)
    _ann(in_memory_engine, aid, "amber", 0.5)
    with Session(in_memory_engine) as s:
        assert role_aware_calibration_status(s, "yyslin1024")["metric"] == "vs_creator_mae"
        assert role_aware_calibration_status(s, "amber")["metric"] == "self_mae"
        assert role_aware_calibration_status(s, "vvgosick")["metric"] == "intra_rater"


# ─── 端點 ─────────────────────────────────────────────────────────

def test_retest_rejected_within_washout(client, in_memory_engine):
    aid = _audio(in_memory_engine, "RT_x.wav")
    _ann(in_memory_engine, aid, "amber", 0.5)  # created_at = now → wash-out 未滿
    r = client.post("/api/calibration/retest", json={
        "audio_id": aid, "annotator_id": "amber",
        "values": {d: 0.55 for d in _DIMS},
    })
    assert r.status_code == 409
    assert "wash-out" in r.json()["detail"]


def test_retest_writes_snapshot_after_washout(client, in_memory_engine):
    from datetime import UTC, datetime, timedelta
    aid = _audio(in_memory_engine, "RT2_x.wav")
    _ann(in_memory_engine, aid, "amber", 0.5)
    # 把 created_at 倒推 20 天
    with Session(in_memory_engine) as s:
        a = s.exec(select(Annotation).where(Annotation.audio_file_id == aid)).one()
        a.created_at = datetime.now(UTC) - timedelta(days=20)
        s.add(a); s.commit()
    r = client.post("/api/calibration/retest", json={
        "audio_id": aid, "annotator_id": "amber",
        "values": {d: 0.6 for d in _DIMS},
    })
    assert r.status_code == 200, r.text
    assert r.json()["pass_no"] == 2


def test_calibration_status_endpoint(client, in_memory_engine):
    aid = _audio(in_memory_engine, "CS_x.wav")
    _ann(in_memory_engine, aid, "amber", 0.5)
    _ann(in_memory_engine, aid, "yyslin1024", 0.5)
    r = client.get("/api/admin/calibration-status/yyslin1024")
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "industry"
    assert r.json()["metric"] == "vs_creator_mae"
