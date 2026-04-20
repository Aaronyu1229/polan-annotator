"""audio_scanner：idempotent scan + parse 正確性。

測試用 in-memory SQLite engine 與 tmp_path 假音檔（0-byte .wav 也行，
scanner Phase 1 不讀檔內容）。
"""
from pathlib import Path

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from src.audio_scanner import ScanResult, scan_audio_directory
from src.models import AudioFile  # noqa: F401 — 註冊 metadata


@pytest.fixture
def session():
    """每個 test 一個乾淨的 in-memory SQLite。"""
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def audio_dir(tmp_path: Path) -> Path:
    """tmp_path/audio 裡放幾個代表性檔名，涵蓋兩段式與三段式品牌主題。"""
    d = tmp_path / "audio"
    d.mkdir()
    for name in (
        "Balloon Fiesta_Base Game.wav",
        "Balloon Fiesta_Free Game.wav",
        "Game Brand Theme Music_金鑫_AI Virtual Voice.wav",
    ):
        (d / name).touch()
    return d


def test_scan_empty_dir(session, tmp_path):
    empty = tmp_path / "audio"
    empty.mkdir()
    result = scan_audio_directory(session, audio_dir=empty)
    assert result == ScanResult(total_on_disk=0, added=[], skipped=[])


def test_scan_missing_dir_raises(session, tmp_path):
    with pytest.raises(FileNotFoundError, match="音檔目錄不存在"):
        scan_audio_directory(session, audio_dir=tmp_path / "nope")


def test_scan_inserts_new_files(session, audio_dir):
    result = scan_audio_directory(session, audio_dir=audio_dir)
    assert result.total_on_disk == 3
    assert len(result.added) == 3
    assert result.skipped == []
    rows = session.exec(select(AudioFile)).all()
    assert len(rows) == 3


def test_scan_is_idempotent(session, audio_dir):
    first = scan_audio_directory(session, audio_dir=audio_dir)
    second = scan_audio_directory(session, audio_dir=audio_dir)
    assert len(first.added) == 3
    assert second.added == []
    assert len(second.skipped) == 3
    rows = session.exec(select(AudioFile)).all()
    assert len(rows) == 3  # 沒有重複


def test_scan_applies_parser_to_brand_theme(session, audio_dir):
    scan_audio_directory(session, audio_dir=audio_dir)
    brand = session.exec(
        select(AudioFile).where(AudioFile.is_brand_theme == True)  # noqa: E712
    ).one()
    assert brand.game_name == "Game Brand Theme Music"
    assert brand.game_stage == "金鑫 (AI Virtual Voice)"


def test_scan_applies_parser_to_two_segment(session, audio_dir):
    scan_audio_directory(session, audio_dir=audio_dir)
    base = session.exec(
        select(AudioFile).where(AudioFile.filename == "Balloon Fiesta_Base Game.wav")
    ).one()
    assert base.game_name == "Balloon Fiesta"
    assert base.game_stage == "Base Game"
    assert base.is_brand_theme is False


def test_scan_adds_only_new_files(session, audio_dir):
    scan_audio_directory(session, audio_dir=audio_dir)
    # 新增第 4 個檔案，重掃
    (audio_dir / "Volcano Goddess_Base Game.wav").touch()
    result = scan_audio_directory(session, audio_dir=audio_dir)
    assert result.added == ["Volcano Goddess_Base Game.wav"]
    assert len(result.skipped) == 3
