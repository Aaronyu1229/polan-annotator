"""進度統計 pure functions — 給 /api/stats/progress 與未來的 endpoint 共用。

設計原則：
- 所有公開函式不直接碰 FastAPI Request / Response，只收 Session 與原始 args。
- `current_streak_days` 的「日界」依賴使用者時區（由前端帶 ?tz=Asia/Taipei），
  不用 UTC — 避免台北凌晨標註被算成前一天。
- `avg_duration_sec` 排除 >= 2 小時的 outlier（Amber 標到一半去吃飯的情境）。
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlmodel import Session, select

from src.models import Annotation, AudioFile

log = logging.getLogger("polan.stats")

# 單筆 annotation 超過這個秒數視為 outlier（標到一半去做別的事），不算進平均
DURATION_OUTLIER_THRESHOLD_SEC: int = 7200  # 2 hours


@dataclass(frozen=True)
class ProgressStats:
    """單一 annotator 的進度快照。has_data=False 時數值欄位全 None。"""
    annotator_id: str
    total_audio_files: int
    completed_count: int
    completion_rate: float
    avg_duration_sec: Optional[float]
    estimated_remaining_sec: Optional[float]
    current_streak_days: Optional[int]
    has_data: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _resolve_tz(tz_name: Optional[str]) -> tzinfo_like:
    """解析 IANA TZ 名稱；不合法或 None 都 fallback UTC。"""
    if tz_name is None or tz_name == "":
        return UTC
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        log.warning("無效時區 '%s'，fallback 到 UTC", tz_name)
        return UTC
    except Exception as e:  # noqa: BLE001 — zoneinfo 可能丟多種
        log.warning("解析時區 '%s' 失敗 (%s)，fallback 到 UTC", tz_name, e)
        return UTC


# tzinfo 型別 alias — Python stdlib 無官方公開 alias，借用 datetime.timezone 的父類
tzinfo_like = timezone  # type: ignore[misc]


def _to_utc_aware(dt: datetime) -> datetime:
    """SQLite 會把 tz-aware 寫入轉成 naive 讀回；naive 一律視為 UTC。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _compute_avg_duration(annotations: list[Annotation]) -> Optional[float]:
    """`updated_at - created_at` 的秒數平均，排除 >= 2 小時 outlier。無有效樣本回 None。"""
    valid: list[float] = []
    for ann in annotations:
        if ann.created_at is None or ann.updated_at is None:
            continue
        created = _to_utc_aware(ann.created_at)
        updated = _to_utc_aware(ann.updated_at)
        delta = (updated - created).total_seconds()
        if delta < 0:
            continue
        if delta >= DURATION_OUTLIER_THRESHOLD_SEC:
            continue
        valid.append(delta)
    if not valid:
        return None
    return sum(valid) / len(valid)


def _compute_streak_days(
    created_ats: list[datetime],
    tz,
    today: Optional[date] = None,
) -> int:
    """計算連續標註天數。`today` 可注入以利測試。

    規則：
    - 取每個 created_at 轉到 tz 後的 date 集合（去重）
    - 從最新日期往回數連續天
    - 最新日期若 < 昨天（tz 下）→ streak 已斷，回 0
    """
    if not created_ats:
        return 0

    dates: set[date] = set()
    for dt in created_ats:
        local = _to_utc_aware(dt).astimezone(tz)
        dates.add(local.date())

    if today is None:
        today = datetime.now(tz).date()

    sorted_desc = sorted(dates, reverse=True)
    latest = sorted_desc[0]
    yesterday = today - timedelta(days=1)
    if latest < yesterday:
        return 0

    streak = 1
    prev = latest
    for d in sorted_desc[1:]:
        if d == prev - timedelta(days=1):
            streak += 1
            prev = d
        else:
            break
    return streak


def compute_progress(
    session: Session,
    annotator_id: str,
    tz_name: Optional[str] = None,
    today: Optional[date] = None,
) -> ProgressStats:
    """聚合給定 annotator 的進度快照。

    Args:
        session: DB session
        annotator_id: 要統計的 annotator
        tz_name: 前端帶來的 IANA 時區；無效或 None 會 fallback UTC
        today: 測試注入用；prod 從 `datetime.now(tz).date()` 算

    Returns:
        ProgressStats — completed_count=0 時 has_data=False、所有計算欄位 None
    """
    total_audio_files = session.exec(select(AudioFile)).all()
    total_count = len(total_audio_files)

    completed = session.exec(
        select(Annotation).where(
            Annotation.annotator_id == annotator_id,
            Annotation.is_complete == True,  # noqa: E712
        )
    ).all()
    completed_count = len(completed)

    if completed_count == 0:
        return ProgressStats(
            annotator_id=annotator_id,
            total_audio_files=total_count,
            completed_count=0,
            completion_rate=0.0,
            avg_duration_sec=None,
            estimated_remaining_sec=None,
            current_streak_days=None,
            has_data=False,
        )

    tz = _resolve_tz(tz_name)
    avg = _compute_avg_duration(completed)
    est_remaining: Optional[float] = None
    if avg is not None:
        est_remaining = (total_count - completed_count) * avg
    streak = _compute_streak_days(
        [c.created_at for c in completed], tz, today=today,
    )
    rate = completed_count / total_count if total_count > 0 else 0.0

    return ProgressStats(
        annotator_id=annotator_id,
        total_audio_files=total_count,
        completed_count=completed_count,
        completion_rate=rate,
        avg_duration_sec=avg,
        estimated_remaining_sec=est_remaining,
        current_streak_days=streak,
        has_data=True,
    )
