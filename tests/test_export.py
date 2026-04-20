"""Phase 4 export aggregation 測試。

覆蓋 prompt 列出的 5 個必要 case + 3 個保險 case：
    1. single annotator → consensus_method == "single_annotator"
    2. 兩人連續維度 mean → 正確
    3. function_roles union
    4. source_type 平手 → null + warning
    5. is_complete=False 被排除
    6. loop_capability 三方各一票 → 0.5（離散 mode tie fallback）
    7. 空 DB → items=[]，不 crash
    8. individual.json 未知 annotator → 404（涵蓋 route 層）

fixture 模仿 tests/test_annotations_api.py：直接用 in-memory Session 寫 row，
function_roles / style_tag 用 json.dumps 存（對齊 POST /api/annotations 的真實格式）。
"""
from __future__ import annotations

import json

from sqlmodel import Session

from src.export import build_dataset
from src.models import Annotation, AudioFile


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_COMPLETE_DIMS = {
    "valence": 0.7,
    "arousal": 0.6,
    "emotional_warmth": 0.5,
    "tension_direction": 0.3,
    "temporal_position": 0.25,
    "event_significance": 0.6,
    "loop_capability": 1.0,
    "tonal_noise_ratio": 0.8,
    "spectral_density": 0.5,
    "world_immersion": 0.7,
}


def _make_audio(session: Session, filename: str = "Foo_Base Game.wav") -> AudioFile:
    audio = AudioFile(
        filename=filename,
        game_name=filename.split("_")[0],
        game_stage=filename.split("_")[1].removesuffix(".wav"),
    )
    session.add(audio)
    session.commit()
    session.refresh(audio)
    return audio


def _make_annotation(
    session: Session,
    audio_id: str,
    annotator_id: str,
    *,
    is_complete: bool = True,
    dims: dict | None = None,
    source_type: str = "ambience",
    function_roles: list | None = None,
    style_tag: list | None = None,
    genre_tag: str | None = "博弈",
    worldview_tag: str | None = "asian_mythology",
    notes: str | None = None,
) -> Annotation:
    d = {**_COMPLETE_DIMS, **(dims or {})}
    ann = Annotation(
        audio_file_id=audio_id,
        annotator_id=annotator_id,
        source_type=source_type,
        function_roles=json.dumps(function_roles or ["atmosphere"]),
        style_tag=json.dumps(style_tag or ["chinese_traditional"]),
        genre_tag=genre_tag,
        worldview_tag=worldview_tag,
        notes=notes,
        is_complete=is_complete,
        **d,
    )
    session.add(ann)
    session.commit()
    session.refresh(ann)
    return ann


# ---------------------------------------------------------------------------
# 1. single annotator
# ---------------------------------------------------------------------------

def test_single_annotator_uses_single_annotator_method(in_memory_engine):
    with Session(in_memory_engine) as s:
        audio = _make_audio(s)
        _make_annotation(s, audio.id, "amber", dims={"valence": 0.77})
        data = build_dataset(s)

    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["consensus_method"] == "single_annotator"
    # single annotator 情境：dimensions 就是該人的值（單筆 mean == 自己，round 到 3）
    assert item["consensus"]["dimensions"]["valence"] == 0.77
    assert item["consensus"]["source_type"] == "ambience"
    assert data["total_annotated"] == 1
    assert data["total_annotations"] == 1
    assert data["annotators"] == ["amber"]


# ---------------------------------------------------------------------------
# 2. 兩人連續 mean
# ---------------------------------------------------------------------------

def test_two_annotators_mean_continuous_dimensions(in_memory_engine):
    with Session(in_memory_engine) as s:
        audio = _make_audio(s)
        _make_annotation(s, audio.id, "amber", dims={"valence": 0.6, "arousal": 0.4})
        _make_annotation(s, audio.id, "bob",   dims={"valence": 0.8, "arousal": 0.6})
        data = build_dataset(s)

    item = data["items"][0]
    assert item["consensus_method"] == "mixed"
    assert item["consensus"]["dimensions"]["valence"] == 0.7
    assert item["consensus"]["dimensions"]["arousal"] == 0.5
    assert sorted(ann["annotator_id"] for ann in item["individual_annotations"]) == ["amber", "bob"]


# ---------------------------------------------------------------------------
# 3. function_roles union
# ---------------------------------------------------------------------------

def test_function_roles_union(in_memory_engine):
    with Session(in_memory_engine) as s:
        audio = _make_audio(s)
        _make_annotation(s, audio.id, "amber", function_roles=["atmosphere", "musical_sfx"])
        _make_annotation(s, audio.id, "bob",   function_roles=["atmosphere", "ui"])
        data = build_dataset(s)

    roles = data["items"][0]["consensus"]["function_roles"]
    assert set(roles) == {"atmosphere", "musical_sfx", "ui"}
    assert len(roles) == 3  # dedupe 生效


# ---------------------------------------------------------------------------
# 4. source_type 平手
# ---------------------------------------------------------------------------

def test_source_type_tie_returns_null_with_warning(in_memory_engine):
    with Session(in_memory_engine) as s:
        audio = _make_audio(s)
        _make_annotation(s, audio.id, "amber", source_type="synthetic_designed")
        _make_annotation(s, audio.id, "bob",   source_type="ambience")
        data = build_dataset(s)

    item = data["items"][0]
    assert item["consensus"]["source_type"] is None
    assert "warnings" in item
    assert "source_type_conflict" in item["warnings"]


# ---------------------------------------------------------------------------
# 5. is_complete=False 被排除
# ---------------------------------------------------------------------------

def test_incomplete_annotation_excluded(in_memory_engine):
    with Session(in_memory_engine) as s:
        audio_a = _make_audio(s, "A_Base Game.wav")
        audio_b = _make_audio(s, "B_Base Game.wav")
        # audio_a：一人完成、一人 draft → 只算完成那人
        _make_annotation(s, audio_a.id, "amber", is_complete=True, dims={"valence": 0.5})
        _make_annotation(s, audio_a.id, "bob",   is_complete=False, dims={"valence": 0.9})
        # audio_b：全員 draft → 整檔被排除
        _make_annotation(s, audio_b.id, "amber", is_complete=False)
        data = build_dataset(s)

    # audio_b 不在 items 裡
    filenames = [it["audio_file"] for it in data["items"]]
    assert "A_Base Game.wav" in filenames
    assert "B_Base Game.wav" not in filenames

    # audio_a 的 consensus 只有 amber 的值，沒被 bob 的 0.9 影響
    item_a = next(it for it in data["items"] if it["audio_file"] == "A_Base Game.wav")
    assert item_a["consensus"]["dimensions"]["valence"] == 0.5
    assert item_a["consensus_method"] == "single_annotator"
    assert len(item_a["individual_annotations"]) == 1

    # 分母計數：2 個音檔、1 個有完整標註、只有 amber 的 1 筆完成 annotation
    assert data["total_audio_files"] == 2
    assert data["total_annotated"] == 1
    assert data["total_annotations"] == 1


# ---------------------------------------------------------------------------
# 6. loop_capability 三方平手 → 0.5
# ---------------------------------------------------------------------------

def test_loop_capability_three_way_tie_returns_middle(in_memory_engine):
    with Session(in_memory_engine) as s:
        audio = _make_audio(s)
        _make_annotation(s, audio.id, "amber", dims={"loop_capability": 0.0})
        _make_annotation(s, audio.id, "bob",   dims={"loop_capability": 0.5})
        _make_annotation(s, audio.id, "carol", dims={"loop_capability": 1.0})
        data = build_dataset(s)

    assert data["items"][0]["consensus"]["dimensions"]["loop_capability"] == 0.5


# ---------------------------------------------------------------------------
# 7. 空 DB → items=[]
# ---------------------------------------------------------------------------

def test_empty_database_does_not_crash(in_memory_engine):
    with Session(in_memory_engine) as s:
        data = build_dataset(s)

    assert data["items"] == []
    assert data["total_audio_files"] == 0
    assert data["total_annotated"] == 0
    assert data["total_annotations"] == 0
    assert data["annotators"] == []
    # schema_version / dimension_schema 仍在，買方 parser 不會炸
    assert data["schema_version"] == "0.1.0"
    assert "valence" in data["dimension_schema"]


# ---------------------------------------------------------------------------
# 8. individual.json 未知 annotator → 404（route 層）
# ---------------------------------------------------------------------------

def test_individual_endpoint_unknown_annotator_returns_404(client, in_memory_engine):
    # DB 空，任何 annotator 都應該 404（不要回 200 帶空 items）
    r = client.get("/api/export/individual.json", params={"annotator": "ghost"})
    assert r.status_code == 404


def test_individual_endpoint_existing_annotator_with_no_completed_returns_404(
    client, in_memory_engine,
):
    """存在該 annotator，但他所有 annotation 都是 draft → 也該 404。"""
    with Session(in_memory_engine) as s:
        audio = _make_audio(s)
        _make_annotation(s, audio.id, "amber", is_complete=False)

    r = client.get("/api/export/individual.json", params={"annotator": "amber"})
    assert r.status_code == 404


def test_individual_endpoint_existing_annotator_with_completed_returns_200(
    client, in_memory_engine,
):
    with Session(in_memory_engine) as s:
        audio = _make_audio(s)
        _make_annotation(s, audio.id, "amber", is_complete=True)

    r = client.get("/api/export/individual.json", params={"annotator": "amber"})
    assert r.status_code == 200
    data = r.json()
    assert data["annotators"] == ["amber"]
    assert len(data["items"]) == 1


# ---------------------------------------------------------------------------
# 附加：dataset endpoint 的 smoke test（驗 JSON 路徑能打通 serialization）
# ---------------------------------------------------------------------------

def test_dataset_endpoint_returns_valid_envelope(client, in_memory_engine):
    with Session(in_memory_engine) as s:
        audio = _make_audio(s)
        _make_annotation(s, audio.id, "amber")

    r = client.get("/api/export/dataset.json")
    assert r.status_code == 200
    data = r.json()
    assert data["schema_version"] == "0.1.0"
    assert data["total_annotated"] == 1
    # function_roles 在 HTTP body 裡應該是真的 array，不是 escape string
    roles = data["items"][0]["consensus"]["function_roles"]
    assert isinstance(roles, list)
    assert roles == ["atmosphere"]


def test_calibration_endpoint_only_contains_amber(client, in_memory_engine):
    with Session(in_memory_engine) as s:
        audio_a = _make_audio(s, "A_Base Game.wav")
        audio_b = _make_audio(s, "B_Base Game.wav")
        _make_annotation(s, audio_a.id, "amber")
        _make_annotation(s, audio_b.id, "bob")

    r = client.get("/api/export/calibration_set.json")
    assert r.status_code == 200
    data = r.json()
    assert data["annotators"] == ["amber"]
    # bob 的那檔整個不進 items
    filenames = [it["audio_file"] for it in data["items"]]
    assert filenames == ["A_Base Game.wav"]
