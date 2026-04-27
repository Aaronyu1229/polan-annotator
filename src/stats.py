"""進度統計 + Phase 3 ICC / overlap pure functions — 給 /api/stats/* endpoint 共用。

設計原則：
- 所有公開函式不直接碰 FastAPI Request / Response，只收 Session 與原始 args。
- `current_streak_days` 的「日界」依賴使用者時區（由前端帶 ?tz=Asia/Taipei），
  不用 UTC — 避免台北凌晨標註被算成前一天。
- `avg_duration_sec` 排除 >= 2 小時的 outlier（Amber 標到一半去吃飯的情境）。
- ICC 用 intersection design — 只計算「全部 K 個 annotator 都 is_complete-標過」的
  audio_id 子集。multi_discrete 維度（loop_capability）跳過，待後續 per-option Cohen's Kappa。
- fixture_ 前綴 annotator 預設排除（dashboard 顯示真實資料）；?include_fixture=true 才納入。
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


# ─── Phase 3: ICC + overlap ───────────────────────────────────────────────

# ICC 門檻：emotion + function 類為主觀，0.7；acoustic 類為客觀，0.85
_ICC_THRESHOLD_BY_CATEGORY: dict[str, float] = {
    "emotion": 0.7,
    "function": 0.7,
    "acoustic": 0.85,
}

FIXTURE_ANNOTATOR_PREFIX = "fixture_"


def list_completed_annotators(
    session: Session,
    *,
    include_fixture: bool = False,
) -> list[str]:
    """回 distinct annotator_id list（有 is_complete=True 紀錄者）。"""
    rows = session.exec(
        select(Annotation.annotator_id)
        .where(Annotation.is_complete == True)  # noqa: E712
        .distinct()
    ).all()
    annotators = sorted({r for r in rows if r})
    if not include_fixture:
        annotators = [a for a in annotators if not a.startswith(FIXTURE_ANNOTATOR_PREFIX)]
    return annotators


def find_overlap_audios(
    session: Session,
    annotators: list[str],
) -> list[str]:
    """回每位 annotator 都 is_complete-標過的 audio_file_id list（intersection，sorted）。

    annotators 少於 2 → 空 list。
    """
    if len(annotators) < 2:
        return []
    audio_sets: list[set[str]] = []
    for ann_id in annotators:
        rows = session.exec(
            select(Annotation.audio_file_id).where(
                Annotation.annotator_id == ann_id,
                Annotation.is_complete == True,  # noqa: E712
            )
        ).all()
        audio_sets.append(set(rows))
    if not audio_sets:
        return []
    common = audio_sets[0]
    for s in audio_sets[1:]:
        common = common & s
    return sorted(common)


def compute_icc_per_dimension(
    session: Session,
    *,
    include_fixture: bool = False,
) -> dict[str, Any]:
    """跨標註員 ICC(2,1) per continuous dimension。

    流程：
    1. 列 distinct annotator（is_complete=True 的）
    2. 找 audio_file_id 同時被「全部 K 個 annotator」標過的 intersection
    3. 對每個連續維度建 (N x K) matrix → icc_2_1
    4. 依 dimension category 套門檻（emotion/function 0.7、acoustic 0.85）

    multi_discrete 維度（loop_capability）skip — 列入 skipped_dimensions。
    """
    import numpy as np  # noqa: PLC0415 — 延遲載入避免 import 順序問題

    from src.dimensions_loader import load_dimensions
    from src.statistics import icc_2_1

    annotators = list_completed_annotators(session, include_fixture=include_fixture)
    overlap_ids = find_overlap_audios(session, annotators)
    n = len(overlap_ids)
    k = len(annotators)

    dims_config = load_dimensions()
    eligible: list[tuple[str, str]] = []
    skipped: list[dict[str, str]] = []
    for dim_key, spec in dims_config.items():
        if spec.get("type") == "continuous":
            eligible.append((dim_key, spec["category"]))
        else:
            skipped.append(
                {
                    "key": dim_key,
                    "type": spec.get("type", "unknown"),
                    "reason": "multi_discrete 維度，ICC 不適用（待後續 per-option Cohen's Kappa）",
                }
            )

    dimensions_result: dict[str, Any] = {}

    if k < 2 or n < 2:
        for dim_key, category in eligible:
            threshold = _ICC_THRESHOLD_BY_CATEGORY.get(category, 0.7)
            dimensions_result[dim_key] = {
                "icc": None,
                "category": category,
                "threshold": threshold,
                "pass": None,
                "note": "尚無足夠重疊資料（需 ≥ 2 位標註員各自完整標記 ≥ 2 個共同檔案）",
            }
        return {
            "annotators": annotators,
            "sample_size": n,
            "include_fixture": include_fixture,
            "dimensions": dimensions_result,
            "skipped_dimensions": skipped,
        }

    # 一次撈所有需要的 annotation
    rows = session.exec(
        select(Annotation).where(
            Annotation.audio_file_id.in_(overlap_ids),  # type: ignore[attr-defined]
            Annotation.annotator_id.in_(annotators),  # type: ignore[attr-defined]
            Annotation.is_complete == True,  # noqa: E712
        )
    ).all()
    by_pair: dict[tuple[str, str], Annotation] = {
        (r.audio_file_id, r.annotator_id): r for r in rows
    }

    for dim_key, category in eligible:
        threshold = _ICC_THRESHOLD_BY_CATEGORY.get(category, 0.7)
        matrix = np.full((n, k), np.nan, dtype=float)
        complete = True
        for i, audio_id in enumerate(overlap_ids):
            for j, ann_id in enumerate(annotators):
                ann = by_pair.get((audio_id, ann_id))
                value = getattr(ann, dim_key, None) if ann else None
                if value is None:
                    complete = False
                    break
                matrix[i, j] = float(value)
            if not complete:
                break

        if not complete:
            dimensions_result[dim_key] = {
                "icc": None,
                "category": category,
                "threshold": threshold,
                "pass": None,
                "note": "intersection 內某筆 annotation 此維度為 None",
            }
            continue

        icc = icc_2_1(matrix)
        dimensions_result[dim_key] = {
            "icc": round(icc, 3) if icc is not None else None,
            "category": category,
            "threshold": threshold,
            "pass": (icc >= threshold) if icc is not None else None,
            "note": None,
        }

    return {
        "annotators": annotators,
        "sample_size": n,
        "include_fixture": include_fixture,
        "dimensions": dimensions_result,
        "skipped_dimensions": skipped,
    }


def compute_overlap_audios(
    session: Session,
    *,
    include_fixture: bool = False,
) -> list[dict[str, Any]]:
    """列被 ≥ 2 位 annotator is_complete-標過的 audio。

    回 [{audio_file_id, filename, game_name, game_stage, annotators}]，
    依 (game_name, game_stage) 排序。
    """
    annotators = list_completed_annotators(session, include_fixture=include_fixture)
    if len(annotators) < 2:
        return []

    rows = session.exec(
        select(Annotation.audio_file_id, Annotation.annotator_id).where(
            Annotation.annotator_id.in_(annotators),  # type: ignore[attr-defined]
            Annotation.is_complete == True,  # noqa: E712
        )
    ).all()
    by_audio: dict[str, set[str]] = {}
    for audio_id, ann_id in rows:
        by_audio.setdefault(audio_id, set()).add(ann_id)

    overlap_ids = [aid for aid, anns in by_audio.items() if len(anns) >= 2]
    if not overlap_ids:
        return []

    audios = session.exec(
        select(AudioFile)
        .where(AudioFile.id.in_(overlap_ids))  # type: ignore[attr-defined]
        .order_by(AudioFile.game_name, AudioFile.game_stage)
    ).all()
    return [
        {
            "audio_file_id": a.id,
            "filename": a.filename,
            "game_name": a.game_name,
            "game_stage": a.game_stage,
            "annotators": sorted(by_audio[a.id]),
        }
        for a in audios
    ]
