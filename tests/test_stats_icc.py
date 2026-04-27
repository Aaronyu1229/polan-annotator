"""Phase 3 ICC + overlap pure function 測試。

覆蓋：
- list_completed_annotators 排除 fixture_ 前綴 / draft 不算
- find_overlap_audios intersection 邏輯
- compute_icc_per_dimension：完美一致 / 不夠 annotator / 多選維度 skip / fixture 排除
- compute_overlap_audios：≥2 人標 / fixture 排除
"""
from __future__ import annotations

import json

from sqlmodel import Session

from src.models import Annotation, AudioFile
from src.stats import (
    compute_icc_per_dimension,
    compute_overlap_audios,
    find_overlap_audios,
    list_completed_annotators,
)


_DEFAULT_DIMS = {
    "valence": 0.5,
    "arousal": 0.5,
    "emotional_warmth": 0.5,
    "tension_direction": 0.5,
    "temporal_position": 0.5,
    "event_significance": 0.5,
    "tonal_noise_ratio": 0.5,
    "spectral_density": 0.5,
    "world_immersion": 0.5,
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
    s: Session,
    audio_id: str,
    annotator_id: str,
    *,
    dims: dict | None = None,
    is_complete: bool = True,
) -> Annotation:
    payload = {**_DEFAULT_DIMS, **(dims or {})}
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
        **payload,
    )
    s.add(ann)
    s.commit()
    s.refresh(ann)
    return ann


# ─── list_completed_annotators ─────────────────────────────

def test_list_completed_annotators_empty_db(in_memory_engine):
    with Session(in_memory_engine) as s:
        assert list_completed_annotators(s) == []


def test_list_completed_annotators_excludes_drafts(in_memory_engine):
    with Session(in_memory_engine) as s:
        a1 = _make_audio(s, "A_Base Game.wav")
        _make_ann(s, a1.id, "amber", is_complete=True)
        _make_ann(s, a1.id, "bob", is_complete=False)  # draft 不算
        assert list_completed_annotators(s) == ["amber"]


def test_list_completed_annotators_excludes_fixture_by_default(in_memory_engine):
    with Session(in_memory_engine) as s:
        a1 = _make_audio(s, "A_Base Game.wav")
        _make_ann(s, a1.id, "amber")
        _make_ann(s, a1.id, "fixture_bob")
        assert list_completed_annotators(s) == ["amber"]
        assert list_completed_annotators(s, include_fixture=True) == ["amber", "fixture_bob"]


# ─── find_overlap_audios ──────────────────────────────────

def test_find_overlap_returns_empty_for_single_annotator(in_memory_engine):
    with Session(in_memory_engine) as s:
        a1 = _make_audio(s, "A_Base Game.wav")
        _make_ann(s, a1.id, "amber")
        assert find_overlap_audios(s, ["amber"]) == []


def test_find_overlap_returns_intersection(in_memory_engine):
    with Session(in_memory_engine) as s:
        a1 = _make_audio(s, "A_Base Game.wav")
        a2 = _make_audio(s, "B_Base Game.wav")
        a3 = _make_audio(s, "C_Base Game.wav")
        # amber 標 a1, a2, a3；bob 只標 a1, a2 → 交集 = {a1, a2}
        for ann_id in ["amber"]:
            for a in [a1, a2, a3]:
                _make_ann(s, a.id, ann_id)
        for a in [a1, a2]:
            _make_ann(s, a.id, "bob")
        overlap = find_overlap_audios(s, ["amber", "bob"])
        assert sorted(overlap) == sorted([a1.id, a2.id])


# ─── compute_icc_per_dimension ────────────────────────────

def test_icc_no_overlap_returns_empty_message(in_memory_engine):
    """單一 annotator → 每維度 icc=None + note 提示。"""
    with Session(in_memory_engine) as s:
        a1 = _make_audio(s, "A_Base Game.wav")
        _make_ann(s, a1.id, "amber")
        result = compute_icc_per_dimension(s)
    assert result["sample_size"] == 0
    assert result["annotators"] == ["amber"]
    for dim_key, dim_data in result["dimensions"].items():
        assert dim_data["icc"] is None
        assert dim_data["pass"] is None
        assert "尚無" in dim_data["note"]


def test_icc_perfect_agreement_two_annotators(in_memory_engine):
    """兩 annotator 給完全相同分數 → 每連續維度 ICC ~ 1.0、pass=True。"""
    with Session(in_memory_engine) as s:
        audios = [_make_audio(s, f"{name}_Base Game.wav") for name in "ABCDE"]
        # 建 5 個 audio，amber/bob 給每個都同樣 dim 值（每個 audio 不同值才有 between-subject variance）
        for i, audio in enumerate(audios):
            v = 0.1 + 0.2 * i  # 0.1, 0.3, 0.5, 0.7, 0.9
            dims = {k: v for k in _DEFAULT_DIMS}
            _make_ann(s, audio.id, "amber", dims=dims)
            _make_ann(s, audio.id, "bob", dims=dims)
        result = compute_icc_per_dimension(s)

    assert result["sample_size"] == 5
    assert result["annotators"] == ["amber", "bob"]
    for dim_key, dim_data in result["dimensions"].items():
        assert dim_data["icc"] is not None
        assert dim_data["icc"] > 0.99, f"{dim_key}: icc={dim_data['icc']}"
        assert dim_data["pass"] is True


def test_icc_skips_loop_capability_as_multi_discrete(in_memory_engine):
    """loop_capability (multi_discrete) 不在 dimensions 結果內，列在 skipped_dimensions。"""
    with Session(in_memory_engine) as s:
        a1 = _make_audio(s, "A_Base Game.wav")
        _make_ann(s, a1.id, "amber")
        result = compute_icc_per_dimension(s)
    assert "loop_capability" not in result["dimensions"]
    skipped_keys = [d["key"] for d in result["skipped_dimensions"]]
    assert "loop_capability" in skipped_keys


def test_icc_excludes_fixture_by_default(in_memory_engine):
    with Session(in_memory_engine) as s:
        a1 = _make_audio(s, "A_Base Game.wav")
        a2 = _make_audio(s, "B_Base Game.wav")
        for a in [a1, a2]:
            _make_ann(s, a.id, "amber")
            _make_ann(s, a.id, "fixture_bob")
        result_default = compute_icc_per_dimension(s)
        result_fixture = compute_icc_per_dimension(s, include_fixture=True)
    assert result_default["annotators"] == ["amber"]
    assert result_default["sample_size"] == 0  # 排掉 fixture 後只剩 amber，無 overlap
    assert "fixture_bob" in result_fixture["annotators"]
    assert result_fixture["sample_size"] == 2


def test_icc_threshold_emotion_07_acoustic_085(in_memory_engine):
    """emotion + function 類門檻 0.7、acoustic 類門檻 0.85。"""
    with Session(in_memory_engine) as s:
        a1 = _make_audio(s, "A_Base Game.wav")
        _make_ann(s, a1.id, "amber")
        result = compute_icc_per_dimension(s)
    # valence 是 emotion → threshold 0.7
    assert result["dimensions"]["valence"]["threshold"] == 0.7
    # tonal_noise_ratio 是 acoustic → 0.85
    assert result["dimensions"]["tonal_noise_ratio"]["threshold"] == 0.85
    # temporal_position 是 function → 0.7
    assert result["dimensions"]["temporal_position"]["threshold"] == 0.7


# ─── compute_overlap_audios ───────────────────────────────

def test_overlap_returns_audios_standard_by_2plus(in_memory_engine):
    with Session(in_memory_engine) as s:
        a1 = _make_audio(s, "A_Base Game.wav")
        a2 = _make_audio(s, "B_Base Game.wav")
        # a1 兩人標、a2 只 amber 標
        _make_ann(s, a1.id, "amber")
        _make_ann(s, a1.id, "bob")
        _make_ann(s, a2.id, "amber")
        result = compute_overlap_audios(s)
    assert len(result) == 1
    assert result[0]["audio_file_id"] == a1.id
    assert result[0]["annotators"] == ["amber", "bob"]


def test_overlap_excludes_fixture_by_default(in_memory_engine):
    with Session(in_memory_engine) as s:
        a1 = _make_audio(s, "A_Base Game.wav")
        _make_ann(s, a1.id, "amber")
        _make_ann(s, a1.id, "fixture_alice")
        # default: amber 一人 → 無 overlap
        assert compute_overlap_audios(s) == []
        # include_fixture=True 才看到
        result = compute_overlap_audios(s, include_fixture=True)
        assert len(result) == 1
        assert result[0]["annotators"] == ["amber", "fixture_alice"]
