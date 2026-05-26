"""src/annotation_serialization.annotation_to_dict 單元測試。"""
from __future__ import annotations

from datetime import UTC, datetime

from src.annotation_serialization import annotation_to_dict, decode_worldview_tag
from src.models import Annotation


def test_full_annotation_serializes_all_fields():
    ann = Annotation(
        id="ann1",
        audio_file_id="aud1",
        annotator_id="yyslin1024",
        valence=0.7,
        arousal=0.5,
        emotional_warmth=0.6,
        tension_direction=0.4,
        temporal_position=0.5,
        event_significance=0.3,
        world_immersion=0.55,
        tonal_noise_ratio=0.8,
        spectral_density=0.6,
        loop_capability='[1.0]',
        source_type='["bgm"]',
        function_roles='["atmosphere", "tension"]',
        genre_tag='["epic"]',
        worldview_tag="fantasy",
        style_tag='["orchestral"]',
        notes="some note",
        is_complete=True,
        created_at=datetime(2026, 5, 12, 3, 11, tzinfo=UTC),
        updated_at=datetime(2026, 5, 12, 3, 14, tzinfo=UTC),
    )
    d = annotation_to_dict(ann)
    assert d["annotation_id"] == "ann1"
    assert d["annotator_id"] == "yyslin1024"
    assert d["valence"] == 0.7
    assert d["world_immersion"] == 0.55
    assert d["loop_capability"] == [1.0]
    assert d["source_type"] == ["bgm"]
    assert d["function_roles"] == ["atmosphere", "tension"]
    assert d["genre_tag"] == ["epic"]
    assert d["style_tag"] == ["orchestral"]
    # worldview_tag 舊資料存原始字串 → 向後相容解碼成單元素 list
    assert d["worldview_tag"] == ["fantasy"]
    assert d["notes"] == "some note"
    assert d["is_complete"] is True
    assert d["created_at"] == "2026-05-12T03:11:00+00:00"
    assert d["updated_at"] == "2026-05-12T03:14:00+00:00"


def test_bad_json_list_field_becomes_empty_list():
    ann = Annotation(
        audio_file_id="a", annotator_id="x",
        source_type="not-json{", function_roles=None, style_tag="",
    )
    d = annotation_to_dict(ann)
    assert d["source_type"] == []
    assert d["function_roles"] == []
    assert d["style_tag"] == []


def test_non_list_json_becomes_empty_list():
    ann = Annotation(
        audio_file_id="a", annotator_id="x",
        genre_tag='{"k": "v"}',  # valid JSON but not a list
    )
    assert annotation_to_dict(ann)["genre_tag"] == []


def test_decode_worldview_tag_legacy_scalar_becomes_single_item_list():
    # 舊資料：原始字串（非 JSON）→ 保留成單元素 list（不可丟掉）
    assert decode_worldview_tag("fantasy") == ["fantasy"]


def test_decode_worldview_tag_new_json_list_passthrough():
    assert decode_worldview_tag('["fantasy", "casino"]') == ["fantasy", "casino"]


def test_decode_worldview_tag_empty_json_list_stays_empty():
    # 新格式的空 list 不可被誤判為「decode 失敗」而 wrap 成 ['[]']
    assert decode_worldview_tag("[]") == []


def test_decode_worldview_tag_none_and_blank_become_empty():
    assert decode_worldview_tag(None) == []
    assert decode_worldview_tag("") == []


def test_decode_worldview_tag_non_list_json_treated_as_scalar():
    # 合法 JSON 但非 list（如數字）→ 視為單一舊值，回原始字串
    assert decode_worldview_tag("123") == ["123"]


def test_none_timestamps_serialize_to_none():
    ann = Annotation(audio_file_id="a", annotator_id="x")
    ann.created_at = None
    ann.updated_at = None
    d = annotation_to_dict(ann)
    assert d["created_at"] is None
    assert d["updated_at"] is None
