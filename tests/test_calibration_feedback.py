"""Phase 9 — 校準 feedback 計算 + report 邏輯測試。

關鍵覆蓋:
- distance_category 三色門檻(0.15 / 0.30)
- compute_calibration_feedback 對齊維度 + 缺值處理
- build_calibration_report MAE / Pearson / signed offset / verdict
- reference 自己跑 report 不該回 feedback(is_reference: true)
"""
from __future__ import annotations

import json

import pytest
from sqlmodel import Session

from src.calibration_feedback import (
    GREEN_THRESHOLD,
    YELLOW_THRESHOLD,
    build_calibration_report,
    compute_calibration_feedback,
    distance_category,
)
from src.models import Annotation, AudioFile


# ---------------------------------------------------------------------------
# distance_category — 連續維度色彩門檻
# ---------------------------------------------------------------------------

def test_distance_category_green_exact_zero():
    assert distance_category(0.5, 0.5) == "green"


def test_distance_category_green_at_threshold():
    """剛好 0.15 該回 green(包含邊界)。"""
    assert distance_category(0.5, 0.5 + GREEN_THRESHOLD) == "green"
    assert distance_category(0.5, 0.5 - GREEN_THRESHOLD) == "green"


def test_distance_category_yellow():
    assert distance_category(0.5, 0.5 + 0.2) == "yellow"
    assert distance_category(0.5, 0.5 - 0.25) == "yellow"


def test_distance_category_yellow_at_threshold():
    assert distance_category(0.5, 0.5 + YELLOW_THRESHOLD) == "yellow"


def test_distance_category_red():
    assert distance_category(0.1, 0.9) == "red"
    assert distance_category(0.0, 0.5) == "red"


# ---------------------------------------------------------------------------
# compute_calibration_feedback
# ---------------------------------------------------------------------------

def _make_annotation_obj(audio_id: str, annotator_id: str, **dim_values) -> Annotation:
    """fixture helper:不寫 DB,只造 Annotation 物件給 compute_calibration_feedback 用。"""
    defaults = {
        "valence": 0.5, "arousal": 0.5, "emotional_warmth": 0.5,
        "tension_direction": 0.5, "temporal_position": 0.5,
        "event_significance": 0.5, "world_immersion": 0.5,
    }
    defaults.update(dim_values)
    return Annotation(
        audio_file_id=audio_id,
        annotator_id=annotator_id,
        is_complete=True,
        loop_capability=json.dumps([1.0]),
        source_type=json.dumps(["ambience"]),
        function_roles=json.dumps(["atmosphere"]),
        genre_tag=json.dumps([]),
        style_tag=json.dumps([]),
        **defaults,
    )


def test_feedback_all_seven_dims_present():
    my = _make_annotation_obj("a1", "vvgosick")
    ref = _make_annotation_obj("a1", "amber")
    feedback = compute_calibration_feedback(my, ref)
    expected_dims = {
        "valence", "arousal", "emotional_warmth", "tension_direction",
        "temporal_position", "event_significance", "world_immersion",
    }
    assert set(feedback.keys()) == expected_dims
    for color in feedback.values():
        assert color in {"green", "yellow", "red"}


def test_feedback_acoustic_dims_excluded():
    """tonal_noise_ratio / spectral_density 不在 feedback 內(Phase 7 拿掉)。"""
    my = _make_annotation_obj("a1", "vvgosick")
    ref = _make_annotation_obj("a1", "amber")
    feedback = compute_calibration_feedback(my, ref)
    assert "tonal_noise_ratio" not in feedback
    assert "spectral_density" not in feedback


def test_feedback_skips_dim_when_either_side_none():
    my = _make_annotation_obj("a1", "vvgosick", valence=None)
    ref = _make_annotation_obj("a1", "amber")
    feedback = compute_calibration_feedback(my, ref)
    assert "valence" not in feedback
    # 其他維度仍正常
    assert "arousal" in feedback


def test_feedback_colors_match_thresholds():
    my = _make_annotation_obj("a1", "vvgosick",
        valence=0.5, arousal=0.5, emotional_warmth=0.5,
    )
    ref = _make_annotation_obj("a1", "amber",
        valence=0.5,         # delta 0 → green
        arousal=0.7,         # delta 0.2 → yellow
        emotional_warmth=0.9,  # delta 0.4 → red
    )
    feedback = compute_calibration_feedback(my, ref)
    assert feedback["valence"] == "green"
    assert feedback["arousal"] == "yellow"
    assert feedback["emotional_warmth"] == "red"


# ---------------------------------------------------------------------------
# build_calibration_report — DB 整合
# ---------------------------------------------------------------------------

def _save_audio(engine, filename: str) -> str:
    with Session(engine) as s:
        a = AudioFile(
            filename=filename,
            game_name=filename.split("_")[0],
            game_stage=filename.split("_")[1].removesuffix(".wav"),
        )
        s.add(a); s.commit(); s.refresh(a)
        return a.id


def _save_annotation(engine, audio_id: str, annotator: str, **dims):
    with Session(engine) as s:
        ann = _make_annotation_obj(audio_id, annotator, **dims)
        s.add(ann)
        s.commit()


def test_report_reference_returns_is_reference_true(in_memory_engine):
    with Session(in_memory_engine) as s:
        report = build_calibration_report(s, "amber")
    assert report["is_reference"] is True
    assert report["dimensions"] == {}


def test_report_no_overlap_returns_empty_dimensions(in_memory_engine):
    """vvgosick 還沒標任何 calibration audio → dimensions 都 sample_size=0。"""
    aid = _save_audio(in_memory_engine, "A_Base Game.wav")
    _save_annotation(in_memory_engine, aid, "amber")
    with Session(in_memory_engine) as s:
        report = build_calibration_report(s, "vvgosick")
    assert report["is_reference"] is False
    assert report["total_overlap"] == 0
    assert report["reference_total"] == 1
    assert report["dimensions"] == {}


def test_report_with_overlap_computes_mae_and_pearson(in_memory_engine):
    """3 筆共標 → MAE / Pearson / signed offset 都該有值。"""
    # 3 個 audio,amber 全標 0.5,vvgosick 全標 0.7 → MAE=0.2 (yellow), offset=+0.2
    audios = [_save_audio(in_memory_engine, f"A{i}_Base Game.wav") for i in range(3)]
    for aid in audios:
        _save_annotation(in_memory_engine, aid, "amber", valence=0.5)
        _save_annotation(in_memory_engine, aid, "vvgosick", valence=0.7)

    with Session(in_memory_engine) as s:
        report = build_calibration_report(s, "vvgosick")

    assert report["total_overlap"] == 3
    v = report["dimensions"]["valence"]
    assert v["sample_size"] == 3
    assert v["mae"] == 0.2
    assert v["mean_signed_offset"] == 0.2  # vvgosick 偏高
    # Pearson 在 ref 全是 0.5 時 = nan(分母 0)→ 應回 None
    assert v["pearson_r"] is None
    assert v["verdict"] == "yellow"


def test_report_pearson_correlates_when_variance_present(in_memory_engine):
    """3 筆 ref 值有變異 → Pearson 該算得出來。"""
    audios = [_save_audio(in_memory_engine, f"P{i}_Base Game.wav") for i in range(3)]
    ref_vals  = [0.2, 0.5, 0.8]
    my_vals   = [0.3, 0.5, 0.7]  # 跟 ref 同方向、略小差距
    for aid, rv, mv in zip(audios, ref_vals, my_vals):
        _save_annotation(in_memory_engine, aid, "amber", valence=rv)
        _save_annotation(in_memory_engine, aid, "vvgosick", valence=mv)

    with Session(in_memory_engine) as s:
        report = build_calibration_report(s, "vvgosick")

    v = report["dimensions"]["valence"]
    assert v["pearson_r"] is not None
    assert v["pearson_r"] > 0.95, f"高度相關該接近 1,實得 {v['pearson_r']}"
    assert v["verdict"] == "green"


def test_report_completed_calibration_flag(in_memory_engine):
    """vvgosick 把 amber 全部標過的 audio 都標完 → completed_calibration=True。"""
    audios = [_save_audio(in_memory_engine, f"C{i}_Base Game.wav") for i in range(2)]
    for aid in audios:
        _save_annotation(in_memory_engine, aid, "amber")
        _save_annotation(in_memory_engine, aid, "vvgosick")

    with Session(in_memory_engine) as s:
        report = build_calibration_report(s, "vvgosick")

    assert report["completed_calibration"] is True
    assert report["total_overlap"] == 2
    assert report["reference_total"] == 2
