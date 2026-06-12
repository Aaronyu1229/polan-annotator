"""audience(Vic)可信度 — 極端共識探針 + 三訊號合成狀態。"""
from __future__ import annotations

import json

from sqlmodel import Session

from src.audience_credibility import extreme_consensus_sanity, vic_credibility
from src.export_editions import build_dual_view
from src.models import Annotation, AudioFile

_DIMS = ["valence", "arousal", "emotional_warmth", "tension_direction",
         "temporal_position", "event_significance", "world_immersion"]
_ROLE_MAP = {"creator": "amber", "industry": "yyslin1024", "audience": "vvgosick"}


def _audio(engine, fn):
    with Session(engine) as s:
        a = AudioFile(filename=fn, game_name=fn.split("_")[0], game_stage="Base Game")
        s.add(a); s.commit(); s.refresh(a)
        return a.id


def _ann(engine, audio_id, who, value):
    """value: scalar → 全 7 維同值；dict → 逐維覆寫(其餘 0.5)。"""
    dims = {d: value for d in _DIMS} if isinstance(value, (int, float)) else {**{d: 0.5 for d in _DIMS}, **value}
    with Session(engine) as s:
        s.add(Annotation(
            audio_file_id=audio_id, annotator_id=who, is_complete=True,
            loop_capability=json.dumps([1.0]), source_type=json.dumps(["ambience"]),
            function_roles=json.dumps(["atmosphere"]), genre_tag=json.dumps([]),
            style_tag=json.dumps([]), **dims,
        ))
        s.commit()


# ── extreme_consensus_sanity ───────────────────────────────────────────────

def test_extreme_consensus_counts_cross_midline_violations(in_memory_engine):
    # A: yyslin 低極端 0.05、Vic 同半邊 0.2 → 0 違反（合法分歧）
    a = _audio(in_memory_engine, "A_X.wav")
    _ann(in_memory_engine, a, "yyslin1024", 0.05)
    _ann(in_memory_engine, a, "vvgosick", 0.2)
    # B: yyslin 高極端 0.95、Vic 跨中線 0.1 → 7 違反
    b = _audio(in_memory_engine, "B_X.wav")
    _ann(in_memory_engine, b, "yyslin1024", 0.95)
    _ann(in_memory_engine, b, "vvgosick", 0.1)

    r = extreme_consensus_sanity(in_memory_engine_session(in_memory_engine), _ROLE_MAP)
    assert r["checked"] == 14          # 2 檔 × 7 維極端
    assert r["violations"] == 7        # 只有 B 跨中線
    assert r["violation_rate"] == 0.5
    assert r["insufficient"] is False
    assert r["pass"] is False          # 0.5 > 0.10
    assert r["violation_audio_ids"] == [b]


def test_extreme_consensus_pass_when_same_side(in_memory_engine):
    for i, fn in enumerate(["P1_X.wav", "P2_X.wav"]):
        aid = _audio(in_memory_engine, fn)
        _ann(in_memory_engine, aid, "yyslin1024", 0.05)      # 低極端
        _ann(in_memory_engine, aid, "vvgosick", 0.2 + i * 0.05)  # 同半邊
    r = extreme_consensus_sanity(in_memory_engine_session(in_memory_engine), _ROLE_MAP)
    assert r["checked"] == 14
    assert r["violations"] == 0
    assert r["pass"] is True


def test_extreme_consensus_insufficient_below_min_probes(in_memory_engine):
    a = _audio(in_memory_engine, "S_X.wav")
    _ann(in_memory_engine, a, "yyslin1024", {"valence": 0.05})  # 只 1 維極端
    _ann(in_memory_engine, a, "vvgosick", 0.5)
    r = extreme_consensus_sanity(in_memory_engine_session(in_memory_engine), _ROLE_MAP)
    assert r["checked"] == 1
    assert r["insufficient"] is True
    assert r["pass"] is None


def test_legit_divergence_not_a_violation(in_memory_engine):
    """yyslin 0.05、Vic 0.45 — 同低半邊、沒那麼極端 → 不算違反（保護賣點）。"""
    a = _audio(in_memory_engine, "L_X.wav")
    _ann(in_memory_engine, a, "yyslin1024", 0.05)
    _ann(in_memory_engine, a, "vvgosick", 0.45)
    r = extreme_consensus_sanity(in_memory_engine_session(in_memory_engine), _ROLE_MAP)
    assert r["violations"] == 0


# ── vic_credibility 合成狀態 ───────────────────────────────────────────────

def test_vic_credibility_trusted(in_memory_engine):
    # 5 檔：yyslin 全低極端 0.05；Vic 同低半邊但跨檔有方差(0.1~0.3)
    vic_vals = [0.10, 0.20, 0.30, 0.15, 0.25]
    for i, vv in enumerate(vic_vals):
        aid = _audio(in_memory_engine, f"T{i}_X.wav")
        _ann(in_memory_engine, aid, "yyslin1024", 0.05)
        _ann(in_memory_engine, aid, "vvgosick", vv)
    d = vic_credibility(in_memory_engine_session(in_memory_engine))
    assert d["signals"]["variance"]["insufficient"] is False
    assert d["signals"]["variance"]["suspect"] is False
    assert d["signals"]["extreme_consensus"]["pass"] is True
    assert d["status"] == "trusted"


def test_vic_credibility_suspect_on_straight_lining(in_memory_engine):
    # Vic 每檔每維都 0.5（整排同值）→ variance suspect → 整體 suspect
    for i in range(5):
        aid = _audio(in_memory_engine, f"U{i}_X.wav")
        _ann(in_memory_engine, aid, "yyslin1024", 0.05)
        _ann(in_memory_engine, aid, "vvgosick", 0.5)
    d = vic_credibility(in_memory_engine_session(in_memory_engine))
    assert d["signals"]["variance"]["suspect"] is True
    assert d["status"] == "suspect"


def test_vic_credibility_insufficient_when_no_audience(in_memory_engine):
    aid = _audio(in_memory_engine, "N_X.wav")
    _ann(in_memory_engine, aid, "yyslin1024", 0.05)  # 只有 industry
    d = vic_credibility(in_memory_engine_session(in_memory_engine))
    assert d["status"] == "insufficient"


# ── Dual-View 匯出帶 audience_credibility ──────────────────────────────────

def test_dual_view_embeds_audience_credibility(in_memory_engine):
    aid = _audio(in_memory_engine, "DV_X.wav")
    _ann(in_memory_engine, aid, "yyslin1024", 0.5)
    with Session(in_memory_engine) as s:
        data = build_dual_view(s)
    assert "audience_credibility" in data
    assert "status" in data["audience_credibility"]
    assert "statement" in data["audience_credibility"]


# ── helper：拿一個 live session（這些純函式吃 Session 物件）──────────────────

def in_memory_engine_session(engine):
    return Session(engine)
