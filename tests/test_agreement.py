"""Phase 8 — agreement 統計純函式。"""
from __future__ import annotations

import random

from src.agreement import bland_altman, ccc, icc_2_1


def _vals(n, seed=1):
    rng = random.Random(seed)
    return [rng.random() for _ in range(n)]


def test_ccc_identical_near_one():
    xs = _vals(40)
    r = ccc(xs, list(xs))
    assert r["insufficient"] is False
    assert r["value"] > 0.99
    assert r["ci_low"] > 0.9


def test_ccc_constant_offset_penalized():
    # 固定偏移：Pearson 仍 =1，但 CCC 因 accuracy 受罰 < 1
    xs = _vals(40)
    ys = [x + 0.3 for x in xs]
    r = ccc(xs, ys)
    assert r["value"] < 0.95  # 偏移拉低 concordance


def test_ccc_insufficient_below_min_n():
    assert ccc(_vals(10), _vals(10))["insufficient"] is True


def test_bland_altman_bias():
    rng = random.Random(7)
    xs = _vals(30)
    ys = [x - 0.2 + rng.uniform(-0.05, 0.05) for x in xs]  # 偏移 ~0.2 + 雜訊 → sd>0
    r = bland_altman(xs, ys)
    assert abs(r["mean_bias"] - 0.2) < 0.05
    assert r["loa_low"] < r["mean_bias"] < r["loa_high"]


def test_icc_identical_raters_near_one():
    matrix = [[v, v, v] for v in _vals(10)]
    r = icc_2_1(matrix)
    assert r["value"] > 0.99


def test_icc_insufficient():
    assert icc_2_1([[0.5, 0.6]])["insufficient"] is True  # 1 subject


# ─── compute_agreement_layers（DB） ───────────────────────────────

def test_layers_measures_creator_industry_not_audience(in_memory_engine):
    """回歸：industry_alignment 量 creator×industry（非 yyslin×Vic）。"""
    import json
    from sqlmodel import Session
    from src.agreement import compute_agreement_layers
    from src.models import Annotation, AudioFile
    dims = ["valence", "arousal", "emotional_warmth", "tension_direction",
            "temporal_position", "event_significance", "world_immersion"]
    with Session(in_memory_engine) as s:
        for i in range(3):
            a = AudioFile(filename=f"L{i}_x.wav", game_name="L", game_stage="Base")
            s.add(a); s.commit(); s.refresh(a)
            for who in ("amber", "yyslin1024", "vvgosick"):
                s.add(Annotation(
                    audio_file_id=a.id, annotator_id=who, is_complete=True,
                    source_type=json.dumps(["ambience"]),
                    **{d: 0.5 for d in dims},
                ))
            s.commit()
        layers = compute_agreement_layers(s)
    # 3 個 creator-industry pair（非 audience 對）
    assert layers["industry_alignment"]["valence"]["n"] == 3
    assert "overall_three_way" in layers
    assert layers["mimicry_residual"]["enabled"] is False


def test_agreement_endpoint(client, in_memory_engine):
    r = client.get("/api/admin/agreement")
    assert r.status_code == 200
    assert "industry_alignment" in r.json()
