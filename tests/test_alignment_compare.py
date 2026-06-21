"""BGM 對齊比對引擎測試。

驗「一個引擎、四種比對」的兩種讀法：
- 比對 1/2/4 看「差距」（compare_pair）
- 比對 3 看「變異」（compute_variance）
以及「一次只變一個軸」的守門（differing_axes / pair_comparison.valid）。
"""
from src.alignment_compare import (
    BGM_DIMENSIONS,
    Reading,
    SetIdentity,
    ReadingSet,
    group_into_sets,
    differing_axes,
    compare_pair,
    compute_variance,
    pair_comparison,
)


def _reading(**kw) -> Reading:
    base = dict(
        session_id="s1",
        annotator_id="eng1",
        annotator_role="engineer",
        audio_id="refA",
        audio_role="ref",
        version=0,
        dimension="valence",
        value=0.5,
        reading_type="perceived",
    )
    base.update(kw)
    return Reading(**base)


# ── grouping ────────────────────────────────────────────────────────────
def test_group_into_sets_collapses_dimensions_into_one_set():
    readings = [
        _reading(dimension="valence", value=0.9),
        _reading(dimension="emotional_warmth", value=0.25),
    ]
    sets = group_into_sets(readings)
    assert len(sets) == 1
    assert sets[0].values == {"valence": 0.9, "emotional_warmth": 0.25}


def test_group_into_sets_separates_by_identity():
    readings = [
        _reading(annotator_role="engineer", annotator_id="eng1", value=0.9),
        _reading(annotator_role="client", annotator_id="cli1", value=0.4),
    ]
    sets = group_into_sets(readings)
    assert len(sets) == 2


# ── differing_axes (the "one axis only" rule) ──────────────────────────────
def _ident(**kw) -> SetIdentity:
    base = dict(
        session_id="s1", annotator_id="eng1", annotator_role="engineer",
        audio_id="refA", audio_role="ref", version=0, reading_type="perceived",
    )
    base.update(kw)
    return SetIdentity(**base)


def test_comparison1_engineer_vs_client_is_single_who_axis():
    a = _ident(annotator_role="engineer", annotator_id="eng1")
    b = _ident(annotator_role="client", annotator_id="cli1")
    assert differing_axes(a, b) == ["who"]


def test_comparison2_version_is_single_axis():
    a = _ident(audio_role="deliverable", version=1)
    b = _ident(audio_role="deliverable", version=2)
    assert differing_axes(a, b) == ["version"]


def test_comparison4_reading_type_is_single_axis():
    a = _ident(annotator_role="client", annotator_id="cli1", reading_type="perceived")
    b = _ident(annotator_role="client", annotator_id="cli1", reading_type="target")
    assert differing_axes(a, b) == ["reading_type"]


def test_multiple_axes_changing_is_flagged():
    # 音效師 ref perceived  vs  客戶 ref target  → who + reading_type 兩軸全變
    a = _ident(annotator_role="engineer", annotator_id="eng1", reading_type="perceived")
    b = _ident(annotator_role="client", annotator_id="cli1", reading_type="target")
    axes = differing_axes(a, b)
    assert set(axes) == {"who", "reading_type"}


def test_identical_identity_has_no_differing_axis():
    assert differing_axes(_ident(), _ident()) == []


def _r(level_id, audio_id, dim, val):
    return Reading(
        session_id="s1", annotator_id="amber", annotator_role="client",
        audio_id=audio_id, audio_role="ref", version=0,
        dimension=dim, value=val, reading_type="perceived", level_id=level_id,
    )


def test_differing_axes_flags_level_mismatch():
    a = group_into_sets([_r("L1", "refA", "valence", 0.9)])[0]
    b = group_into_sets([_r("L2", "refA", "valence", 0.9)])[0]
    assert "level" in differing_axes(a.identity, b.identity)


# ── compare_pair (差距) ────────────────────────────────────────────────────
def test_compare_pair_per_dimension_abs_diff():
    a = ReadingSet(_ident(), {"valence": 0.9, "emotional_warmth": 0.25})
    b = ReadingSet(_ident(annotator_role="client", annotator_id="c1"),
                   {"valence": 0.85, "emotional_warmth": 0.80})
    diffs = compare_pair(a, b)
    assert round(diffs["valence"], 2) == 0.05
    assert round(diffs["emotional_warmth"], 2) == 0.55


def test_compare_pair_only_dimensions_in_both():
    a = ReadingSet(_ident(), {"valence": 0.9})
    b = ReadingSet(_ident(), {"valence": 0.8, "tension_direction": 0.6})
    assert list(compare_pair(a, b).keys()) == ["valence"]


# ── pair_comparison orchestrator ──────────────────────────────────────────
def test_pair_comparison_valid_single_axis():
    a = ReadingSet(_ident(annotator_role="engineer", annotator_id="e1"),
                   {"valence": 0.9})
    b = ReadingSet(_ident(annotator_role="client", annotator_id="c1"),
                   {"valence": 0.4})
    res = pair_comparison(a, b)
    assert res.valid is True
    assert res.differing_axes == ["who"]
    assert res.diffs["valence"] == 0.5


def test_pair_comparison_invalid_when_multi_axis():
    a = ReadingSet(_ident(annotator_role="engineer", annotator_id="e1",
                          reading_type="perceived"), {"valence": 0.9})
    b = ReadingSet(_ident(annotator_role="client", annotator_id="c1",
                          reading_type="target"), {"valence": 0.4})
    res = pair_comparison(a, b)
    assert res.valid is False           # 仍回 diffs，但標 invalid 讓 UI 警示
    assert res.diffs["valence"] == 0.5


# ── compute_variance (比對 3：穩定=保留項 / 飄動=可自由發揮) ────────────────
def test_compute_variance_spread_across_refs():
    # spec 兩首 ref 落點：valence 一致(保留)、柔烈度最大分歧
    s1 = ReadingSet(_ident(audio_id="refA"),
                    {"valence": 0.9, "emotional_warmth": 0.25})
    s2 = ReadingSet(_ident(audio_id="refB"),
                    {"valence": 0.85, "emotional_warmth": 0.80})
    spread = compute_variance([s1, s2])
    assert round(spread["valence"], 2) == 0.05          # 穩定 → 保留項
    assert round(spread["emotional_warmth"], 2) == 0.55  # 飄動 → 可自由發揮


def test_compute_variance_single_set_is_zero():
    s1 = ReadingSet(_ident(), {"valence": 0.9})
    assert compute_variance([s1]) == {"valence": 0.0}


def test_bgm_dimensions_are_the_four_feel_sliders():
    assert BGM_DIMENSIONS == (
        "valence", "tension_direction", "emotional_warmth", "world_immersion",
    )
