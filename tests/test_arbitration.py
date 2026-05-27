from __future__ import annotations

from sqlmodel import Session, select

from src.models import Arbitration


def test_arbitration_row_roundtrip(in_memory_engine):
    with Session(in_memory_engine) as s:
        s.add(Arbitration(
            audio_file_id="a1", field="valence", arbitrated_value="0.7",
            value_type="float", path="fast", arbitrated_by="amber",
        ))
        s.commit()
        row = s.exec(select(Arbitration).where(Arbitration.audio_file_id == "a1")).one()
        assert row.field == "valence"
        assert row.path == "fast"
        assert row.notes is None
        assert row.arbitrated_at is not None


def test_arbitration_history_multiple_rows_same_audio_field(in_memory_engine):
    # 同 (audio, field) 允許多筆（re-arbitration 歷史保留）
    with Session(in_memory_engine) as s:
        s.add(Arbitration(audio_file_id="a1", field="valence", arbitrated_value="0.5",
                          value_type="float", path="fast", arbitrated_by="amber"))
        s.add(Arbitration(audio_file_id="a1", field="valence", arbitrated_value="0.6",
                          value_type="float", path="full", notes="改判", arbitrated_by="amber"))
        s.commit()
        rows = s.exec(select(Arbitration).where(Arbitration.field == "valence")).all()
        assert len(rows) == 2


def test_annotation_snapshot_append_only_multiple_passes(in_memory_engine):
    from src.models import AnnotationSnapshot
    with Session(in_memory_engine) as s:
        s.add(AnnotationSnapshot(audio_file_id="a1", annotator_id="amber",
                                 pass_no=1, valence=0.5))
        s.add(AnnotationSnapshot(audio_file_id="a1", annotator_id="amber",
                                 pass_no=2, valence=0.55))
        s.commit()
        rows = s.exec(
            select(AnnotationSnapshot).where(AnnotationSnapshot.annotator_id == "amber")
        ).all()
        assert {r.pass_no for r in rows} == {1, 2}


def test_serialize_roundtrip_float_and_list():
    from src.arbitration import serialize_value, deserialize_value
    v, t = serialize_value("valence", 0.7)
    assert (v, t) == ("0.7", "float")
    assert deserialize_value(v, t) == 0.7
    v, t = serialize_value("genre_tag", ["博弈", "RPG"])
    assert t == "list_str"
    assert deserialize_value(v, t) == ["博弈", "RPG"]
    v, t = serialize_value("loop_capability", [0.5, 1.0])
    assert t == "list_float"
    assert deserialize_value(v, t) == [0.5, 1.0]


def test_arbitrated_fields_count():
    from src.arbitration import ARBITRATED_FIELDS
    # 7 連續維 + loop_capability + 5 tags = 13
    assert len(ARBITRATED_FIELDS) == 13
    assert "valence" in ARBITRATED_FIELDS
    assert "worldview_tag" in ARBITRATED_FIELDS
    assert "tonal_noise_ratio" not in ARBITRATED_FIELDS  # acoustic 不仲裁


def test_latest_by_audio_field_picks_newest(in_memory_engine):
    from datetime import datetime, UTC, timedelta
    from src.arbitration import latest_by_audio_field
    t0 = datetime(2026, 5, 1, tzinfo=UTC)
    rows = [
        Arbitration(audio_file_id="a", field="valence", arbitrated_value="0.5",
                    value_type="float", path="fast", arbitrated_by="amber", arbitrated_at=t0),
        Arbitration(audio_file_id="a", field="valence", arbitrated_value="0.6",
                    value_type="float", path="full", arbitrated_by="amber",
                    arbitrated_at=t0 + timedelta(days=1)),
    ]
    latest = latest_by_audio_field(rows)
    assert latest[("a", "valence")].arbitrated_value == "0.6"


def test_bulk_load_arbitrations_groups_by_audio(in_memory_engine):
    from src.arbitration import bulk_load_arbitrations_by_audio
    with Session(in_memory_engine) as s:
        s.add(Arbitration(audio_file_id="a", field="valence", arbitrated_value="0.5",
                          value_type="float", path="fast", arbitrated_by="amber"))
        s.add(Arbitration(audio_file_id="b", field="arousal", arbitrated_value="0.3",
                          value_type="float", path="fast", arbitrated_by="amber"))
        s.commit()
        by_audio = bulk_load_arbitrations_by_audio(s)
        assert set(by_audio.keys()) == {"a", "b"}
        assert by_audio["a"][0].field == "valence"
