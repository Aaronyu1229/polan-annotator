"""Phase 10 — AudioFile 狀態機與資料集匯出 quality 過濾邏輯。

狀態(衍生,不存 DB):
    untouched       無任何 is_complete annotation
    draft           恰 1 位 annotator is_complete 完成
    cross_annotated ≥ 2 位 annotator is_complete 完成
    lockable        ≥ 2 人標 + 每維 max-min spread ≤ GOLD_MAX_SPREAD(品質達標,等 Amber 鎖)
    gold            is_gold_locked=True(Amber 人工認證可商用)

設計理由:
- 5 個狀態,gold 由 1 個 bool 欄位持久化,其他 4 個程式即時算
- 「reconciled」中間態被拿掉 — Amber 看完直接 lock = gold,沒有「看過但不滿意」的中間態
- per-file 用 max-min spread(不是 ICC,因為 ICC 是 set-level 指標)

匯出過濾(min_status):
    gold            ≥ gold
    lockable        ≥ lockable(含 gold)
    cross_annotated ≥ cross(含 lockable / gold)
    draft           ≥ draft(含 cross / lockable / gold)
    untouched       全部(包含 untouched)
"""
from __future__ import annotations

from typing import Any, Optional

from sqlmodel import Session, select

from src.models import Annotation, AudioFile

# Phase 9 對 calibration feedback 用 0.15/0.30。這裡的 gold spread threshold 比較寬容(0.20),
# 因為:
# (a) cross_annotated 包含 guest 等多視角,自然會比校準時的單對單寬
# (b) max-min spread 比 mean delta 嚴格(多人時更易超門檻),保守一點較合理
GOLD_MAX_SPREAD = 0.20

# 7 個 human 連續維度 — 跟 calibration_feedback.HUMAN_CONTINUOUS_DIMS 對齊
# acoustic 2 維 librosa 算的(deterministic),不參與 spread 判斷
HUMAN_CONTINUOUS_DIMS = (
    "valence",
    "arousal",
    "emotional_warmth",
    "tension_direction",
    "temporal_position",
    "event_significance",
    "world_immersion",
)

# 狀態排序(用於 min_status 比對)
_STATUS_ORDER = {
    "untouched": 0,
    "draft": 1,
    "cross_annotated": 2,
    "lockable": 3,
    "gold": 4,
}


def status_meets(actual: str, minimum: str) -> bool:
    """actual 是否 ≥ minimum(用於 export filter)。"""
    return _STATUS_ORDER.get(actual, -1) >= _STATUS_ORDER.get(minimum, -1)


def per_dim_spread(
    annotations: list[Annotation],
) -> dict[str, Optional[float]]:
    """計算每個 human 連續維度的 max-min spread。

    None 值跳過(annotator 沒標該維度)。少於 2 個有效值 → None(沒法算 spread)。
    """
    result: dict[str, Optional[float]] = {}
    for dim in HUMAN_CONTINUOUS_DIMS:
        values = [
            getattr(a, dim) for a in annotations
            if getattr(a, dim, None) is not None
        ]
        if len(values) < 2:
            result[dim] = None
            continue
        result[dim] = float(max(values) - min(values))
    return result


def compute_audiofile_status(
    audio: AudioFile,
    session: Session,
) -> str:
    """衍生狀態。從 audio + DB 即時算,不快取。

    順序:
    1. is_gold_locked → gold
    2. 0 完成 → untouched
    3. 1 完成 → draft
    4. ≥ 2 完成 + spread 全達標 → lockable
    5. ≥ 2 完成 但 spread 有任一超門檻 → cross_annotated
    """
    if audio.is_gold_locked:
        return "gold"

    completed = session.exec(
        select(Annotation).where(
            Annotation.audio_file_id == audio.id,
            Annotation.is_complete == True,  # noqa: E712
        )
    ).all()

    n = len(completed)
    if n == 0:
        return "untouched"
    if n == 1:
        return "draft"

    # n >= 2: 看 spread
    spreads = per_dim_spread(completed)
    has_any_exceeds = any(
        s is not None and s > GOLD_MAX_SPREAD for s in spreads.values()
    )
    return "cross_annotated" if has_any_exceeds else "lockable"


def gold_lock_prerequisites(
    audio: AudioFile,
    session: Session,
) -> dict[str, Any]:
    """檢查 audio 能不能鎖 gold,回 (eligible, reasons, details)。

    給 lock_gold API 用 — 不符 prereq 時回 409 帶 reasons 給 Amber 看。
    """
    if audio.is_gold_locked:
        return {
            "eligible": False,
            "reasons": ["此音檔已 gold-locked"],
            "current_status": "gold",
        }

    completed = session.exec(
        select(Annotation).where(
            Annotation.audio_file_id == audio.id,
            Annotation.is_complete == True,  # noqa: E712
        )
    ).all()
    n = len(completed)
    reasons: list[str] = []
    if n < 2:
        reasons.append(f"至少需 2 位 annotator is_complete 完成,目前 {n} 位")

    spreads = per_dim_spread(completed) if n >= 2 else {}
    exceeding = {
        dim: s for dim, s in spreads.items()
        if s is not None and s > GOLD_MAX_SPREAD
    }
    if exceeding:
        details = ", ".join(f"{dim}={s:.2f}" for dim, s in exceeding.items())
        reasons.append(
            f"以下維度 max-min spread 超過 {GOLD_MAX_SPREAD}:{details}"
        )

    return {
        "eligible": not reasons,
        "reasons": reasons,
        "n_complete_annotators": n,
        "max_spread_per_dim": spreads,
        "spread_threshold": GOLD_MAX_SPREAD,
    }


def bulk_load_annotations_by_audio(
    session: Session,
) -> dict[str, list[Annotation]]:
    """Phase 13-A:一次撈所有 is_complete annotation,分組 by audio_id。

    給需要對全部 audio 算 status 的 endpoint 避免 N+1 query。
    """
    rows = session.exec(
        select(Annotation).where(Annotation.is_complete == True)  # noqa: E712
    ).all()
    by_audio: dict[str, list[Annotation]] = {}
    for r in rows:
        by_audio.setdefault(r.audio_file_id, []).append(r)
    return by_audio


def compute_status_from_preload(
    audio: AudioFile,
    annotations: list[Annotation],
) -> str:
    """compute_audiofile_status 的 pure-function 版本 — 用預載 annotations,不查 DB。

    Phase 13-A:給 bulk endpoint 避免 N+1。語意跟 compute_audiofile_status 完全一致。
    """
    if audio.is_gold_locked:
        return "gold"
    n = len({a.annotator_id for a in annotations})
    if n == 0:
        return "untouched"
    if n == 1:
        return "draft"
    spreads = per_dim_spread(annotations)
    has_any_exceeds = any(
        s is not None and s > GOLD_MAX_SPREAD for s in spreads.values()
    )
    return "cross_annotated" if has_any_exceeds else "lockable"


def status_summary(session: Session) -> dict[str, Any]:
    """Dashboard 5 卡用 — 一次回所有 audio 的 status 分布。

    Phase 13-A:bulk pre-load 把 1312 queries 降到 2 queries(audiofile + annotation)。
    """
    audios = session.exec(select(AudioFile)).all()
    by_audio = bulk_load_annotations_by_audio(session)
    counts = {k: 0 for k in _STATUS_ORDER}
    for a in audios:
        st = compute_status_from_preload(a, by_audio.get(a.id, []))
        counts[st] = counts.get(st, 0) + 1
    counts["total"] = len(audios)
    return counts
