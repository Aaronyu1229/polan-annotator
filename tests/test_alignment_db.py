"""BGM 對齊資料庫測試。

重點驗「實體隔離」：alignment_reading 表絕不出現在 annotations.db 用的
SQLModel.metadata，反之主表也不出現在 alignment metadata。
"""
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session
from sqlmodel import SQLModel

import src.models  # noqa: F401  確保主 metadata 註冊
from src.alignment_db import (
    AlignmentAudio,
    AlignmentBase,
    AlignmentReading,
    ClientLink,
    apply_alignment_migrations,
    create_alignment_db,
    make_alignment_engine,
)


def test_alignment_table_not_in_main_metadata():
    # 若洩漏，db.create_db() 會把它建進 annotations.db
    assert "alignment_reading" not in SQLModel.metadata.tables
    assert "alignment_spec" not in SQLModel.metadata.tables


def test_main_tables_not_in_alignment_metadata():
    names = set(AlignmentBase.metadata.tables.keys())
    assert names == {"alignment_reading", "alignment_spec", "alignment_audio", "client_link"}
    assert "annotation" not in names
    assert "audiofile" not in names


def test_create_alignment_db_creates_only_alignment_tables(tmp_path):
    db = tmp_path / "alignment.db"
    eng = create_engine(f"sqlite:///{db}")
    create_alignment_db(eng)
    assert sorted(inspect(eng).get_table_names()) == ["alignment_audio", "alignment_reading", "alignment_spec", "client_link"]


def test_reading_round_trip(tmp_path):
    db = tmp_path / "alignment.db"
    eng = create_engine(f"sqlite:///{db}")
    create_alignment_db(eng)
    with Session(eng) as s:
        s.add(AlignmentReading(
            session_id="s1", annotator_id="cli1", annotator_role="client",
            audio_id="refA", audio_role="ref", version=0,
            dimension="valence", value=0.9, reading_type="perceived",
        ))
        s.commit()
    with Session(eng) as s:
        row = s.query(AlignmentReading).one()
        assert row.value == 0.9
        assert row.reading_type == "perceived"
        assert row.created_at is not None


def _column_names(eng, table):
    with eng.begin() as conn:
        rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").all()
    return {r[1] for r in rows}


def test_level_id_column_added_and_idempotent(tmp_path):
    db = tmp_path / "alignment.db"
    eng = make_alignment_engine(db)
    # 先建一個「沒有 level_id」的舊版表，模擬既有 alignment.db
    with eng.begin() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE alignment_reading (id INTEGER PRIMARY KEY, session_id VARCHAR)"
        )
        conn.exec_driver_sql(
            "CREATE TABLE alignment_spec (id INTEGER PRIMARY KEY, session_id VARCHAR)"
        )
    applied = apply_alignment_migrations(eng)
    assert "alignment_reading.level_id" in applied
    assert "alignment_spec.level_id" in applied
    assert "level_id" in _column_names(eng, "alignment_reading")
    assert "level_id" in _column_names(eng, "alignment_spec")
    # 再跑一次不應重複套用
    assert apply_alignment_migrations(eng) == []
