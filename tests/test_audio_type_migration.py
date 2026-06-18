"""AudioFile.audio_type migration 測試。

驗 (1) 新列 default 'sfx'；(2) 既有缺欄的舊庫經 migration 補欄並回填 'sfx'；(3) idempotent。
"""
from sqlalchemy import text
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import src.models  # noqa: F401  註冊 metadata
from src.migrations import apply_pending_migrations
from src.models import AudioFile


def _engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(eng)
    return eng


def test_new_audiofile_defaults_to_sfx():
    eng = _engine()
    with Session(eng) as s:
        af = AudioFile(filename="x.wav", game_name="G", game_stage="Base Game")
        s.add(af)
        s.commit()
        s.refresh(af)
        assert af.audio_type == "sfx"


def test_migration_adds_and_backfills_legacy_rows():
    eng = _engine()
    # 模擬舊庫：拿掉 audio_type，塞一筆無該欄的舊資料
    with Session(eng) as s:
        s.exec(text("ALTER TABLE audiofile DROP COLUMN audio_type"))  # type: ignore[arg-type]
        s.exec(text(
            "INSERT INTO audiofile (id, filename, game_name, game_stage, is_brand_theme, is_gold_locked, discovered_at) "
            "VALUES ('a1', 'old.wav', 'G', 'Base Game', 0, 0, '2026-01-01 00:00:00')"
        ))  # type: ignore[arg-type]
        s.commit()

    applied = apply_pending_migrations(eng)
    assert "audiofile.audio_type" in applied

    with Session(eng) as s:
        row = s.exec(text("SELECT audio_type FROM audiofile WHERE id='a1'")).one()  # type: ignore[arg-type]
        assert row[0] == "sfx"  # 既有列回填


def test_migration_is_idempotent():
    eng = _engine()  # create_all 已含 audio_type → migration 應 skip
    assert "audiofile.audio_type" not in apply_pending_migrations(eng)
    assert apply_pending_migrations(eng) == []
