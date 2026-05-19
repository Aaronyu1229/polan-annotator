"""src/annotation_serialization.annotation_to_dict 單元測試。"""
from __future__ import annotations

from datetime import UTC, datetime

from src.annotation_serialization import annotation_to_dict
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
    assert d["worldview_tag"] == "fantasy"
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


def test_none_timestamps_serialize_to_none():
    ann = Annotation(audio_file_id="a", annotator_id="x")
    ann.created_at = None
    ann.updated_at = None
    d = annotation_to_dict(ann)
    assert d["created_at"] is None
    assert d["updated_at"] is None
