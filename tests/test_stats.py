"""src/stats.py 單元測試。

涵蓋 spec 要求的 7 個 case：
1. 新 annotator（無資料） → has_data=False
2. 單筆標註 → avg/est/streak 正確
3. streak = 1（只有今天）
4. streak = 2（昨天+今天）
5. streak = 0（> 1 天沒標）
6. outlier（>= 2 小時）被排除出 avg
7. 兩個 annotator 各自統計不互相污染
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from sqlmodel import Session

from src.models import Annotation, AudioFile
from src.stats import compute_progress


def _add_audio(engine, filename: str = "foo.wav") -> str:
    """在 test DB 建一筆 AudioFile，回 id。"""
    with Session(engine) as s:
        a = AudioFile(filename=filename, game_name="X", game_stage="Y")
        s.add(a)
        s.commit()
        s.refresh(a)
        return a.id


def _add_annotation(
    engine,
    audio_id: str,
    annotator_id: str,
    *,
    is_complete: bool = True,
    created_at: datetime,
    updated_at: datetime | None = None,
) -> None:
    """插入一筆 annotation — created_at / updated_at 必須自己給（測時序）。"""
    if updated_at is None:
        updated_at = created_at + timedelta(minutes=5)
    with Session(engine) as s:
        ann = Annotation(
            audio_file_id=audio_id,
            annotator_id=annotator_id,
            is_complete=is_complete,
            created_at=created_at,
            updated_at=updated_at,
            function_roles='["atmosphere"]',
            style_tag="[]",
        )
        s.add(ann)
        s.commit()


def test_progress_no_annotations(in_memory_engine):
    # 新 annotator，DB 無任何 annotation
    for i in range(5):
        _add_audio(in_memory_engine, f"a{i}.wav")
    with Session(in_memory_engine) as s:
        result = compute_progress(s, "amber", tz_name="Asia/Taipei")
    assert result.has_data is False
    assert result.completed_count == 0
    assert result.completion_rate == 0.0
    assert result.avg_duration_sec is None
    assert result.estimated_remaining_sec is None
    assert result.current_streak_days is None
    assert result.total_audio_files == 5


def test_progress_single_annotation(in_memory_engine):
    # 5 個音檔，amber 標完 1 個，花了 7 分鐘
    audio_ids = [_add_audio(in_memory_engine, f"a{i}.wav") for i in range(5)]
    now = datetime(2026, 4, 20, 10, 0, tzinfo=UTC)
    _add_annotation(
        in_memory_engine, audio_ids[0], "amber",
        created_at=now, updated_at=now + timedelta(minutes=7),
    )
    with Session(in_memory_engine) as s:
        result = compute_progress(
            s, "amber", tz_name="UTC", today=date(2026, 4, 20),
        )
    assert result.has_data is True
    assert result.completed_count == 1
    assert result.total_audio_files == 5
    assert abs(result.completion_rate - 0.2) < 1e-6
    assert result.avg_duration_sec is not None
    assert abs(result.avg_duration_sec - 7 * 60) < 1  # ≈ 420 秒
    assert result.estimated_remaining_sec is not None
    assert abs(result.estimated_remaining_sec - 4 * 420) < 1  # 4 個剩餘 × 420 秒


def test_progress_streak_today_only(in_memory_engine):
    a = _add_audio(in_memory_engine)
    today_utc = datetime(2026, 4, 20, 10, 0, tzinfo=UTC)
    _add_annotation(in_memory_engine, a, "amber", created_at=today_utc)
    with Session(in_memory_engine) as s:
        result = compute_progress(
            s, "amber", tz_name="UTC", today=date(2026, 4, 20),
        )
    # 只今天標 → streak = 1（前端依此規則不加 🔥，那是 UI 的事；backend 回 1）
    assert result.current_streak_days == 1


def test_progress_streak_two_consecutive_days(in_memory_engine):
    a1 = _add_audio(in_memory_engine, "x.wav")
    a2 = _add_audio(in_memory_engine, "y.wav")
    yesterday = datetime(2026, 4, 19, 10, 0, tzinfo=UTC)
    today = datetime(2026, 4, 20, 10, 0, tzinfo=UTC)
    _add_annotation(in_memory_engine, a1, "amber", created_at=yesterday)
    _add_annotation(in_memory_engine, a2, "amber", created_at=today)
    with Session(in_memory_engine) as s:
        result = compute_progress(
            s, "amber", tz_name="UTC", today=date(2026, 4, 20),
        )
    assert result.current_streak_days == 2


def test_progress_streak_broken(in_memory_engine):
    # 3 天前標過；昨天今天都沒標 → streak = 0
    a = _add_audio(in_memory_engine)
    three_days_ago = datetime(2026, 4, 17, 10, 0, tzinfo=UTC)
    _add_annotation(in_memory_engine, a, "amber", created_at=three_days_ago)
    with Session(in_memory_engine) as s:
        result = compute_progress(
            s, "amber", tz_name="UTC", today=date(2026, 4, 20),
        )
    assert result.current_streak_days == 0


def test_progress_excludes_outlier_duration(in_memory_engine):
    # 正常 5 分鐘 + outlier 3 小時 → 平均應 = 5 分鐘（outlier 被排除）
    a1 = _add_audio(in_memory_engine, "a.wav")
    a2 = _add_audio(in_memory_engine, "b.wav")
    now = datetime(2026, 4, 20, 10, 0, tzinfo=UTC)
    _add_annotation(
        in_memory_engine, a1, "amber",
        created_at=now, updated_at=now + timedelta(minutes=5),
    )
    _add_annotation(
        in_memory_engine, a2, "amber",
        created_at=now, updated_at=now + timedelta(hours=3),
    )
    with Session(in_memory_engine) as s:
        result = compute_progress(
            s, "amber", tz_name="UTC", today=date(2026, 4, 20),
        )
    assert result.avg_duration_sec is not None
    assert abs(result.avg_duration_sec - 5 * 60) < 1  # ≈ 300 秒


def test_progress_different_annotators_isolated(in_memory_engine):
    # amber 與 bob 標同一個音檔，各自統計互不影響
    a = _add_audio(in_memory_engine, "shared.wav")
    _add_audio(in_memory_engine, "other.wav")  # 多一個讓 total_count=2
    now = datetime(2026, 4, 20, 10, 0, tzinfo=UTC)
    _add_annotation(
        in_memory_engine, a, "amber",
        created_at=now, updated_at=now + timedelta(minutes=3),
    )
    _add_annotation(
        in_memory_engine, a, "bob",
        created_at=now, updated_at=now + timedelta(minutes=10),
    )
    with Session(in_memory_engine) as s:
        amber = compute_progress(
            s, "amber", tz_name="UTC", today=date(2026, 4, 20),
        )
        bob = compute_progress(
            s, "bob", tz_name="UTC", today=date(2026, 4, 20),
        )
    assert amber.completed_count == 1
    assert bob.completed_count == 1
    assert amber.avg_duration_sec is not None and abs(amber.avg_duration_sec - 180) < 1
    assert bob.avg_duration_sec is not None and abs(bob.avg_duration_sec - 600) < 1
    # total_audio_files 共用
    assert amber.total_audio_files == bob.total_audio_files == 2
